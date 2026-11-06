"""Export the current material test as an editable, standalone OpenSeesPy model."""
from __future__ import annotations

import ast
import math
from pathlib import Path
import re
import shlex

import numpy as np

from material_test.domain import ProjectConfig
from material_test.material_commands import parse_material_commands, validate_solver_materials
from material_test.modeling import compile_topology, defined_material_tags
from material_test.opensees.tcl_writer import _explicit_model_commands
from material_test.protocols import history_from_protocol


def _call(command: str) -> str:
    """Translate a literal model command, refusing unresolved Tcl expressions."""
    if re.search(r"[$\[\]{};\\]", command):
        raise ValueError("Python 导出需要显式材料参数，不支持 Tcl 变量、表达式或脚本：" + command)
    tokens = shlex.split(command, comments=True)
    arguments = []
    for token in tokens[1:]:
        if re.fullmatch(r"[+-]?\d+", token):
            value = int(token)
        else:
            try:
                value = float(token)
            except ValueError:
                value = token
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Python 模型参数必须为有限数值")
        arguments.append(repr(value))
    return f"ops.{tokens[0]}({', '.join(arguments)})"


def _comment(value) -> str:
    return " / ".join(str(value).splitlines())


def _name(value) -> str:
    """Keep user names; omit generated identifiers that repeat the node/element tag."""
    name = _comment(value).strip()
    return '' if re.fullmatch(r'[NE]0*\d+', name, re.IGNORECASE) else name


def _node_note(node, elements, control_node, control_dof):
    parts = [_name(node.get('name', ''))]
    connected = []
    for element in elements:
        for end in ('i', 'j'):
            if element[end] == node['tag']:
                label = f"E{element['tag']}.{end}"
                if element['type'] == 'zeroLength':
                    label += f'（零长度连接 {end} 侧）'
                connected.append(label)
    if connected:
        parts.append('连接 ' + '、'.join(connected))
    if node['tag'] == control_node:
        parts.append('加载控制 ' + ('UX', 'UY', 'RZ')[control_dof - 1])
    fixed = [name for name, flag in zip(('UX', 'UY', 'RZ'), node.get('fix', [0, 0, 0])) if flag]
    if fixed:
        parts.append('固定 ' + '/'.join(fixed))
    return f"# N{node['tag']}：" + '；'.join(part for part in parts if part)


def _element_note(element):
    kinds = {'elasticBeamColumn': '弹性梁柱段', 'truss': '轴向材料杆',
             'corotTruss': '共回转轴向材料杆', 'twoNodeLink': '双节点连接', 'zeroLength': '零长度连接'}
    parts = [_name(element.get('name', '')), kinds[element['type']], f"N{element['i']} → N{element['j']}"]
    if element['type'] in {'truss', 'corotTruss'}:
        parts.append(f"材料 Tag {element['material']}")
    elif element['type'] in {'twoNodeLink', 'zeroLength'}:
        parts.append('局部方向—材料：' + '、'.join(f"{direction}→Tag {tag}" for direction, tag in
                                               zip(element['directions'], element['materials'])))
    elif element.get('release'):
        parts.append('端部转动释放：' + {1: 'i 端', 2: 'j 端', 3: 'i/j 两端'}[element['release']])
    return f"# E{element['tag']}：" + '；'.join(part for part in parts if part)


