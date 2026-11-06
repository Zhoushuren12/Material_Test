"""Replay exported Python models independently and compare to the actual Tcl solver."""
from copy import deepcopy
import os
from pathlib import Path
import re
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from material_test.domain import LoadingProtocol, MaterialTemplate, ModelingConfig, ProjectConfig, SolverConfig, TopologyNode
from material_test.components2d.templates import make_plate_joint, make_rotation, make_translation
from material_test.opensees.python_writer import write_python_model
from material_test.opensees.runner import run_material_history


ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / 'OS_terminal' / 'OpenSees.exe'


def config_for(tmp_path):
    return ProjectConfig(
        solver=SolverConfig(SOLVER),
        material=MaterialTemplate('导出试件', 'uniaxialMaterial Elastic 1 100\nuniaxialMaterial Elastic 2 50'),
        loading=LoadingProtocol(source='samples', samples=[0., .1, .1, -.2, 0.]),
        output_dir=tmp_path / 'runs', material_mode='force_deformation',
    )


def replay(config, tmp_path):
    pytest.importorskip('openseespy.opensees')
    if not SOLVER.exists():
        pytest.skip('OpenSees executable unavailable')
    path = write_python_model(config, tmp_path / '独立模型' / 'model.py')
    environment = dict(os.environ, PYTHONPATH='')
    result = subprocess.run([sys.executable, str(path)], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'MATERIAL_TEST_COMPLETE:' in result.stdout
    values = np.genfromtxt(path.with_name('model_results') / 'response.csv', delimiter=',', names=True)
    transient = config.modeling.assembly is not None and config.modeling.assembly['control'].get('analysis') == 'transient'
    assert ('time' if transient else 'pseudo_time') in values.dtype.names
    assert ('pseudo_time' if transient else 'time') not in values.dtype.names
    original = run_material_history(
        solver=config.solver, material=config.material,
        displacement_history=np.asarray(config.loading.samples), work_dir=tmp_path / 'original',
        modeling=config.modeling, material_mode=config.material_mode, area=config.area, length=config.length,
        force_unit=config.force_unit, length_unit=config.length_unit,
    )
    np.testing.assert_allclose(values['displacement'], original.displacement, rtol=1e-7, atol=1e-8)
    np.testing.assert_allclose(values['force'], original.force, rtol=1e-6, atol=1e-7)
    return values


@pytest.mark.parametrize('mode', ['force_deformation', 'stress_strain'])
@pytest.mark.parametrize('topology', ['single', 'series', 'parallel', 'nested'])
def test_legacy_export_matches_solver(tmp_path, mode, topology):
    config = config_for(tmp_path)
    config.material_mode = mode
    config.area = 2.; config.length = 4.
    config.modeling = ModelingConfig(topology=topology, material_tags=[1, 2])
    if topology == 'nested':
        config.modeling.root = TopologyNode.group('parallel', [
            TopologyNode.material(1), TopologyNode.group('series', [TopologyNode.material(1), TopologyNode.material(2)]),
        ])
    values = replay(config, tmp_path)
    assert len(values) == len(config.loading.samples)


@pytest.mark.parametrize('kind', ['translation', 'rotation', 'plate', 'viscous', 'truss', 'corotTruss', 'twoNodeLink'])
def test_explicit_export_matches_solver(tmp_path, kind):
    project = (make_rotation(tmp_path, SOLVER) if kind == 'rotation' else
               make_plate_joint(tmp_path, SOLVER, specimen=True) if kind == 'plate' else
               make_translation(tmp_path, SOLVER, viscous=kind == 'viscous'))
    if kind in {'truss', 'corotTruss'}:
        project.data['nodes'][1].update(x=1000., y=500.)
        project.data['materials'][0]['response'] = 'stress_strain'
        project.data['elements'] = [{'type': kind, 'tag': 1, 'i': 1, 'j': 2, 'A': 200., 'material': 1}]
    elif kind == 'twoNodeLink':
        project.data['nodes'][1]['x'] = 100.
        project.data['elements'][0]['type'] = kind
    config = config_for(tmp_path)
    config.material = MaterialTemplate('装配模型', '\n'.join(m.render_command() for m in project.materials.values()),
                                       tag=project.data['materials'][0]['tag'])
    config.modeling = ModelingConfig(assembly={
        **{key: deepcopy(project.data.get(key, [])) for key in ('nodes', 'elements', 'constraints')},
        'control': {key: value for key, value in project.data['loading'].items() if key in ('node', 'dof', 'analysis', 'dt')},
    })
    replay(config, tmp_path)


def test_external_history_is_embedded_and_module_lines_are_accurate(tmp_path):
    config = config_for(tmp_path)
    source = tmp_path / 'history.txt'
    np.savetxt(source, config.loading.samples)
    config.loading = LoadingProtocol(source='external', history_path=source, history_column=0, history_scale=-2.)
    path = write_python_model(config, tmp_path / 'model.py')
    source.unlink()
    lines = path.read_text(encoding='utf-8').splitlines()
    for key, number in re.findall(r'^# \[(M\d+)\] 第 (\d+) 行：', '\n'.join(lines), re.M):
        assert f'[{key}]' in lines[int(number) - 1]
        assert lines[int(number) - 1].startswith('# ===')
    assert 'import material_test' not in '\n'.join(lines)
    namespace = {'__file__': str(path), '__name__': 'export_test'}
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), namespace)
    np.testing.assert_array_equal(namespace['HISTORY'], [0., -.2, -.2, .4, 0.])


