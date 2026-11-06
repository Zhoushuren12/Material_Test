"""Publication must round-trip through SA's real editor and component solver."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np
import pytest

from material_test.brace_export import build_brace_package, checksum, export_constitutive, input_identity
from material_test.domain import (LoadingProtocol, MaterialTemplate, ModelingConfig,
                                 ProjectConfig, SolverConfig, TopologyNode)
from material_test.workbench_export import export_workbench_definition

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'Structural analysis'))
from structure_app.services.brace_component import editor_definition, resolve_component, IDENTITY_SCALE
from structure_app.services.brace_package import import_brace_package, validate_brace_package


def config_for(tmp_path):
    return ProjectConfig(SolverConfig(tmp_path/'unused.exe'),
        MaterialTemplate('steel-and-SMA',
            'uniaxialMaterial Steel02 102 32000 8500 .07 18 .925 .15 .01 1 .02 1\n'
            'uniaxialMaterial SelfCentering 201 12800 126 48200 .58\n'
            'uniaxialMaterial MinMax 301 201 -min -65 -max 65', tag=102),
        LoadingProtocol(amplitudes=[70, -70], du=.1), length=1000.,
        material_mode='force_deformation', modeling=ModelingConfig(root=TopologyNode.group('parallel',
            [TopologyNode.material(tag) for tag in [102]*12+[301]*8])))


def export_fixture(config, tmp_path):
    # Labelled signature fixture, not experimental or claimed calibrated data.
    curve = tmp_path/'simulation_curve.csv'
    curve.write_text('displacement,force\n0,0\n1,1\n', encoding='utf-8')
    (tmp_path/'input-manifest.json').write_text(json.dumps({
        'input_sha256':checksum(input_identity(config)),
        'simulation_sha256':hashlib.sha256(curve.read_bytes()).hexdigest()}), encoding='utf-8')
    return build_brace_package(config, result_dir=tmp_path, workbench=True)


@pytest.mark.parametrize('optional', ['', ' .01 1 .02 1', ' .01 1 .02 1 7'])
def test_steel02_optional_groups_preserve_values_and_units(tmp_path, optional):
    config = config_for(tmp_path)
    config.material = MaterialTemplate('steel', 'uniaxialMaterial Steel02 1 20 100 .02 18 .925 .15'+optional)
    config.modeling = ModelingConfig(root=TopologyNode.material(1))
    config.material_mode='stress_strain'; config.area=3.; config.length=2.
    values = export_constitutive(config)['parameters']
    assert values['Fy']==60 and values['E0']==150
    if optional:
        assert [values[k] for k in ('a1','a2','a3','a4')]==[.01,1.,.02,1.]
    if optional.endswith(' 7'):
        assert values['sigInit']==21.


def test_v3_import_reopens_in_explicit_workbench_and_preserves_source(tmp_path):
    config = config_for(tmp_path); before = deepcopy(config)
    package = export_fixture(config, tmp_path)
    validate_brace_package(package)
    path=tmp_path/'support.oasbrace'; path.write_text(json.dumps(package), encoding='utf-8')
    resource=import_brace_package(path)
    definition, mode, root, units=editor_definition(resource)
    assert package['schema_version']==3 and mode=='explicit'
    assert units=={'force':'N','displacement':'mm'}
    assert len(definition['stages'][0]['branches'])==20
    assert len(definition['materials'])==2
    assert definition['materials'][1]['law']['type']=='MinMax'
    assert definition['materials'][1]['law']['parameters']=={'min':-65.,'max':65.}
    assert config==before
    definition['stages'][0]['branches'].pop()
    assert len(resource.package_snapshot['constitutive']['stages'][0]['branches'])==20
    config.material.command += '\n'
    with pytest.raises(ValueError,match='重新计算'):
        build_brace_package(config,result_dir=tmp_path,workbench=True)


def explicit_beams(config):
    config.modeling=ModelingConfig(topology='explicit',assembly={
        'nodes':[{'tag':i+1,'x':x,'y':0.,'fix':[1,1,1] if i==0 else [0,1,1]}
                 for i,x in enumerate([0.,100.,200.,300.])],
        'elements':[{'tag':1,'type':'elasticBeamColumn','i':1,'j':2,'A':2.,'E':200.,'Iz':3.},
                    *[{'tag':i+2,'type':'twoNodeLink','i':2,'j':3,'materials':[tag],
                       'directions':[1],'axis_auto':True} for i,tag in enumerate([102,301])],
                    {'tag':4,'type':'elasticBeamColumn','i':3,'j':4,'A':2.,'E':200.,'Iz':3.}],
        'constraints':[],'control':{'node':4,'dof':1,'analysis':'static'}})
    return config


def test_explicit_beam_parallel_beam_geometry_and_units(tmp_path):
    config=explicit_beams(config_for(tmp_path)); config.force_unit='kN';config.length_unit='cm'
    package=export_fixture(config,tmp_path)
    definition=package['constitutive']
    assert [s['kind'] for s in definition['stages']]==['beam','link','beam']
    assert definition['reference_length_mm']==3000.
    assert definition['stages'][0]['section']=={'E':2000.,'A':200.,'Iz':30000.}
    assert definition['connection_mode']=='axial-only'
    assert [len(s.get('branches',[])) for s in definition['stages']]==[0,2,0]
    config.modeling.assembly['nodes'][1]['fix']=[0,0,0]
    with pytest.raises(ValueError,match='内部约束'):
        export_workbench_definition(config)


@pytest.mark.parametrize('change, message', [
    (lambda g:g['nodes'][1].update(y=1.),'共轴'),
    (lambda g:g['constraints'].append({'type':'equalDOF','retained':2,'constrained':3,'dofs':[2]}),'多点约束'),
    (lambda g:g['elements'][1].update(type='corotTruss',material=102),'corotTruss'),
    (lambda g:g['elements'][1].update(axis_auto=False,axis=[0.,1.]),'局部轴'),
])
def test_unsupported_graph_is_rejected_without_changing_source(tmp_path,change,message):
    config=explicit_beams(config_for(tmp_path)); change(config.modeling.assembly)
    before=deepcopy(config)
    with pytest.raises(ValueError,match=message):export_workbench_definition(config)
    assert config==before


def test_parallel_nonlinear_curve_and_fracture_match_downstream_kernel(tmp_path):
    ops=pytest.importorskip('openseespy.opensees')
    from structure_app.workers.component_coupon import run_component_coupon
    config=config_for(tmp_path); package=export_fixture(config,tmp_path)
    history=np.r_[np.linspace(0,60,121),np.linspace(60,-60,241)[1:],
                  np.linspace(-60,70,261)[1:],np.linspace(70,-70,281)[1:],np.linspace(-70,0,141)[1:]]
    ops.wipe();ops.model('basic','-ndm',2,'-ndf',3)
    ops.node(1,0.,0.);ops.node(2,1000.,0.);ops.fix(1,1,1,1);ops.fix(2,0,1,1)
    ops.uniaxialMaterial('Steel02',102,32000,8500,.07,18,.925,.15,.01,1,.02,1)
    ops.uniaxialMaterial('SelfCentering',201,12800,126,48200,.58)
    ops.uniaxialMaterial('MinMax',301,201,'-min',-65,'-max',65)
    for index,tag in enumerate([102]*12+[301]*8,1):
        ops.element('twoNodeLink',index,1,2,'-mat',tag,'-dir',1)
    ops.timeSeries('Linear',1);ops.pattern('Plain',1,1);ops.load(2,1.,0.,0.)
    ops.constraints('Plain');ops.numberer('Plain');ops.system('BandGeneral')
    ops.test('NormDispIncr',1e-10,100);ops.algorithm('Newton')
    ops.integrator('DisplacementControl',2,1,.5);ops.analysis('Static')
    expected=[0.]
    for target in history[1:]:
        ops.integrator('DisplacementControl',2,1,float(target)-ops.nodeDisp(2,1))
        assert ops.analyze(1)==0
        expected.append(ops.getLoadFactor(1))
    ops.wipe()
    model=resolve_component(package['constitutive'],package,IDENTITY_SCALE)
    result=run_component_coupon({'contract':model,'loading_path':history.tolist()})
    np.testing.assert_allclose(np.asarray(result['points'])[:,1],expected,rtol=1e-8,atol=1e-5)
    assert all(h['checks']['passed'] for h in result['component_history'])


def test_publish_button_lives_on_assembly_toolbar_and_stale_state_blocks_it(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from material_test.ui.main_window import MainWindow
    from material_test.workflow import WorkflowResult
    from material_test.domain import SimulationResult
    app=QApplication.instance() or QApplication([])
    window=MainWindow(restore_last_project=False)
    try:
        toolbar=window.topology_editor.toolbar
        assert window.publish_button.parent() is toolbar
        assert toolbar.widgetForAction(toolbar.actions()[-1]) is window.publish_button
        assert not window.publish_button.isEnabled()
        file=tmp_path/'fixture'
        simulation=SimulationResult(np.array([0.,1.]),np.array([0.,1.]),file,file,file)
        window.current_result=WorkflowResult(tmp_path,simulation,None,file,file)
        window._set_running(False,'test')
        assert window.publish_button.isEnabled() and window.publish_brace_action.isEnabled()
        config=config_for(tmp_path)
        export_fixture(config,tmp_path)
        # Exercise the real clicked handler and package writer, with only the
        # native dialogs replaced so the test needs no human interaction.
        destination=tmp_path/'published_from_toolbar'
        with patch.object(window,'_comparison_config_from_ui',return_value=config), \
             patch('material_test.ui.fitting_workflow.QFileDialog.getSaveFileName',return_value=(str(destination),'')), \
             patch('material_test.ui.fitting_workflow.QMessageBox.information'), \
             patch('material_test.ui.fitting_workflow.QMessageBox.warning') as warning:
            window.publish_button.click()
            warning.assert_not_called()
        imported=import_brace_package(destination.with_suffix('.oasbrace'))
        assert editor_definition(imported)[1]=='explicit'
        window._mark_comparison_dirty('修改参数')
        assert not window.publish_button.isEnabled() and not window.publish_brace_action.isEnabled()
    finally:
        window._project_dirty=False;window.close();app.processEvents()
