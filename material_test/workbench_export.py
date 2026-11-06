"""Convert supported collinear assemblies into the SA editable component schema.

No solver/UI is copied. Unsupported graphs are rejected rather than condensed
into a different constitutive model or silently stabilized.
"""
from copy import deepcopy
import math
from pathlib import Path
import sys

from material_test.brace_export import _material_commands, _material_law
from material_test.explicit_assembly import validate_assembly

LENGTH_MM = {'mm': 1., 'cm': 10., 'm': 1000.}
FORCE_N = {'N': 1., 'kN': 1000., 'MN': 1e6}


def _legacy_graph(config):
    """Use the same lengths, areas and leaf elements as the legacy solver."""
    graph = {'nodes': [], 'elements': [], 'constraints': [],
             'control': {'node': 2, 'dof': 1}}
    def node(x):
        tag = len(graph['nodes']) + 1
        graph['nodes'].append({'tag': tag, 'x': x, 'y': 0., 'fix': [0, 1, 1]})
        return tag
    start, end = node(0.), node(config.length)
    graph['nodes'][0]['fix'] = [1, 1, 1]
    def emit(root, i, j):
        if root.kind == 'material':
            element = {'tag': len(graph['elements'])+1, 'name': root.name,
                       'i': i, 'j': j}
            if config.material_mode == 'force_deformation':
                element.update(type='twoNodeLink', materials=[root.material_tag], directions=[1])
            else:
                element.update(type='truss', material=root.material_tag, A=root.area or config.area)
            graph['elements'].append(element)
        elif root.kind == 'parallel':
            for child in root.children:
                emit(child, i, j)
        else:
            left = graph['nodes'][i-1]['x']; right = graph['nodes'][j-1]['x']
            total = sum(child.length_weight for child in root.children)
            previous, fraction = i, 0.
            for index, child in enumerate(root.children):
                fraction += child.length_weight/total
                following = j if index == len(root.children)-1 else node(left+(right-left)*fraction)
                emit(child, previous, following); previous = following
    root = config.modeling.resolved_root(config.material.tag)
    root.validate(); emit(root, start, end)
    return graph