def _material_lines(config, commands, responses, elements):
    """Name known material arguments without changing their order or mirrored values."""
    from material_test.io.material_library import load_material_library

    layouts, metadata = {}, {}
    for entry in load_material_library().entries:
        tokens = entry.command.split()
        names = [re.fullmatch(r'\{(\w+)\}', token) for token in tokens[3:]]
        if names and all(names):
            layouts[tokens[1]] = [match.group(1) for match in names]
        metadata[tokens[1]] = {parameter.name: (parameter.label, parameter.unit) for parameter in entry.parameters}
    hardening = [('a1', '压向等向硬化参数'), ('a2', '压向等向硬化尺度'),
                 ('a3', '拉向等向硬化参数'), ('a4', '拉向等向硬化尺度')]
    for kind in ('Steel01', 'Steel02'):
        layouts[kind] += [name for name, _ in hardening]
        metadata[kind].update({name: (label, 'dimensionless') for name, label in hardening})
    layouts['Steel02'].append('sigInit')
    metadata['Steel02']['sigInit'] = ('初始应力或初始广义力', 'model stress unit')
    layouts['Elastic'] += ['eta', 'Eneg']
    metadata['Elastic'].update({'eta': ('黏性系数', 'force/velocity'),
                                'Eneg': ('负向弹性刚度', 'force/deformation')})
    source_commands = {command.tag: command for command in parse_material_commands(config.material)}
    result, explained = [], set()
    for line in commands.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line.lstrip().startswith('uniaxialMaterial '):
            raise ValueError('Python 导出仅支持显式 uniaxialMaterial 命令：' + line)
        call = _call(line)
        arguments = [ast.literal_eval(arg) for arg in ast.parse(call).body[0].value.args]
        kind, tag, *values = arguments
        response = responses.get(tag, config.material_mode)
        dimension = {'stress_strain': '应力—应变', 'force_deformation': '力—位移', 'moment_rotation': '弯矩—转角'}[response]
        users = [f"E{e['tag']}" for e in elements if tag in ([e.get('material')] if e['type'] in {'truss', 'corotTruss'} else e.get('materials', []))]
        result.append(f"# 材料 Tag {tag}：{kind}；{dimension}" + ('；用于 ' + '、'.join(users) if users else '；子材料/组合引用'))
        names = layouts.get(kind, [])[:len(values)]
        if len(names) != len(values) or any(not isinstance(value, (int, float)) for value in values) or kind in {'Series', 'Parallel', 'MinMax'}:
            result.append(call)
            continue
        stress = (f'{config.force_unit}/{config.length_unit}²' if response == 'stress_strain' else
                  f'{config.force_unit}·{config.length_unit}' if response == 'moment_rotation' else config.force_unit)
        deformation = '应变（无量纲）' if response == 'stress_strain' else 'rad' if response == 'moment_rotation' else config.length_unit
        stiffness = stress if response == 'stress_strain' else f'{stress}/{deformation}'
        units = {'dimensionless': '无量纲', 'model stress unit': stress, 'force': stress,
                 'model strain unit': deformation, 'deformation': deformation, 'strain': deformation,
                 'force/deformation': stiffness, 'stress/strain': stiffness, 'force/velocity': f'{stiffness}·s'}
        variable = f'material_{tag}'
        source = source_commands.get(tag)
        source_args = shlex.split(source.source, comments=True)[3:] if source else []
        result.append(f'{variable} = {{')
        for index, (name, value) in enumerate(zip(names, values)):
            notes = []
            if (kind, response, name) not in explained:
                label, unit = metadata.get(kind, {}).get(name, (name, ''))
                if response != 'stress_strain':
                    if name == 'Fy':
                        label = '屈服弯矩' if response == 'moment_rotation' else '屈服力'
                    elif name in {'E', 'E0', 'Eneg'}:
                        label = ('负向' if name == 'Eneg' else '初始') + ('转动刚度' if response == 'moment_rotation' else '平动刚度')
                display_unit = stiffness if name in {'E', 'E0', 'Eneg'} else units.get(unit, unit)
                notes.append(label + (f' [{display_unit}]' if unit else ''))
                explained.add((kind, response, name))
            field = re.fullmatch(r'\{([^{}!:]+)(?:[!:][^{}]*)?\}', source_args[index]) if index < len(source_args) else None
            if field and field.group(1) != name:
                notes.append('对应项目参数 ' + field.group(1))
            suffix = '  # ' + '；'.join(notes) if notes else ''
            result.append(f"    {name!r}: {value!r},{suffix}")
        result.append('}')
        result.append(f'ops.uniaxialMaterial({kind!r}, {tag},')
        for offset in range(0, len(names), 3):
            result.append('    ' + ', '.join(f'{variable}[{name!r}]' for name in names[offset:offset+3]) + ',')
        result.append(')')
    return result


