"""Verify deformation playback against actual OpenSees node responses."""
from copy import deepcopy
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from material_test.components2d.solver import run_component, write_component_tcl
from material_test.components2d.templates import make_plate_joint
from material_test.deformation import DeformationHistory
from material_test.domain import LoadingProtocol, MaterialTemplate, ModelingConfig, ProjectConfig, SolverConfig
from material_test.explicit_assembly import component_from_inputs, empty_assembly
from material_test.opensees.runner import run_material_history
from material_test.ui.assembly_deformation import AssemblyDeformationDialog
from material_test.ui.main_window import MainWindow
from material_test.workflow import run_project

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT/'OS_terminal'/'OpenSees.exe'
pytestmark = pytest.mark.skipif(not SOLVER.exists(), reason='OpenSees unavailable')


def config_for(output, *, rotation=False, transient=False):
    graph = empty_assembly()
    if rotation:
        graph['nodes'] = [{'tag': 1, 'x': 0., 'y': 0., 'fix': [1, 1, 1]},
                          {'tag': 2, 'x': 0., 'y': 0., 'fix': [1, 1, 0]}]
        graph['elements'] = [{'tag': 1, 'type': 'zeroLength', 'i': 1, 'j': 2,
                              'directions': [3], 'materials': [1]}]
        graph['control'].update(node=2, dof=3)
        samples = [0., .01, .01, -.01, 0.]
    else:
        graph['nodes'] = [{'tag': tag, 'x': float((tag-1)*500), 'y': 0.,
                           'fix': [int(tag == 1), 1, 1]} for tag in (1, 2, 3)]
        graph['elements'] = [{'tag': tag, 'type': 'twoNodeLink', 'i': tag, 'j': tag+1,
                              'directions': [1], 'materials': [tag]} for tag in (1, 2)]
        graph['control'].update(node=3, dof=1)
        samples = [0., 1., 1., -1., 0.]
    graph['control'].update(analysis='transient' if transient else 'static', dt=.03)
    return ProjectConfig(solver=SolverConfig(SOLVER),
        material=MaterialTemplate('Deformation QA', 'uniaxialMaterial Elastic 1 100\nuniaxialMaterial Elastic 2 200'),
        loading=LoadingProtocol(source='samples', samples=samples), output_dir=output,
        modeling=ModelingConfig(topology='explicit', assembly=graph),
        material_mode='force_deformation', force_unit='kN', length_unit='mm')


@pytest.fixture(scope='session')
def app():
    application = QApplication.instance() or QApplication([])
    for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
    return application


@pytest.fixture
def window(app, tmp_path):
    widget = MainWindow(restore_last_project=False)
    widget._populate_from_config(config_for(tmp_path))
    widget.show(); widget._navigate_to(3); app.processEvents()
    yield widget
    if widget._thread is not None:
        widget.cancel_analysis(); wait_worker(widget)
    widget._project_dirty = False; widget.close(); widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait_worker(window):
    loop = QEventLoop(); timer = QTimer(); timer.setInterval(20)
    timer.timeout.connect(lambda: loop.quit() if window._thread is None else None)
    timeout = QTimer(); timeout.setSingleShot(True); timeout.timeout.connect(loop.quit)
    timer.start(); timeout.start(30000); loop.exec(); timer.stop(); timeout.stop()
    assert window._thread is None


def calculate(window):
    with patch('material_test.ui.fitting_workflow.QMessageBox.critical') as error:
        window.open_assembly_response(); wait_worker(window)
        error.assert_not_called()
    assert window.current_result is not None


def pixels(widget):
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), dtype=np.uint8).copy()