def test_unsupported_tcl_expression_preserves_existing_destination(tmp_path):
    config = config_for(tmp_path)
    config.material.command = 'uniaxialMaterial Elastic 1 [expr 50*2]'
    destination = tmp_path / 'model.py'; destination.write_bytes(b'existing model')
    with pytest.raises(ValueError, match='Tcl'):
        write_python_model(config, destination)
    assert destination.read_bytes() == b'existing model'


def test_large_history_keeps_model_first_and_exact_samples(tmp_path):
    config = config_for(tmp_path)
    config.loading.samples = [0., .12345678901234566, .12345678901234566, -.12345678901234566] * 3000
    # Use explicit nodes so history remains at the planar solver's full input precision.
    data = make_translation(tmp_path, SOLVER).data
    config.modeling = ModelingConfig(assembly={
        **{key: deepcopy(data.get(key, [])) for key in ('nodes', 'elements', 'constraints')},
        'control': data['loading'],
    })
    path = write_python_model(config, tmp_path / 'model.py')
    source = path.read_text(encoding='utf-8')
    namespace = {'__file__': str(path), '__name__': 'export_test'}
    exec(compile(source, str(path), 'exec'), namespace)
    np.testing.assert_array_equal(namespace['HISTORY'], config.loading.samples)
    assert source[:source.index('def build_model():')].count('\n') < 60
    assert source.index('def run(') < source.index('HISTORY = (') < source.index("if __name__ == '__main__':")
    assert len(source.splitlines()) < 1500
    assert '零长度连接 i 侧' in source and '零长度连接 j 侧' in source
    for key, number in re.findall(r'^# \[(M\d+)\] 第 (\d+) 行：', source, re.M):
        assert f'[{key}]' in source.splitlines()[int(number) - 1]


def test_named_steel02_arguments_preserve_mirrored_materials(tmp_path):
    config = config_for(tmp_path)
    config.material = MaterialTemplate('镜像材料',
        'uniaxialMaterial Steel02 1 190 134329 0.066 2 0.405 0.002 0.096 1 0.095 1\n'
        'uniaxialMaterial Steel02 3 190 134329 0.066 2 0.405 0.002 0.095 1 0.096 1')
    config.modeling = ModelingConfig(topology='parallel', material_tags=[1, 3])
    path = write_python_model(config, tmp_path / 'model.py')
    source = path.read_text(encoding='utf-8')
    calls = []
    class RecordingOps:
        def __getattr__(self, name):
            return lambda *arguments: calls.append((name, arguments))
    namespace = {'__file__': str(path), '__name__': 'export_test'}
    exec(compile(source, str(path), 'exec'), namespace)
    namespace['ops'] = RecordingOps()
    namespace['build_model']()
    actual = [arguments for name, arguments in calls if name == 'uniaxialMaterial']
    assert actual == [('Steel02', 1, 190, 134329, .066, 2, .405, .002, .096, 1, .095, 1),
                      ('Steel02', 3, 190, 134329, .066, 2, .405, .002, .095, 1, .096, 1)]
    assert "'Fy': 190" in source
    assert '屈服力 [N]' in source and '初始平动刚度 [N/mm]' in source
    assert '压向等向硬化参数' in source


def test_gui_exports_unsaved_inputs_without_a_calculation(tmp_path):
    from PySide6.QtWidgets import QApplication
    from material_test.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(restore_last_project=False)
    config = config_for(tmp_path)
    window._populate_material(config.material)
    window.topology_editor.set_root(TopologyNode.material(1))
    window.material_mode_combo.setCurrentIndex(window.material_mode_combo.findData('force_deformation'))
    window.amplitudes_edit.setPlainText('0, 0.1, -0.1, 0')
    window.du_spin.setValue(.1)
    window.current_project_path = tmp_path / 'unsaved.yaml'
    destination = tmp_path / 'exported'
    try:
        assert window.current_result is None
        assert window.export_python_button.isEnabled()
        assert window.export_python_action.shortcut().toString() == 'Ctrl+Shift+E'
        with patch('material_test.ui.main_window.QFileDialog.getSaveFileName', return_value=(str(destination), '')) as dialog, \
             patch('material_test.ui.main_window.QMessageBox.information') as information, \
             patch('material_test.ui.main_window.QMessageBox.warning') as warning:
            window.export_python_button.click()
        warning.assert_not_called()
        information.assert_called_once()
        assert destination.with_suffix('.py').exists()
        assert Path(dialog.call_args.args[2]).parent == tmp_path
        window._set_running(True, 'test')
        assert not window.export_python_action.isEnabled()
        window._set_running(False, 'test')
        assert window.export_python_action.isEnabled()
    finally:
        window._project_dirty = False
        window.close()
        app.processEvents()


@pytest.mark.parametrize('explicit', [False, True])
def test_nonlinear_wrapper_materials_match_solver(tmp_path, explicit):
    config = config_for(tmp_path)
    config.material.command = ('uniaxialMaterial Steel01 11 5 100 0.02\n'
                               'uniaxialMaterial Elastic 12 10\n'
                               'uniaxialMaterial Parallel 1 11 12')
    if explicit:
        data = make_translation(tmp_path, SOLVER).data
        config.modeling = ModelingConfig(assembly={
            **{key: deepcopy(data.get(key, [])) for key in ('nodes', 'elements', 'constraints')},
            'control': {key: data['loading'][key] for key in ('node', 'dof', 'analysis', 'dt')},
        })
    replay(config, tmp_path)
