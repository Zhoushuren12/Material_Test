"""Explicit support graphs share the material workflow and planar solver."""
from copy import deepcopy
import math
from pathlib import Path

import numpy as np

from material_test.domain import SimulationResult
from material_test.material_commands import parse_material_commands

ELEMENT_TYPES = ('twoNodeLink', 'zeroLength', 'truss', 'corotTruss', 'elasticBeamColumn')


def empty_assembly():
    return {'nodes': [], 'elements': [], 'constraints': [],
            'control': {'node': None, 'dof': 1, 'analysis': 'static', 'dt': .01}}


def material_tags(element):
    if element['type'] == 'elasticBeamColumn':
        return []
    return element.get('materials', []) if element['type'] in ('twoNodeLink', 'zeroLength') else [element.get('material')]


def validate_assembly(graph, *, ready=True):
    if not isinstance(graph, dict):
        raise ValueError('显式装配必须为节点与单元集合')
    nodes = {}
    for node in graph.get('nodes', []):
        key = node['tag']
        if not isinstance(key, int) or key <= 0 or key in nodes:
            raise ValueError('节点编号必须为不重复的正整数')
        if not all(math.isfinite(float(node[k])) for k in ('x', 'y')):
            raise ValueError(f'N{key} 坐标必须为有限数值')
        if len(node.get('fix', [])) != 3 or any(v not in (0, 1) for v in node['fix']):
            raise ValueError(f'N{key} 约束必须声明 UX / UY / RZ')
        nodes[key] = node
    seen = set()
    for e in graph.get('elements', []):
        key = e['tag']
        if not isinstance(key, int) or key <= 0 or key in seen:
            raise ValueError('单元编号必须为不重复的正整数')
        seen.add(key)
        if e['type'] not in ELEMENT_TYPES or e['i'] not in nodes or e['j'] not in nodes or e['i'] == e['j']:
            raise ValueError(f'E{key} 类型或端点无效')
        if e['type'] in ('twoNodeLink','zeroLength'):
            dirs,mats = e.get('directions',[]),e.get('materials',[])
            if not dirs or len(dirs)!=len(mats) or len(set(dirs))!=len(dirs) or any(d not in (1,2,3) for d in dirs):
                raise ValueError(f'E{key} 需要为每个局部方向指定一个材料槽位')
        if ready and any(tag is None for tag in material_tags(e)):
            raise ValueError(f"{e.get('name') or 'E'+str(key)} 尚未分配材料")
    for c in graph.get('constraints',[]):
        if c.get('type') not in ('rigidLink','equalDOF') or c.get('retained') not in nodes or c.get('constrained') not in nodes or c['retained']==c['constrained']:
            raise ValueError('多点约束类型或节点无效')
    if not ready:
        return
    if len(nodes) < 2 or not seen:
        raise ValueError('请在画布上创建节点与单元')
    control = graph.get('control', {})
    if control.get('node') not in nodes or control.get('dof') not in (1, 2, 3):
        raise ValueError('请选择位移控制节点和加载方向')
    if nodes[control['node']]['fix'][control['dof']-1]:
        raise ValueError('加载自由度被固定，请解除对应约束')
    # Reject floating/disconnected parts before invoking the native solver.
    edges = {key: set() for key in nodes}
    for e in graph['elements']:
        edges[e['i']].add(e['j']); edges[e['j']].add(e['i'])
    for c in graph.get('constraints', []):
        edges[c['retained']].add(c['constrained']); edges[c['constrained']].add(c['retained'])
    reached, pending = set(), [control['node']]
    while pending:
        key = pending.pop()
        if key not in reached:
            reached.add(key); pending.extend(edges[key] - reached)
    if reached != set(nodes):
        raise ValueError('模型存在孤立节点或未连接的组件：' + ', '.join(f'N{k}' for k in sorted(set(nodes)-reached)))