@pytest.mark.parametrize('transient', [False, True])
def test_all_node_histories_keep_equilibrium_samples_and_actual_time(tmp_path, transient):
    config = config_for(tmp_path, transient=transient)
    result = run_project(config, run_dir=tmp_path/'run')
    data = result.simulation.deformation
    assert data.node_tags == (1, 2, 3) and data.displacements.shape == (5, 3, 3)
    np.testing.assert_allclose(data.displacements[:, 0], 0., atol=1e-8)
    np.testing.assert_allclose(data.displacements[:, 1, 0], np.array(config.loading.samples)*2/3, atol=1e-8)
    np.testing.assert_allclose(data.displacements[:, 2, 0], config.loading.samples, atol=1e-8)
    np.testing.assert_allclose(data.displacements[:, :, 1:], 0., atol=1e-8)
    np.testing.assert_allclose(data.timeline, np.arange(5)*(.03 if transient else 1.))
    np.testing.assert_allclose(data.positions(3, 10), data.coordinates + 10*data.displacements[3, :, :2])
    assert data.displacements[1].tolist() == data.displacements[2].tolist()
    header = result.simulation.response_file.read_text().splitlines()[0].split(',')
    assert all(f'node_{tag}_u{dof}' in header for tag in (1, 2, 3) for dof in (1, 2, 3))
    assert not any(name.endswith(('_v1', '_a1')) for name in header)
    config.modeling.assembly['nodes'][1]['x'] = 9999.
    assert data.coordinates[1, 0] == data.model['nodes'][1]['x'] == 500.


def test_compact_fitting_runs_can_still_omit_deformation_channels(tmp_path):
    config = config_for(tmp_path)
    project = component_from_inputs(config.modeling.assembly, config.material, config.solver,
                                    config.loading.samples, tmp_path)
    _, columns, _ = write_component_tcl(project, tmp_path/'compact', compact=True)
    assert 'node_2_u1' not in columns
    result = run_component(project, scratch=True)
    with pytest.raises(ValueError, match='Missing nodal'):
        DeformationHistory.from_component(project, result)


def test_material_fitting_candidates_omit_unused_deformation_outputs(tmp_path):
    config = config_for(tmp_path)
    result = run_material_history(solver=config.solver, material=config.material,
        modeling=config.modeling, displacement_history=config.loading.samples,
        work_dir=tmp_path, record_deformation=False)
    assert result.deformation is None
    np.testing.assert_allclose(result.force, np.array(config.loading.samples)*200/3, atol=1e-8)
    assert 'node_2_u1' not in result.response_file.read_text().splitlines()[0]


def test_constrained_joint_records_independent_translation_and_rotation(tmp_path):
    project = make_plate_joint(tmp_path, SOLVER, specimen=True)
    history = project.data['loading']
    history.pop('path'); history.pop('step'); history['history'] = [0., 7.5, -7.5, 0.]
    project.data['channels'] = [c for c in project.data['channels'] if c['name'].startswith('actuator_')]
    result = run_component(project, scratch=True, record_displacements=True)
    data = DeformationHistory.from_component(project, result)
    lookup = {tag: i for i, tag in enumerate(data.node_tags)}
    column = data.displacements[:, lookup[101]]
    beam = data.displacements[:, lookup[102]]
    assert np.max(np.abs(beam[:, 2]-column[:, 2])) > 1e-4
    for retained, constrained, offset in ((101, 111, 150.), (102, 112, 150.), (101, 121, -150.)):
        a, b = data.displacements[:, lookup[retained]], data.displacements[:, lookup[constrained]]
        np.testing.assert_allclose(b[:, 0], a[:, 0]-offset*a[:, 2], atol=1e-8)
        np.testing.assert_allclose(b[:, 2], a[:, 2], atol=1e-8)


def test_pure_rotational_zero_length_changes_picture_without_fake_translation(app, tmp_path):
    config = config_for(tmp_path, rotation=True)
    data = run_project(config, run_dir=tmp_path/'run').simulation.deformation
    np.testing.assert_allclose(data.displacements[:, :, :2], 0., atol=1e-12)
    np.testing.assert_allclose(data.displacements[:, 1, 2], config.loading.samples, atol=1e-12)
    dialog = AssemblyDeformationDialog(); dialog.set_history(data); dialog.show(); app.processEvents()
    before = pixels(dialog.canvas.viewport())
    dialog.seek(1); app.processEvents()
    after = pixels(dialog.canvas.viewport())
    assert np.count_nonzero(before != after) > 100
    assert 'rad' in dialog.status.text() and 'M ' in dialog.status.text()
    assert 'kN·mm' in dialog.status.text()
    assert set(dialog.canvas.positions) == {1, 2}
    assert dialog.canvas.positions[1] == dialog.canvas.positions[2]
    assert 2 in dialog.canvas.rotation_items
    dialog.close(); dialog.deleteLater()