def export_workbench_definition(config):
    graph = deepcopy(config.modeling.assembly) if config.modeling.assembly is not None else _legacy_graph(config)
    validate_assembly(graph)
    if graph.get('constraints'):
        raise ValueError('工作台目前不支持转换含 equalDOF / rigidLink 的装配；请保留原模型，不能省略多点约束')
    if graph['control']['dof'] != 1 or graph['control'].get('analysis', 'static') != 'static':
        raise ValueError('发布工作台构件需要静力轴向位移加载；转动、横向或瞬态试算不能作为轴向来源曲线')
    nodes = {n['tag']: n for n in graph['nodes']}
    if any(any(float(v) != 0 for v in n.get('mass', [])) for n in nodes.values()):
        raise ValueError('工作台不能保留节点质量；请保留原动态模型')
    adjacency = {tag: set() for tag in nodes}; pairs = {}
    for e in graph['elements']:
        i, j = e['i'], e['j']
        adjacency[i].add(j); adjacency[j].add(i)
        pairs.setdefault(frozenset((i, j)), []).append(e)
    ends = [tag for tag, neighbours in adjacency.items() if len(neighbours) == 1]
    control = graph['control']['node']
    if len(ends) != 2 or control not in ends or any(len(v) > 2 for v in adjacency.values()):
        raise ValueError('图二工作台仅支持共轴串联段及同端点并联分支；当前装配含分叉、回路或内部加载点')
    start = next(tag for tag in ends if tag != control)
    ordered = [start]
    while ordered[-1] != control:
        following = adjacency[ordered[-1]] - set(ordered)
        if len(following) != 1:
            raise ValueError('装配无法转换为唯一串联段序列')
        ordered.append(following.pop())
    if set(ordered) != set(nodes):
        raise ValueError('装配存在未参与串联构件的节点')
    first, last = nodes[start], nodes[control]
    dx, dy = last['x']-first['x'], last['y']-first['y']
    span = math.hypot(dx, dy); tolerance = max(1e-8, span*1e-8)
    # Source curves are global UX versus actuator force, so only a +X axis
    # has the same declared axial quantity without transforming the curves.
    if dx <= 0 or abs(dy) > tolerance:
        raise ValueError('当前来源曲线按全局 UX 记录；请沿全局 +X 布置轴向试算后发布，避免误标局部力与位移')
    if any(abs(n['y']-first['y']) > tolerance for n in nodes.values()):
        raise ValueError('工作台仅支持共轴构件；不能把非共轴节点投影成直线')
    internal = [nodes[tag].get('fix', [0,0,0]) for tag in ordered[1:-1]]
    if any(fix != [0,1,1] for fix in internal) and any(fix != [0,0,0] for fix in internal):
        raise ValueError('内部约束不能映射为统一的连续二维 / 仅轴向连接；请保留原约束模型')
    axial = bool(internal) and all(fix == [0,1,1] for fix in internal)
    commands = _material_commands(config)
    materials, material_ids = [], {}
    def material(tag, response):
        key = (tag, response)
        if key in material_ids:
            return material_ids[key]
        mid = f'mat_{tag}_{response.replace("-", "_")}'
        law = _material_law(commands, tag)
        material_ids[key] = mid
        materials.append({'id': mid, 'name': f'Tag {tag} · {law["type"]}',
                          'scope': 'material-specimen' if response == 'stress-strain' else 'core-device',
                          'response': response, 'coverage': '当前材料或装置分支，不包含其他显式单元',
                          'source': 'Material Test 当前参数；试验/计算核验状态见构件包 evidence', 'law': law})
        return mid
    length = LENGTH_MM[config.length_unit]; force = FORCE_N[config.force_unit]
    stages = []
    for index, (i, j) in enumerate(zip(ordered, ordered[1:]), 1):
        group = pairs[frozenset((i,j))]
        local_length = nodes[j]['x']-nodes[i]['x']
        if local_length < -tolerance:
            raise ValueError('串联路径存在反向折返，工作台不能无损重排节点')
        stage = {'id': f'stage_{index}', 'name': ' / '.join(e.get('name') or f'E{e["tag"]}' for e in group),
                 'length_mm': max(0., local_length)*length, 'length_mode': 'fixed'}
        if any(e['i'] != i or e['j'] != j for e in group):
            raise ValueError('单元端点方向与安装轴相反；不能无提示反转非对称本构')
        kinds = {e['type'] for e in group}
        if kinds <= {'twoNodeLink', 'zeroLength'}:
            branches = []
            for e in group:
                axis = [1.,0.] if e.get('axis_auto', True) and local_length > tolerance else e.get('axis', [1.,0.])
                norm = math.hypot(*axis) if len(axis)==2 else 0.
                if not math.isfinite(norm) or norm<=0 or axis[0]<=0 or abs(axis[1])/norm>1e-8:
                    raise ValueError(f'E{e["tag"]} 局部轴与构件轴不一致')
                if e['type']=='zeroLength' and abs(local_length)>tolerance:
                    raise ValueError('zeroLength 两端不重合，不能当作有限长 Link 导出')
                if 1 not in e['directions']:
                    raise ValueError(f'E{e["tag"]} 缺少轴向材料；工作台分支必须有局部1方向本构')
                branch = {'id': f'branch_{e["tag"]}', 'name': e.get('name') or f'E{e["tag"]}',
                          'quantity': 1, 'mode': 'independent', 'parameter_basis': 'single',
                          'transverse_material': None, 'rotation_material': None}
                for direction, tag in zip(e['directions'], e['materials']):
                    key = {1:'material', 2:'transverse_material', 3:'rotation_material'}[direction]
                    branch[key] = material(tag, 'moment-rotation' if direction==3 else 'force-displacement')
                branches.append(branch)
            stage.update(kind='link', branches=branches)
        elif len(group)==1 and kinds=={'elasticBeamColumn'}:
            e = group[0]
            if e.get('transformation', 'Linear')!='Linear' or e.get('release',0)!=0:
                raise ValueError('工作台目前仅支持源装配的 Linear 无释放梁段')
            stage.update(kind='beam', section={'E': float(e['E'])*force/length**2,
                         'A': float(e['A'])*length**2, 'Iz': float(e['Iz'])*length**4},
                         transformation='Linear', release=0)
        elif len(group)==1 and kinds=={'truss'}:
            e = group[0]
            stage.update(kind='truss', section={'A':float(e['A'])*length**2},
                         material=material(e['material'], 'stress-strain'))
        else:
            raise ValueError('工作台不支持并联梁/杆、混合并联单元或 corotTruss；原单元不能替换为其他类型')
        if any(float(e.get(key,0)) != 0 for e in group for key in ('mass','rho')):
            raise ValueError('工作台不能保留单元质量，请保留原模型')
        stages.append(stage)
    if not materials:
        raise ValueError('发布支撑构件至少需要一个已赋值的材料或装置分支')
    return {'type':'Assembly2D', 'geometry':'collinear', 'reference_length_mm':span*length,
            'materials':materials, 'stages':stages, 'connection_mode':'axial-only' if axial else 'continuous',
            'coverage_confirmed':False, 'ports':{'i':'shared-frame-node', 'j':'shared-frame-node'},
            'description':'Material Test 共轴显式装配；几何、独立分支及本构参数按来源保留。'}


def validate_workbench_package(package):
    """Validate with the actual downstream kernel, without loading either Qt UI."""
    root = Path(__file__).resolve().parents[3] / 'Structural analysis'
    if not (root/'structure_app/services/brace_component.py').is_file():
        raise ValueError('工作台构件发布需要同一工作区的 Structural analysis 构件内核')
    inserted = str(root) not in sys.path
    if inserted: sys.path.insert(0, str(root))
    try:
        from structure_app.services.brace_package import validate_brace_package
        validate_brace_package(package)
    finally:
        if inserted: sys.path.remove(str(root))
