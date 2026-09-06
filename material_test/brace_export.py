"""Versioned two-terminal force/deformation packages for Structural analysis.

Only typed materials are exported. No Tcl/Python from a package is executed by
the structural solver. Geometry converts stress/strain laws to axial F/u laws.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shlex
from uuid import uuid4
import numpy as np
from material_test.io.experiment_data import load_experiment_data

FIELDS = {'Elastic': [('k','stiffness')], 'Steel01': [('Fy','force'),('k','stiffness'),('b','ratio')],
          'SelfCentering': [('k1','stiffness'),('k2','stiffness'),('sigAct','force'),('beta','ratio'),
                           ('epsSlip','length'),('epsBear','length'),('rBear','ratio')]}

def checksum(data):
    data=deepcopy(data);data.pop('content_sha256',None)
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def input_identity(config):
    result={'material':config.material.to_mapping(),'modeling':config.modeling.to_mapping(),
            'area':config.area,'length':config.length,'loading':config.loading.to_mapping(),
            'experiment_columns':[config.displacement_column,config.force_column,config.experiment_sheet],
            'solver':config.solver.to_mapping()}
    for name,path in [('experiment',config.experiment_path),('history',config.loading.history_path),('solver_executable',config.solver.executable_path)]:
        result[name+'_sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest() if path and Path(path).is_file() else None
    return result

def export_constitutive(config):
    commands={}
    for line in config.material.render_command().splitlines():
        tokens=shlex.split(line,comments=True)
        if not tokens:continue
        if len(tokens)<4 or tokens[0]!='uniaxialMaterial':
            raise ValueError('发布仅支持明确的 uniaxialMaterial 声明；不允许脚本或控制语句')
        tag=int(tokens[2])
        if tag in commands:raise ValueError('材料 Tag 重复')
        commands[tag]=(tokens[1],tokens[3:])
    def material(tag,area,length,active=()):
        if tag in active or tag not in commands:raise ValueError('材料引用循环或缺少 Tag')
        kind,args=commands[tag];active=(*active,tag)
        if kind in {'Series','Parallel'}:
            if len(args)<2:raise ValueError('串并联材料至少需要两个引用；发布不支持自定义迭代选项')
            return {'type':kind,'children':[material(int(a),area,length,active) for a in args]}
        if kind=='MinMax':
            if len(args)!=5 or set(args[1::2])!={'-min','-max'}:raise ValueError('MinMax 发布需同时给出 -min 和 -max')
            bounds=dict(zip(args[1::2],args[2::2]))
            return {'type':kind,'child':material(int(args[0]),area,length,active),
                    'parameters':{'min':float(bounds['-min'])*length,'max':float(bounds['-max'])*length}}
        if kind not in FIELDS:raise ValueError(f'当前支撑包尚不支持材料 {kind}，不能用曲线代替其本构')
        fields=FIELDS[kind]
        minimum=4 if kind=='SelfCentering' else len(fields)
        if not minimum<=len(args)<=len(fields):raise ValueError(f'{kind} 参数数量无效')
        values=[float(a) for a in args]
        if not all(math.isfinite(a) for a in values):raise ValueError('本构必须为有限数值')
        values+= [0.,0.,1.][max(0,len(values)-4):] if kind=='SelfCentering' else []
        factors={'force':area,'stiffness':area/length,'length':length,'ratio':1.}
        return {'type':kind,'parameters':{n:v*factors[d] for (n,d),v in zip(fields,values)}}
    def topology(node,length):
        if node.kind=='material':
            return material(node.material_tag,node.area if node.area is not None else config.area,length)
        weights=[c.length_weight for c in node.children]
        return {'type':node.kind.title(),'name':node.name,'realization':'explicit-axial-network',
                'children':[topology(c,length*w/sum(weights) if node.kind=='series' else length) for c,w in zip(node.children,weights)]}
    root=config.modeling.resolved_root(config.material.tag);root.validate()
    if not all(math.isfinite(v) and v>0 for v in (config.area,config.length)):raise ValueError('面积和长度必须为有限正值')
    return topology(root,config.length)

def build_brace_package(config, *, result_dir, version='1', force_unit='N', displacement_unit='mm'):
    if force_unit not in {'N','kN','MN'} or displacement_unit not in {'mm','cm','m'}:
        raise ValueError('请选择明确的力与长度单位')
    result_dir=Path(result_dir)
    manifest=json.loads((result_dir/'input-manifest.json').read_text(encoding='utf-8'))
    identity=input_identity(config)
    if checksum(identity)!=manifest['input_sha256']:
        raise ValueError('当前参数、装配或实验输入已改变，请先重新计算再发布支撑模型')
    simulation_sha=hashlib.sha256((result_dir/'simulation_curve.csv').read_bytes()).hexdigest()
    if simulation_sha != manifest.get('simulation_sha256'):
        raise ValueError('计算结果缺少校验记录或文件已改变，请先重新计算再发布支撑模型')
    if not config.experiment_path:raise ValueError('发布支撑模型需要实际实验数据及其来源')
    experiment=load_experiment_data(config.experiment_path,displacement_column=config.displacement_column,
                                    force_column=config.force_column,sheet_name=config.experiment_sheet)
    simulation=np.loadtxt(result_dir/'simulation_curve.csv',delimiter=',',skiprows=1,ndmin=2)
    curves=[{'id':'experiment','name':'原始实验','kind':'experimental',
             'points':np.c_[experiment.displacement,experiment.force].tolist()},
            {'id':'simulation','name':'当前参数计算','kind':'numerical','points':simulation[:,:2].tolist()}]
    result={'format':'open-analysis-brace-package','schema_version':2,'id':'component_'+uuid4().hex,
            'name':config.material.name,'version':str(version),'units':{'force':force_unit,'displacement':displacement_unit},
            'interfaces':{'type':'two-terminal-axial','ports':['i','j'],'positive':'extension'},
            'topology':{'type':'axial-brace','element_type':'twoNodeLink'},'constitutive':export_constitutive(config),
            'parameters':[],'curves':curves,'story_overrides':[],
            'evidence':{'experiment_path':str(config.experiment_path),'experiment_sha256':identity['experiment_sha256'],
                        'input_sha256':manifest['input_sha256'],'input_snapshot':identity,
                        'simulation_sha256':simulation_sha,
                        'run_directory':str(result_dir.resolve()),'exported_at':datetime.now(timezone.utc).isoformat(),
                        'scope':'Validated loading history; typed axial constitutive graph. No general spatial mechanism equivalence claim.'}}
    result['content_sha256']=checksum(result)
    return result

def write_brace_package(config, path, *, result_dir, version='1'):
    result=build_brace_package(config,result_dir=result_dir,version=version)
    Path(path).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return result