def component_from_inputs(graph, material, solver, history, output_dir, *, force_unit='N', length_unit='mm'):
    from material_test.components2d.model import ComponentProject
    validate_assembly(graph)
    graph = deepcopy(graph)
    dimensions = {}
    for e in graph['elements']:
        tags = material_tags(e)
        dirs = e.get('directions', [])
        for index, tag in enumerate(tags):
            response = 'stress_strain' if e['type'] in ('truss', 'corotTruss') else ('moment_rotation' if dirs[index] == 3 else 'force_deformation')
            if tag in dimensions and dimensions[tag] != response:
                raise ValueError(f'Tag {tag} 同时赋予了不同响应维度的单元，请使用独立材料定义')
            dimensions[tag] = response
        if e['type'] in ('twoNodeLink', 'zeroLength') and e.get('axis_auto', True):
            nodes = {n['tag']: n for n in graph['nodes']}
            dx = nodes[e['j']]['x'] - nodes[e['i']]['x']
            dy = nodes[e['j']]['y'] - nodes[e['i']]['y']
            if math.hypot(dx, dy) > 1e-9:
                e['axis'] = [dx, dy]
    commands = parse_material_commands(material)
    # Wrapper children inherit the root response, never rescale by display length.
    by_tag = {c.tag: c for c in commands}
    def inherit(tag, response, active=()):
        if tag in active or tag not in by_tag:
            raise ValueError(f'材料 Tag {tag} 缺失或循环引用')
        if tag in dimensions and dimensions[tag] != response:
            raise ValueError(f'材料 Tag {tag} 的响应维度冲突')
        dimensions[tag] = response
        for child in by_tag[tag].referenced_tags:
            inherit(child, response, (*active, tag))
    for tag, response in list(dimensions.items()):
        inherit(tag, response)
    resources = [{'tag': c.tag, 'name': f'{c.material_type} {c.tag}', 'command': c.rendered,
                  'response': dimensions[c.tag]}
                 for c in commands if c.tag in dimensions]
    history = np.asarray(history, dtype=float)
    if history.ndim!=1 or history.size<2 or not np.isfinite(history).all():
        raise ValueError('加载历程至少需要两个有序的有限数值')
    if history[0] != 0:
        history = np.r_[0., history]
    control = graph['control']
    dof = control['dof']; node = control['node']
    dynamic = control.get('analysis', 'static') == 'transient'
    force_channel = {'name': 'actuator_force', 'quantity': 'reaction', 'node': node, 'dof': dof} if dynamic else {'name': 'actuator_force', 'quantity': 'load_factor'}
    data = {'schema': 'material-test/component-2d/v1', 'name': material.name,
            'solver': solver.to_mapping(), 'output_dir': str(Path(output_dir).resolve()),
            'units': {'force': force_unit, 'length': length_unit, 'time': 's'},
            'nodes': graph['nodes'], 'elements': graph['elements'], 'constraints': graph.get('constraints', []),
            'materials': resources, 'loading': {**control, 'history': history.tolist()},
            'channels': [{'name': 'actuator_displacement', 'quantity': 'disp', 'node': node, 'dof': dof}, force_channel],
            'plot': {'x': 'actuator_displacement', 'y': 'actuator_force'}}
    return ComponentProject(data, Path(output_dir).resolve()).validate()


def response_units(config):
    rotation = config.modeling.assembly is not None and config.modeling.assembly['control']['dof']==3
    return (f'{config.force_unit}*{config.length_unit}', 'rad') if rotation else (config.force_unit,config.length_unit)


def run_explicit_history(*, solver, material, modeling, displacement_history, work_dir,
                         force_unit='N', length_unit='mm', cancel_check=None, record_deformation=True):
    from material_test.deformation import DeformationHistory
    from material_test.components2d.solver import run_component
    project = component_from_inputs(modeling.assembly, material, solver, displacement_history, work_dir,
                                    force_unit=force_unit,length_unit=length_unit)
    result = run_component(project, run_dir=Path(work_dir) / 'explicit', cancel_check=cancel_check,
                           scratch=True, record_displacements=record_deformation)
    return SimulationResult(result.channel('actuator_displacement'), result.channel('actuator_force'),
                            result.model_file, result.run_dir / 'history.txt', result.run_dir / 'response.csv',
                            result.stdout, result.stderr,
                            deformation=DeformationHistory.from_component(project, result) if record_deformation else None)