def test_integrated_dialog_playback_export_and_view_controls_do_not_edit_model(window, app, tmp_path):
    assert not window.assembly_deformation_button.isEnabled()
    calculate(window)
    assert window.assembly_deformation_button.isEnabled() and window.assembly_response_deformation.isEnabled()
    QTest.mouseClick(window.assembly_deformation_button, Qt.MouseButton.LeftButton)
    dialog = window.assembly_deformation_dialog; app.processEvents()
    assert dialog.isVisible() and dialog.history is window.current_result.simulation.deformation
    graph = deepcopy(window.topology_editor.graph); generation = window._generation
    start = pixels(dialog.canvas.viewport()); dialog.seek(1); app.processEvents()
    assert np.count_nonzero(start != pixels(dialog.canvas.viewport())) > 100
    dialog.node_selector.setCurrentIndex(dialog.node_selector.findData(2))
    assert '0.666667 mm' in dialog.node_values.text()
    app.processEvents()
    QTest.mouseClick(dialog.canvas.viewport(), Qt.MouseButton.LeftButton,
                    pos=dialog.canvas.mapFromScene(dialog.canvas.positions[3]))
    app.processEvents()
    assert dialog.node_selector.currentData() == 3
    dialog.amplification.setValue(80); dialog.undeformed.setChecked(False); dialog.node_labels.setChecked(False)
    diagram = tmp_path/'deformed.png'
    assert dialog.export_image(diagram) and not QImage(str(diagram)).isNull()
    assert not dialog.timer.isActive()
    dialog.seek(0); dialog.speed.setCurrentIndex(dialog.speed.findData(4.)); dialog.toggle_play()
    QTest.qWait(150)
    assert dialog.frame.value() == dialog.frame.maximum() and not dialog.timer.isActive()
    dialog.toggle_play(); assert dialog.timer.isActive()
    dialog.hide(); assert not dialog.timer.isActive()
    assert window.topology_editor.graph == graph and window._generation == generation
    assert not window._comparison_dirty


def test_input_edits_invalidate_animation_and_recalculation_restores_it(window):
    calculate(window); window.open_assembly_deformation()
    dialog = window.assembly_deformation_dialog; dialog.toggle_play()
    window.topology_editor.canvas.select('node', 2)
    window.topology_editor.node_x.setValue(550); window.topology_editor._node_properties()
    assert window._comparison_dirty and not dialog.valid and not dialog.timer.isActive()
    assert not window.assembly_deformation_button.isEnabled() and not dialog.export_button.isEnabled()
    assert not dialog.export_image(dialog.history.run_dir/'stale.png')
    assert dialog.history.coordinates[1, 0] == 500.
    calculate(window)
    assert dialog.valid and dialog.history.coordinates[1, 0] == 550.
    assert window.assembly_deformation_button.isEnabled()


def test_project_switch_clears_previous_geometry_and_stops_playback(window, tmp_path):
    calculate(window); window.open_assembly_deformation()
    dialog = window.assembly_deformation_dialog; dialog.toggle_play()
    window._populate_from_config(config_for(tmp_path/'other', rotation=True))
    assert dialog.history is None and not dialog.canvas.positions and not dialog.timer.isActive()
    assert window._assembly_deformation is None and not window.assembly_deformation_button.isEnabled()


def test_cancelled_recalculation_cannot_replay_previous_results(window):
    calculate(window); window.open_assembly_deformation()
    dialog = window.assembly_deformation_dialog; dialog.toggle_play()
    window.open_assembly_response(); window.cancel_analysis(); wait_worker(window)
    assert not dialog.valid and not dialog.timer.isActive()
    assert window._assembly_deformation is None and not window.assembly_deformation_button.isEnabled()
    assert '取消' in dialog.status.text()


def test_failed_calculation_stops_and_disables_animation(window):
    calculate(window); window.open_assembly_deformation()
    dialog = window.assembly_deformation_dialog; dialog.toggle_play()
    with patch('material_test.ui.fitting_workflow.QMessageBox.critical'):
        window._run_failed('OpenSees failed')
    assert not dialog.valid and not dialog.timer.isActive()
    assert '失败' in dialog.status.text() and not window.assembly_deformation_button.isEnabled()


def test_result_completed_after_input_change_is_not_available_for_playback(window, tmp_path):
    result = run_project(config_for(tmp_path), run_dir=tmp_path/'stale_run')
    window._run_generation = window._generation
    window._mark_comparison_dirty('加载已修改')
    window._run_finished(result)
    assert window._comparison_dirty and window._assembly_deformation is None
    assert not window.assembly_deformation_button.isEnabled()