def write_python_model(config: ProjectConfig, path: Path) -> Path:
    """Freeze current inputs into one Python file; no saved result is required."""
    config.modeling.validate()
    validate_solver_materials(config.material)
    history = history_from_protocol(config.loading)
    explicit = config.modeling.assembly is not None
    transient = False
    dt = 1.0
    material_commands = config.material.render_command()
    responses, elements = {}, []
    node_lines, element_lines, boundary_lines, mass_lines = [], [], [], []
    if explicit:
        from material_test.explicit_assembly import component_from_inputs

        project = component_from_inputs(
            config.modeling.assembly, config.material, config.solver, history,
            config.output_dir, force_unit=config.force_unit, length_unit=config.length_unit,
        )
        data = project.data
        history = np.asarray(data['loading']['history'], dtype=float)
        material_commands = '\n'.join(m.render_command() for m in project.materials.values())
        control = data['loading']
        node, dof = control['node'], control['dof']
        elements = data['elements']
        responses = {item['tag']: item['response'] for item in data['materials']}
        transient = control.get('analysis', 'static') == 'transient'
        dt = float(control.get('dt', 1.0)) if transient else 1.0
        for n in data['nodes']:
            node_lines += [_node_note(n, elements, node, dof),
                           f"ops.node({n['tag']}, {float(n['x'])!r}, {float(n['y'])!r})"]
            boundary_lines.append(f"ops.fix({n['tag']}, " + ', '.join(map(str, n.get('fix', [0, 0, 0]))) + ')')
            if any(n.get('mass', [])):
                mass_lines.append(f"ops.mass({n['tag']}, " + ', '.join(repr(float(v)) for v in n['mass']) + ')')
        for e in data['elements']:
            element_lines.append(_element_note(e))
            head = f"element {e['type']} {e['tag']} {e['i']} {e['j']}"
            if e['type'] == 'elasticBeamColumn':
                element_lines.append(_call(f"geomTransf {e.get('transformation', 'Linear')} {e['tag']}"))
                command = head + f" {e['A']:.16g} {e['E']:.16g} {e['Iz']:.16g} {e['tag']}"
                if e.get('release'):
                    command += f" -release {e['release']}"
            elif e['type'] in {'truss', 'corotTruss'}:
                command = head + f" {e['A']:.16g} {e['material']}"
            else:
                x, y = e.get('axis', [1., 0.])
                command = head + ' -mat ' + ' '.join(map(str, e['materials']))
                command += ' -dir ' + ' '.join(map(str, e['directions']))
                command += f" -orient {x:.16g} {y:.16g} 0 {-y:.16g} {x:.16g} 0"
            element_lines.append(_call(command))
        for c in data.get('constraints', []):
            command = (f"rigidLink beam {c['retained']} {c['constrained']}" if c['type'] == 'rigidLink'
                       else f"equalDOF {c['retained']} {c['constrained']} " + ' '.join(map(str, c['dofs'])))
            boundary_lines.append(_call(command))
        slaves = {c['constrained'] for c in data.get('constraints', [])}
        free_dofs = sum(sum(1 - v for v in n.get('fix', [0, 0, 0]))
                        for n in data['nodes'] if n['tag'] not in slaves) - 1
        use_penalty = transient and (free_dofs == 0 or node in {c['retained'] for c in data.get('constraints', [])})
        handler = "ops.constraints('Penalty', 1.0e12, 1.0e12)" if use_penalty else "ops.constraints('Transformation')"
        test = ("ops.test('NormDispIncr', 1.0e-9, 80)" if transient else
                f"ops.test('NormUnbalance', {float(control.get('equilibrium_tolerance', 1e-6))!r}, 80)")
        force = f"ops.nodeReaction({node}, {dof})" if transient else 'ops.getLoadFactor(1)'
    else:
        root = config.modeling.resolved_root(config.material.tag)
        missing = set(root.material_tags()) - defined_material_tags(material_commands)
        if missing:
            raise ValueError('装配引用了未定义的材料 Tag：' + ', '.join(map(str, sorted(missing))))
        model, node, _ = _explicit_model_commands(
            topology=config.modeling.topology, material_tags=config.modeling.material_tags,
            test_material_tag=config.material.tag, area=config.area, length=config.length,
            topology_root=root, material_mode=config.material_mode,
        )
        compiled = compile_topology(root, default_area=config.area, total_length=config.length)
        elements = [{'tag': e.tag, 'name': e.name, 'type': 'truss' if config.material_mode == 'stress_strain' else 'twoNodeLink',
                     'i': e.node_i, 'j': e.node_j, 'material': e.material_tag, 'materials': [e.material_tag], 'directions': [1]}
                    for e in compiled.elements]
        for line in model.splitlines():
            target = node_lines if line.startswith('node ') else boundary_lines if line.startswith('fix ') else element_lines
            tag = int(line.split()[1] if line.startswith(('node ', 'fix ')) else line.split()[2])
            if line.startswith('node '):
                target.append(_node_note({'tag': tag, 'fix': [1, 1, 1] if tag == compiled.fixed_node else [0, 1, 1]}, elements, node, 1))
            elif line.startswith('element '):
                target.append(_element_note(next(e for e in elements if e['tag'] == tag)))
            target.append(_call(line))
        if not np.isclose(history[0], 0.0):
            history = np.r_[0., history]
        # The legacy Tcl runner writes the loading history with twelve significant digits.
        history = np.asarray([float(f'{value:.12g}') for value in history])
        dof = 1
        handler = "ops.constraints('Lagrange')"
        test = "ops.test('EnergyIncr', 1.0e-8, 100)"
        force = '-ops.nodeReaction(1, 1)'

    materials = _material_lines(config, material_commands, responses, elements)

    sections = []
    def section(key, title, lines, *, indent=False):
        sections.append((key, title, ['    ' + line if indent and line else line for line in lines]))

    section('M01', '运行依赖、结果目录与加载控制参数', [
        'import csv', 'from pathlib import Path', 'import openseespy.opensees as ops', '',
        "OUTPUT_DIR = Path(__file__).resolve().with_name(Path(__file__).stem + '_results')",
        f'CONTROL_NODE = {node}', f'CONTROL_DOF = {dof}', f'DT = {dt!r}',
        f'# 完整加载历程共 {len(history)} 点，见文件末尾 [M09]；只调整格式，采样顺序和精度保持不变。',
        '# DT 为瞬态分析的采样间隔 [s]；静力分析中仅用于伪时间步，不代表物理时间。',
    ])
    section('M02', '模型初始化与单轴材料（先定义子材料，再定义组合材料）', [
        'def build_model():', '    ops.wipe()', "    ops.model('basic', '-ndm', 2, '-ndf', 3)",
        '    # 材料参数直接写在下面的命令中；Tag 与后续单元引用一致。',
        '    # truss 使用应力—应变；连接平动使用力—位移，转动使用弯矩—转角。',
        '    try:', *(['        ' + line for line in materials] or ['        pass']),
        '    except Exception as exc:',
        "        raise RuntimeError('材料创建失败：请核对参数；TwoStage、SMA 等扩展材料需要支持该材料的 OpenSeesPy 内核。') from exc",
    ])
    section('M03', '几何节点（全局 X/Y；自由度 1=UX、2=UY、3=RZ）', node_lines, indent=True)
    section('M04', '构件与连接（端点、材料 Tag 和局部方向）', [
        f'# 杆参数：类型、Tag、i/j 节点、截面积 A [{config.length_unit}²]、应力—应变材料 Tag。',
        f'# 梁参数：类型、Tag、i/j 节点、A [{config.length_unit}²]、E [{config.force_unit}/{config.length_unit}²]、Iz [{config.length_unit}⁴]、坐标变换 Tag。',
        '# 连接参数：-mat 与 -dir 一一对应；zeroLength 两侧使用独立的重合节点。',
        '# -orient 定义局部 x/y 轴；连接方向 1/2 是局部平动，3 是绕 Z 转动。',
        *element_lines,
    ], indent=True)
    section('M05', '支承、节点约束' + ('与质量' if mass_lines else ''), [
        '# fix 按 UX/UY/RZ 排列，1=固定、0=自由；多点约束先主节点、后从节点。',
        *boundary_lines, *mass_lines,
    ], indent=True)
    load_lines = []
    if explicit and not transient:
        reference = [0.0, 0.0, 0.0]; reference[dof - 1] = 1.0
        load_lines = ["ops.timeSeries('Linear', 1)", "ops.pattern('Plain', 1, 1)",
                      f"ops.load(CONTROL_NODE, {', '.join(map(repr, reference))})"]
    else:
        load_lines = ["ops.timeSeries('Path', 1, '-dt', DT, '-values', *HISTORY, "
                      + ("'-useLast')" if transient else "HISTORY[-1])"),
                      "ops.pattern('Plain', 1, 1)", 'ops.sp(CONTROL_NODE, CONTROL_DOF, 1.0)']
    section('M06', '加载与求解设置（控制节点、方向、增量及收敛准则）', [
        *load_lines, handler, "ops.numberer('RCM')", "ops.system('BandGeneral')", test,
        "ops.algorithm('Newton')",
        *( ["ops.integrator('Newmark', 0.5, 0.25)", "ops.analysis('Transient')"] if transient else
           ["ops.integrator('DisplacementControl', CONTROL_NODE, CONTROL_DOF, 1.0e-6)", "ops.analysis('Static')"] if explicit else
           ["ops.integrator('LoadControl', 1.0)", "ops.analysis('Static')"] ),
    ], indent=True)
    if explicit:
        section('M07', '收敛重试（最多二分 8 层，失败时停止并报告）', [
            'def _advance(delta, depth=0):', "    ops.algorithm('Newton')",
            *([] if transient else ["    ops.integrator('DisplacementControl', CONTROL_NODE, CONTROL_DOF, delta)"]),
            '    ok = ops.analyze(1, delta)' if transient else '    ok = ops.analyze(1)',
            '    if ok == 0 or depth >= 8:', '        return ok',
            '    ok = _advance(delta / 2.0, depth + 1)',
            '    return ok if ok else _advance(delta / 2.0, depth + 1)',
        ])
    reaction = "ops.reactions('-dynamic')" if transient else 'ops.reactions()'
    time_column = 'time' if transient else 'pseudo_time'
    loop = ([
        '            record(0)', '            for sample, target in enumerate(HISTORY[1:], 1):',
        '                delta = DT' if transient else '                delta = target - ops.nodeDisp(CONTROL_NODE, CONTROL_DOF)',
        '                ok = _advance(delta)' if transient else '                ok = _advance(delta) if abs(delta) > 1.0e-15 else 0',
        '                if ok != 0:', "                    raise RuntimeError(f'分析未收敛，停止于采样点 {sample}，返回码 {ok}')",
        '                record(sample)',
    ] if explicit else [
        '            for sample in range(len(HISTORY)):', '                record(sample)',
        '                ok = ops.analyze(1)', '                if ok != 0:',
        "                    raise RuntimeError(f'分析未收敛，停止于采样点 {sample}，返回码 {ok}')",
    ])
    section('M08', '计算与结果记录（保留顺序；只执行本文件声明的分析）', [
        'def run(output_dir=OUTPUT_DIR):', '    output_dir = Path(output_dir)',
        '    output_dir.mkdir(parents=True, exist_ok=True)', "    response = output_dir / 'response.csv'",
        '    try:', '        build_model()', "        with response.open('w', newline='', encoding='utf-8') as stream:",
        '            writer = csv.writer(stream)', f"            writer.writerow(('sample', {time_column!r}, 'displacement', 'force'))",
        ('            # time 为物理时间 [s]。' if transient else
         '            # pseudo_time 为 OpenSees 静力伪时间，没有秒的物理含义。'),
        '            # displacement：控制自由度响应；force：与当前程序相同的有符号执行器力/弯矩。',
        '            def record(sample):', f'                {reaction}',
        f'                writer.writerow((sample, ops.getTime(), ops.nodeDisp(CONTROL_NODE, CONTROL_DOF), {force}))',
        '                stream.flush()', *loop,
        '    finally:', '        ops.wipe()',
        "    print(f'MATERIAL_TEST_COMPLETE: {response}')", '    return response',
    ])
    history_lines = ['# 每项为控制自由度的有序目标值；不插值、不删点，保留全部有效精度。',
                     f"# 单位：{'rad' if dof == 3 else config.length_unit}；修改时保持实际加载顺序。", 'HISTORY = (']
    for offset in range(0, len(history), 10):
        history_lines.append('    ' + ', '.join(repr(float(value)) for value in history[offset:offset+10]) + ',')
    history_lines.append(')')
    section('M09', '完整有序加载历程（数据附录，每行最多 10 个采样值）', history_lines)
    section('M10', '运行入口（模型、函数和完整加载历程定义后执行）', ["if __name__ == '__main__':", '    run()'])

    header = [
        '# -*- coding: utf-8 -*-', '# Material Test 0.1.0 · Python 建模与复算文件',
        f'# 模型对象：{_comment(config.material.name)}；来源：当前项目输入快照（包含未保存修改）。',
        f"# 单位：力 {config.force_unit}；长度 {config.length_unit}；转角 rad；" + ('瞬态物理时间 s。' if transient else '静力不使用物理时间。'),
        '# 坐标：平面全局 X/Y；每节点 3 自由度 UX/UY/RZ。单位声明不进行自动换算。',
        f"# 分析范围：{'显式装配瞬态' if transient else '显式装配静力位移控制' if explicit else '轴向材料静力循环'}；不执行拟合、模态或结构地震分析。",
        '# 外部依赖：Python 3.11+、OpenSeesPy；扩展材料需使用含相应实现的内核。',
        '# 运行：python 本文件.py；加载内嵌，结果写入本文件旁的同名 _results 目录。',
        f'# 输出：response.csv，列为 sample/{time_column}/displacement/force；RZ 加载对应转角/弯矩。',
        *(['# 项目材料说明：' + _comment(config.material.description)] if config.material.description else []),
        '# 模块目录（行号指向对应分区标题）：',
    ]
    toc_start = len(header)
    header += [''] * len(sections)
    lines = header + ['']
    for index, (key, title, content) in enumerate(sections):
        lines[toc_start + index] = f'# [{key}] 第 {len(lines) + 1} 行：{title}'
        lines += [f'# ==================== [{key}] {title} ====================', *content, '']
    source = '\n'.join(lines) + '\n'
    path = Path(path)
    compile(source, str(path), 'exec')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')
    return path
