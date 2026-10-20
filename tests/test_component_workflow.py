"""Acceptance of explicit assembly within the single material-test workflow."""
from copy import deepcopy
import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPointF, QThread, QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QScrollArea

from material_test.domain import LoadingProtocol, MaterialTemplate, ModelingConfig, ParameterSpec, ProjectConfig, SolverConfig
from material_test.explicit_assembly import component_from_inputs, empty_assembly, validate_assembly
from material_test.io.project_config import load_project_config, save_project_config
from material_test.protocols import history_from_protocol
from material_test.ui.assembly_canvas import display_positions
from material_test.ui.assembly_editor import AssemblyEditor
from material_test.ui.main_window import MainWindow
from material_test.ui.project_session import LAST_PROJECT_KEY
from material_test.workflow import run_project

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT/'OS_terminal'/'OpenSees.exe'


def connection_graph():
    graph = empty_assembly()
    coordinates = [(0,0),(0,0),(0,150),(200,150),(0,-150),(200,-150),(-300,0),(500,0)]
    graph['nodes'] = [{'tag': tag, 'x': float(x), 'y': float(y), 'fix': [0,0,0]}
                      for tag, (x,y) in enumerate(coordinates, 1)]
    graph['nodes'][6]['fix'] = [1,1,1]
    graph['nodes'][7]['fix'] = [1,0,1]
    graph['elements'] = [{'tag': tag, 'type': 'elasticBeamColumn', 'i': i, 'j': j,
                          'A': 1000., 'E': 200000., 'Iz': 1e6}
                         for tag, (i,j) in enumerate([(1,3),(2,5),(1,4),(2,6),(7,1),(2,8)], 1)]
    graph['elements'].extend([
        {'tag': 7, 'type': 'twoNodeLink', 'i': 3, 'j': 4, 'materials': [1], 'directions': [1]},
        {'tag': 8, 'type': 'twoNodeLink', 'i': 5, 'j': 6, 'materials': [1], 'directions': [1]},
        {'tag': 9, 'type': 'zeroLength', 'i': 1, 'j': 2, 'materials': [2,2], 'directions': [1,2]},
    ])
    graph['control'].update(node=8, dof=2)
    return graph


def graph_config(output):
    graph = empty_assembly()
    graph['nodes'] = [{'tag':1,'x':0.,'y':0.,'fix':[1,1,1]},
                      {'tag':2,'x':1000.,'y':0.,'fix':[0,1,1]}]
    graph['elements'] = [{'tag':1,'name':'Spring','type':'twoNodeLink','i':1,'j':2,
                         'directions':[1],'materials':[1],'axis_auto':True}]
    graph['control']['node'] = 2
    return ProjectConfig(solver=SolverConfig(SOLVER),
                         material=MaterialTemplate('Assembly QA','uniaxialMaterial Elastic 1 100\nuniaxialMaterial Elastic 2 200'),
                         loading=LoadingProtocol(source='samples',samples=[0,.5,1,.5,0,-1,0]),
                         modeling=ModelingConfig(topology='explicit',assembly=graph),output_dir=output,
                         force_unit='kN',length_unit='mm',material_mode='force_deformation')


@pytest.fixture(scope='session')
def app():
    application = QApplication.instance() or QApplication([])
    for name in ('msyh.ttc','msyhbd.ttc','segoeui.ttf','consola.ttf'):
        font = Path('C:/Windows/Fonts')/name
        if font.exists(): QFontDatabase.addApplicationFont(str(font))
    return application


@pytest.fixture
def editor(app):
    widget = AssemblyEditor(); widget.resize(1080,760); widget.show(); app.processEvents()
    widget.set_material_options([(1,'Elastic'),(2,'Elastic')])
    yield widget
    widget.close(); widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


@pytest.fixture
def window(app,tmp_path):
    widget = MainWindow(); widget._populate_from_config(graph_config(tmp_path))
    widget.resize(1400,900); widget.show(); widget._navigate_to(3); app.processEvents()
    yield widget
    if widget._thread is not None:
        widget.cancel_analysis()
        wait_worker(widget)
    widget._project_dirty=False; widget.close(); widget.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def wait_worker(window):
    loop=QEventLoop(); timer=QTimer(); timer.setInterval(20)
    timer.timeout.connect(lambda: loop.quit() if window._thread is None else None)
    timeout=QTimer(); timeout.setSingleShot(True); timeout.timeout.connect(loop.quit)
    timer.start(); timeout.start(30000); loop.exec()
    timer.stop(); timeout.stop()
    assert window._thread is None


def point(editor,x,y):
    return editor.canvas.mapFromScene(QPointF(x,-y))


def click(editor,x,y):
    QTest.mouseClick(editor.canvas.viewport(),Qt.MouseButton.LeftButton,pos=point(editor,x,y))


def test_blank_initial_canvas_and_single_workflow(app):
    w=MainWindow()
    assert w.topology_editor.graph['nodes']==[]
    assert not hasattr(w,'model_preview_plot')
    assert not hasattr(w,'analysis_mode')
    assert not hasattr(w,'model_mode_tabs')
    assert w.pages.count()==5
    assert all(isinstance(w.pages.widget(index), QScrollArea) for index in range(w.pages.count()))
    assert all(w.pages.widget(index).horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
               for index in range(w.pages.count()))
    w._project_dirty=False; w.close()


def test_click_draw_snap_drag_undo_redo(editor):
    editor.draw_action.trigger(); click(editor,0,0); click(editor,300,0)
    editor.draw_action.trigger(); click(editor,300,0); click(editor,500,150)
    assert len(editor.graph['nodes'])==3
    assert [e['i'] for e in editor.graph['elements']]==[1,2]
    editor.select_action.trigger()
    viewport=editor.canvas.viewport()
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=point(editor,300,0))
    QTest.mouseMove(viewport,point(editor,350,100))
    QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=point(editor,350,100))
    assert (editor.node(2)['x'],editor.node(2)['y'])==(350.,100.)
    editor.undo(); assert (editor.node(2)['x'],editor.node(2)['y'])==(300.,0.)
    editor.redo(); assert editor.node(2)['y']==100.


def test_drag_draw_element(editor):
    editor.draw_action.trigger(); viewport=editor.canvas.viewport()
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=point(editor,0,0))
    QTest.mouseMove(viewport,point(editor,300,100))
    QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=point(editor,300,100))
    assert len(editor.graph['elements'])==1
    assert editor.node(2)['y']==100.


def test_properties_material_refresh_does_not_mutate_model(editor,tmp_path):
    config=graph_config(tmp_path); graph=config.modeling.assembly
    graph['elements'][0]={'tag':1,'type':'truss','i':1,'j':2,'A':300.,'material':1}
    editor.set_modeling(config.modeling,1); editor.canvas.select('element',1)
    before=deepcopy(editor.graph)
    editor.set_material_options([(1,'renamed Elastic'),(2,'new')])
    assert editor.graph==before
    assert editor.area_spin.isEnabled()
    editor.material_combo.setCurrentIndex(editor.material_combo.findData(2))
    assert editor.element(1)['material']==2
    editor.node_i.setCurrentIndex(0)
    assert editor.element(1)['i']==1
    editor.node_j.setCurrentIndex(editor.node_j.findData(1))
    assert editor.element(1)['j']==2


def test_node_support_and_control_selection(editor):
    editor.node_action.trigger(); click(editor,0,0); click(editor,200,0)
    editor.canvas.select('node',2)
    QTest.mouseClick(editor.assign_control_button,Qt.MouseButton.LeftButton)
    editor.control_dof.setCurrentIndex(editor.control_dof.findData(2))
    editor.support_preset.setCurrentIndex(5)
    assert editor.node(2)['fix']==[1,0,1]
    assert editor.graph['control']['node']==2
    assert editor.graph['control']['dof']==2
    assert 'UY' in [item.text() for item in editor.canvas.scene().items() if hasattr(item,'text')]


def test_assembly_element_types_use_opensees_names(editor):
    expected = ['twoNodeLink', 'zeroLength', 'truss', 'corotTruss', 'elasticBeamColumn']
    assert [editor.element_type.itemText(i) for i in range(editor.element_type.count())] == expected
    assert [editor.selected_type.itemText(i) for i in range(editor.selected_type.count())] == expected
    editor.set_modeling(ModelingConfig(assembly=connection_graph()), 1)
    rows = {editor.entity_list.topLevelItem(i).text(1) for i in range(editor.entity_list.topLevelItemCount())}
    assert 'twoNodeLink · Tag 1' in rows
    assert 'zeroLength · Tag 2, Tag 2' in rows
    assert 'elasticBeamColumn' in rows


def test_mixed_assembly_parameter_units_follow_assigned_element(window):
    window._populate_material(MaterialTemplate('Mixed','uniaxialMaterial Elastic 1 100\nuniaxialMaterial Elastic 2 {e2}',
                              parameters=[ParameterSpec('e2',200.,unit='stress/strain')]))
    parameter=window._material_parameters['e2']
    graph=window.topology_editor.graph
    graph['elements'].append({'tag':2,'type':'truss','i':1,'j':2,'A':20.,'material':2})
    assert window._parameter_response(parameter)=='stress_strain'
    assert window._parameter_unit(parameter)=='kN/mm²'
    graph['elements'][-1]={'tag':2,'type':'twoNodeLink','i':1,'j':2,'materials':[2],'directions':[3],'axis':[1.,0.]}
    assert window._parameter_response(parameter)=='moment_rotation'
    assert window._parameter_unit(parameter)=='kN*mm/rad'


def test_zero_length_pair_creation_and_coordinate_move(editor):
    editor.element_type.setCurrentIndex(editor.element_type.findData('zeroLength'))
    click(editor,0,0); click(editor,0,0)
    assert len(editor.graph['nodes'])==2
    assert editor.element(1)['i']!=editor.element(1)['j']
    editor.canvas.select('node',2)
    editor.node_x.setValue(150); editor.node_y.setValue(100); editor._node_properties()
    assert [(n['x'],n['y']) for n in editor.graph['nodes']]==[(150.,100.),(150.,100.)]


def test_connection_expansion_follows_branches_and_preserves_supports():
    graph = connection_graph(); before = deepcopy(graph)
    positions = display_positions(graph, expanded=True)
    assert positions[1].y() < 0 < positions[2].y()
    assert (positions[2]-positions[1]).manhattanLength() > 64
    for tag in range(3,9):
        node = graph['nodes'][tag-1]
        assert positions[tag] == QPointF(node['x'], -node['y'])
    assert graph == before


@pytest.mark.parametrize('count', [2,3,5])
def test_connection_expansion_handles_nodes_without_member_directions(count):
    graph = empty_assembly()
    graph['nodes'] = [{'tag': tag, 'x': 0., 'y': 0., 'fix': [0,0,0]} for tag in range(1,count+1)]
    positions = display_positions(graph, expanded=True)
    assert len({(p.x(),p.y()) for p in positions.values()}) == count
    assert all(positions[tag] == QPointF() for tag in positions) is False
    graph['nodes'][0]['fix'] = [1,1,1]
    anchored = display_positions(graph, expanded=True)
    assert anchored[1] == QPointF()
    assert all(anchored[tag] != anchored[1] for tag in range(2,count+1))


def test_expansion_toggle_is_view_only_and_can_collapse(editor):
    editor.set_modeling(ModelingConfig(assembly=connection_graph()),1)
    before = deepcopy(editor.graph); history = deepcopy(editor._history)
    changed = []; editor.changed.connect(lambda: changed.append(True))
    scale = editor.canvas.transform().m11()
    editor.canvas.select('element',9)
    editor.expand_check.setChecked(True)
    assert editor.canvas.positions[1] != editor.canvas.positions[2]
    assert editor.canvas.selection == {('element',9)}
    assert editor.graph == before and editor._history == history and not changed
    assert editor.canvas.transform().m11() == scale
    editor.expand_check.setChecked(False)
    assert editor.canvas.positions[1] == editor.canvas.positions[2] == QPointF()
    assert editor.graph == before and not changed


def test_expanded_nodes_can_be_selected_and_connected_independently(editor):
    editor.set_modeling(ModelingConfig(assembly=connection_graph()),1)
    editor.expand_check.setChecked(True)
    viewport = editor.canvas.viewport()
    for tag in (1,2):
        point = editor.canvas.mapFromScene(editor.canvas.positions[tag])
        assert editor.canvas.node_at(point)['tag'] == tag
        QTest.mouseClick(viewport,Qt.MouseButton.LeftButton,pos=point)
        assert editor.canvas.selection == {('node',tag)}
        assert editor.node_x.value() == editor.node_y.value() == 0.
    editor.element_type.setCurrentIndex(editor.element_type.findData('zeroLength'))
    for tag in (1,2):
        QTest.mouseClick(viewport,Qt.MouseButton.LeftButton,
                         pos=editor.canvas.mapFromScene(editor.canvas.positions[tag]))
    assert len(editor.graph['nodes']) == 8
    assert (editor.element(10)['i'],editor.element(10)['j']) == (1,2)


def test_node_hit_tolerance_stays_circular_and_handles_unrendered_nodes(editor):
    node = editor.create_node(0,0)
    pixel = point(editor,0,0)
    assert editor.canvas.node_at(pixel+QPointF(8,8).toPoint()) is node
    assert editor.canvas.node_at(pixel+QPointF(10,10).toPoint()) is None


@pytest.mark.parametrize('size', [(1460,900),(1100,700)])
def test_expanded_connection_labels_fit_at_desktop_and_compact_sizes(window,app,size):
    window.resize(*size)
    editor = window.topology_editor
    editor.set_modeling(ModelingConfig(assembly=connection_graph()),1)
    app.processEvents(); editor.canvas.fit_all()
    editor.expand_check.setChecked(True)
    canvas = editor.canvas
    assert all(canvas.viewport().rect().contains(canvas.mapFromScene(p)) for p in canvas.positions.values())
    texts = [item for item in canvas.scene().items() if hasattr(item,'text')]
    rectangles = [item.deviceTransform(canvas.viewportTransform()).mapRect(item.boundingRect()) for item in texts]
    for index, rectangle in enumerate(rectangles):
        assert not any(rectangle.intersects(other) for other in rectangles[index+1:])


def test_expanded_nodes_box_select_uses_display_positions(editor):
    editor.set_modeling(ModelingConfig(assembly=connection_graph()),1)
    editor.expand_check.setChecked(True)
    viewport = editor.canvas.viewport()
    radius = 9/editor.canvas.transform().m11()
    p = editor.canvas.positions[1]
    start = editor.canvas.mapFromScene(p-QPointF(radius,radius))
    end = editor.canvas.mapFromScene(p+QPointF(radius,radius))
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(viewport,end)
    QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=end)
    assert editor.canvas.selection == {('node',1)}


def test_expanded_node_drag_keeps_zero_length_pair_coincident(editor):
    editor.set_modeling(ModelingConfig(assembly=connection_graph()),1)
    editor.expand_check.setChecked(True)
    viewport = editor.canvas.viewport()
    p = editor.canvas.positions[2]
    start = editor.canvas.mapFromScene(p)
    end = editor.canvas.mapFromScene(p+QPointF(100,-50))
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=start)
    QTest.mouseMove(viewport,end)
    QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=end)
    assert [(editor.node(tag)['x'],editor.node(tag)['y']) for tag in (1,2)] == [(100.,50.),(100.,50.)]
    editor.undo()
    assert all(editor.node(tag)['x'] == editor.node(tag)['y'] == 0. for tag in (1,2))
    assert editor.expand_check.isChecked()


def test_expansion_does_not_invalidate_results_or_saved_graph(window,tmp_path):
    editor = window.topology_editor
    config = graph_config(tmp_path)
    config.modeling.assembly['nodes'][1]['x'] = 0.
    config.modeling.assembly['elements'][0]['type'] = 'zeroLength'
    window._populate_from_config(config)
    result = run_project(config,run_dir=tmp_path/'connection_result')
    window._run_generation = window._generation; window._run_finished(result)
    window._project_dirty = False
    generation = window._generation
    editor.expand_check.setChecked(True)
    assert window._generation == generation and not window._project_dirty
    assert not window._comparison_dirty and window.current_result is result
    saved = tmp_path/'expanded.yaml'
    save_project_config(window._comparison_config_from_ui(),saved)
    assert load_project_config(saved).modeling.assembly == config.modeling.assembly
    assert component_from_inputs(editor.graph,config.material,config.solver,history_from_protocol(config.loading),tmp_path).data['nodes'][1]['x'] == 0.


def test_delete_cascades_control_and_elements(editor,tmp_path):
    editor.set_modeling(graph_config(tmp_path).modeling,1)
    editor.canvas.select('node',2); editor.delete_selected()
    assert editor.graph['elements']==[]
    assert editor.graph['control']['node'] is None
    editor.undo(); assert editor.graph['control']['node']==2


def test_copy_selection_retains_geometry_materials_and_independent_tags(editor,tmp_path):
    editor.set_modeling(graph_config(tmp_path).modeling,1)
    editor.canvas.select('element',1); editor.duplicate_selection()
    assert len(editor.graph['nodes'])==4 and len(editor.graph['elements'])==2
    assert editor.element(2)['materials']==[1]
    assert editor.element(2)['i']==3 and editor.element(2)['j']==4
    assert editor.node(3)['x']==50.


def test_draft_and_ready_graph_yaml_roundtrip(tmp_path):
    config=graph_config(tmp_path)
    config.modeling.assembly['elements'][0]['materials']=[None]
    config.modeling.assembly['control']['node']=None
    config.modeling.validate(require_assigned=False)
    save_project_config(config,tmp_path/'draft.yaml')
    loaded=load_project_config(tmp_path/'draft.yaml')
    assert loaded.modeling.assembly==config.modeling.assembly
    with pytest.raises(ValueError,match='分配材料'):
        loaded.modeling.validate()


def test_sample_history_preserves_repeats_and_precision(tmp_path):
    config=graph_config(tmp_path)
    config.loading.samples=[0.,.12345678901234567,.12345678901234567,-.3,0.]
    save_project_config(config,tmp_path/'model.yaml')
    loaded=load_project_config(tmp_path/'model.yaml')
    np.testing.assert_array_equal(history_from_protocol(loaded.loading),config.loading.samples)


def test_save_and_reload_same_material_workflow(window,tmp_path):
    window.current_project_path=tmp_path/'model.yaml'
    assert window.save_project()
    loaded=load_project_config(window.current_project_path)
    before=deepcopy(window.topology_editor.graph)
    window.topology_editor.new_empty(); window._populate_from_config(loaded)
    assert window.topology_editor.graph==before
    assert not hasattr(window,'component_workbench')


def test_startup_restores_last_opened_project(window,tmp_path,session_settings):
    path = tmp_path/'last project.yaml'
    config = graph_config(tmp_path)
    config.modeling.assembly = connection_graph()
    save_project_config(config,path)
    window._project_dirty = False
    with patch('material_test.ui.project_session.QFileDialog.getOpenFileName',return_value=(str(path),'YAML')):
        window.load_project()
    session_settings.sync()
    assert session_settings.value(LAST_PROJECT_KEY) == str(path.resolve())
    assert window.close()
    restored = MainWindow()
    try:
        assert restored.current_project_path == path.resolve()
        assert restored.topology_editor.graph == config.modeling.assembly
        assert restored.loading_source_combo.currentData() == 'samples'
        assert restored.force_unit_combo.currentText() == 'kN'
        assert restored.material_command_table.rowCount() == 2
        assert not restored._project_dirty and not restored._loading_ui
        assert restored.current_result is None and restored._thread is None
        assert not restored._auto_timer.isActive()
    finally:
        restored._project_dirty = False; restored.close()


def test_save_as_remembers_new_path_and_saved_edits(window,tmp_path,session_settings):
    window.current_project_path = tmp_path/'first.yaml'
    assert window.save_project()
    window.material_name_edit.setText('Updated material')
    destination = tmp_path/'new project with spaces.yml'
    with patch('material_test.ui.project_session.QFileDialog.getSaveFileName',return_value=(str(destination),'YAML')):
        assert window.save_project_as()
    session_settings.sync()
    assert session_settings.value(LAST_PROJECT_KEY) == str(destination.resolve())
    restored = MainWindow()
    try:
        assert restored.current_project_path == destination.resolve()
        assert restored.material_name_edit.text() == 'Updated material'
        assert not restored._project_dirty
    finally:
        restored._project_dirty = False; restored.close()


@pytest.mark.parametrize('operation',['cancel','invalid'])
def test_cancelled_or_failed_open_keeps_last_project(window,tmp_path,session_settings,operation):
    previous = tmp_path/'previous.yaml'
    window.current_project_path = previous
    assert window.save_project()
    invalid = tmp_path/'invalid.yaml'
    invalid.write_text('solver: [',encoding='utf-8')
    selected = '' if operation == 'cancel' else str(invalid)
    with patch('material_test.ui.project_session.QFileDialog.getOpenFileName',return_value=(selected,'YAML')) as picker, \
         patch('material_test.ui.project_session.QMessageBox.critical') as error:
        window.load_project()
        assert picker.call_args.args[2] == str(previous.parent)
        assert error.call_count == (operation == 'invalid')
    session_settings.sync()
    assert session_settings.value(LAST_PROJECT_KEY) == str(previous.resolve())
    assert window.current_project_path == previous.resolve()


@pytest.mark.parametrize('failure',['missing','invalid_yaml','partial_population'])
def test_unavailable_last_project_starts_blank_without_modal_dialog(app,tmp_path,session_settings,failure):
    path = tmp_path/'unavailable.yaml'
    if failure == 'invalid_yaml':
        path.write_text('solver: [',encoding='utf-8')
    elif failure == 'partial_population':
        config = graph_config(tmp_path)
        config.modeling.assembly['control'] = {}
        save_project_config(config,path)
    session_settings.setValue(LAST_PROJECT_KEY,str(path.resolve())); session_settings.sync()
    with patch('material_test.ui.project_session.QMessageBox.critical') as error:
        restored = MainWindow()
        error.assert_not_called()
    try:
        assert restored.current_project_path is None
        assert not restored._project_dirty and not restored._loading_ui
        assert restored.topology_editor.graph['nodes'] == []
        assert restored.material_command_table.rowCount() == 0
        assert restored.force_unit_combo.currentText() == 'N'
        assert restored.loading_source_combo.currentData() == 'amplitudes'
        assert restored.pages.currentIndex() == 0
        assert str(path.resolve()) in restored.log_edit.toPlainText()
        session_settings.sync()
        assert not session_settings.contains(LAST_PROJECT_KEY)
    finally:
        restored._project_dirty = False; restored.close()


def test_failed_save_keeps_last_successful_path(window,tmp_path,session_settings):
    previous = tmp_path/'saved.yaml'
    window.current_project_path = previous
    assert window.save_project()
    window.current_project_path = tmp_path/'failed.yaml'
    with patch('material_test.ui.project_session.save_project_config',side_effect=PermissionError('read only')), \
         patch('material_test.ui.project_session.QMessageBox.critical') as error:
        assert not window.save_project()
        error.assert_called_once()
    session_settings.sync()
    assert session_settings.value(LAST_PROJECT_KEY) == str(previous.resolve())


def test_imported_component_restores_and_save_as_becomes_current_project(window,tmp_path,session_settings):
    from material_test.components2d.model import save_component
    from material_test.components2d.templates import make_plate_joint
    source = tmp_path/'joint.yaml'
    save_component(make_plate_joint(tmp_path,SOLVER,specimen=True),source)
    before = source.read_bytes()
    window.import_component_project(source)
    restored = MainWindow()
    try:
        assert restored.current_project_path is None and restored._project_dirty
        assert restored.pages.currentIndex() == 3
        assert restored.topology_editor.graph == window.topology_editor.graph
        destination = tmp_path/'material joint.yaml'
        with patch('material_test.ui.project_session.QFileDialog.getSaveFileName',return_value=(str(destination),'YAML')):
            assert restored.save_project_as()
        session_settings.sync()
        assert session_settings.value(LAST_PROJECT_KEY) == str(destination.resolve())
        assert restored.current_project_path == destination.resolve()
        assert load_project_config(destination).modeling.assembly == restored.topology_editor.graph
        assert source.read_bytes() == before
    finally:
        restored._project_dirty = False; restored.close()


def test_explicit_blank_start_does_not_change_remembered_project(app,tmp_path,session_settings):
    path = tmp_path/'remembered.yaml'
    save_project_config(graph_config(tmp_path),path)
    session_settings.setValue(LAST_PROJECT_KEY,str(path.resolve())); session_settings.sync()
    blank = MainWindow(restore_last_project=False)
    try:
        assert blank.current_project_path is None
        assert blank.material_command_table.rowCount() == 0
        session_settings.sync()
        assert session_settings.value(LAST_PROJECT_KEY) == str(path.resolve())
    finally:
        blank._project_dirty = False; blank.close()


def test_unreferenced_viscous_material_not_forced_into_static_solver(tmp_path):
    config=graph_config(tmp_path)
    config.material.command+='\nuniaxialMaterial Viscous 9 100 1'
    p=component_from_inputs(config.modeling.assembly,config.material,config.solver,[0,1],tmp_path)
    assert set(p.materials)=={1}


def test_control_edits_preserve_equilibrium_tolerance(app, tmp_path):
    config = graph_config(tmp_path)
    config.modeling.assembly['control']['equilibrium_tolerance'] = .001
    editor = AssemblyEditor()
    try:
        editor.set_modeling(config.modeling, config.material.tag)
        editor._control_changed()
        graph = editor.modeling(require_assigned=False).assembly
        project = component_from_inputs(graph, config.material, config.solver, [0., 1.], tmp_path)
        assert project.data['loading']['equilibrium_tolerance'] == .001
    finally:
        editor.close()


@pytest.mark.parametrize('history',[[],[0],[0,float('nan')],[[0,1],[1,0]]])
def test_invalid_histories_fail_before_solver(tmp_path,history):
    config=graph_config(tmp_path)
    with pytest.raises(ValueError,match='加载历程'):
        component_from_inputs(config.modeling.assembly,config.material,config.solver,history,tmp_path)


def test_loading_fixed_disconnected_and_invalid_constraints_fail(tmp_path):
    graph=graph_config(tmp_path).modeling.assembly
    graph['nodes'][1]['fix'][0]=1
    with pytest.raises(ValueError,match='被固定'): validate_assembly(graph)
    graph['nodes'][1]['fix'][0]=0
    graph['nodes'].append({'tag':3,'x':1,'y':1,'fix':[0,0,0]})
    with pytest.raises(ValueError,match='孤立'): validate_assembly(graph)
    graph['constraints']=[{'type':'equalDOF','retained':1,'constrained':999,'dofs':[1]}]
    with pytest.raises(ValueError,match='约束'): validate_assembly(graph)


@pytest.mark.skipif(not SOLVER.exists(),reason='OpenSees unavailable')
@pytest.mark.parametrize('arrangement,stiffness',[('single',100.),('parallel',300.),('series',200./3)])
def test_explicit_solver_single_parallel_series(tmp_path,arrangement,stiffness):
    config=graph_config(tmp_path); graph=config.modeling.assembly
    if arrangement=='parallel':
        e=deepcopy(graph['elements'][0]); e.update(tag=2,materials=[2]); graph['elements'].append(e)
    if arrangement=='series':
        graph['nodes'].append({'tag':3,'x':500.,'y':0.,'fix':[0,1,1]})
        graph['elements'][0]['j']=3
        graph['elements'].append({'tag':2,'type':'twoNodeLink','i':3,'j':2,'directions':[1],'materials':[2],'axis_auto':True})
    result=run_project(config,run_dir=tmp_path/'run')
    np.testing.assert_allclose(result.simulation.force,stiffness*result.simulation.displacement,atol=1e-7)
    assert 'model BasicBuilder -ndm 2 -ndf 3' in result.simulation.tcl_file.read_text()


@pytest.mark.skipif(not SOLVER.exists(),reason='OpenSees unavailable')
@pytest.mark.parametrize('dof',[1,2,3])
def test_global_direction_and_moment_rotation(tmp_path,dof):
    config=graph_config(tmp_path); graph=config.modeling.assembly
    graph['nodes'][1]['fix']=[int(i!=dof) for i in (1,2,3)]
    graph['elements'][0].update(directions=[dof],axis_auto=False,axis=[1.,0.])
    graph['control']['dof']=dof
    result=run_project(config,run_dir=tmp_path/'run')
    np.testing.assert_allclose(result.simulation.force,100*result.simulation.displacement,atol=1e-7)


@pytest.mark.skipif(not SOLVER.exists(),reason='OpenSees unavailable')
def test_actual_xy_coordinates_control_truss_stiffness(tmp_path):
    config=graph_config(tmp_path); graph=config.modeling.assembly
    config.material.command='uniaxialMaterial Elastic 1 200'
    graph['elements'][0]={'tag':1,'type':'truss','i':1,'j':2,'A':300.,'material':1}
    horizontal=run_project(config,run_dir=tmp_path/'horizontal')
    graph['nodes'][1]['y']=1000.
    diagonal=run_project(config,run_dir=tmp_path/'diagonal')
    np.testing.assert_allclose(horizontal.simulation.force,60*horizontal.simulation.displacement,atol=1e-7)
    np.testing.assert_allclose(diagonal.simulation.force,60/(2*np.sqrt(2))*diagonal.simulation.displacement,atol=1e-7)


@pytest.mark.skipif(not SOLVER.exists(),reason='OpenSees unavailable')
def test_explicit_transient_uses_exact_samples_and_declared_dt(tmp_path):
    from material_test.components2d.model import load_component
    config=graph_config(tmp_path)
    config.material.command='uniaxialMaterial Elastic 1 100'
    config.loading.samples=[0,.1,.2,.2,.1,0.,-.1,0.]
    config.modeling.assembly['control'].update(analysis='transient',dt=.03)
    result=run_project(config,run_dir=tmp_path/'transient')
    np.testing.assert_allclose(result.simulation.displacement,config.loading.samples,atol=1e-8)
    np.testing.assert_allclose(result.simulation.force,100*result.simulation.displacement,atol=1e-5)
    saved=load_component(result.simulation.tcl_file.parent/'project.yaml')
    assert saved.data['loading']['dt']==.03
    assert saved.data['units']=={'force':'kN','length':'mm','time':'s'}


@pytest.mark.skipif(not SOLVER.exists(),reason='OpenSees unavailable')
def test_curve_popup_uses_current_config_and_becomes_stale(window):
    assert not window.findChildren(QDialog)
    callback_threads=[]; finished=window._run_finished
    def record_result(result):
        callback_threads.append(QThread.currentThread()); finished(result)
    with patch('material_test.ui.fitting_workflow.QMessageBox.critical') as error,patch.object(window,'_run_finished',side_effect=record_result):
        QTest.mouseClick(window.calculate_model_preview_button,Qt.MouseButton.LeftButton)
        assert window.assembly_response_dialog.isVisible()
        wait_worker(window)
        error.assert_not_called()
    assert callback_threads==[QApplication.instance().thread()]
    result=window.current_result
    assert result is not None
    np.testing.assert_allclose(result.simulation.force,100*result.simulation.displacement,atol=1e-7)
    assert window.assembly_response_export.isEnabled()
    window.topology_editor.canvas.select('node',2)
    window.topology_editor.node_x.setValue(1100); window.topology_editor._node_properties()
    assert window._comparison_dirty
    assert not window.assembly_response_export.isEnabled()


def test_immediate_cancellation_shuts_down_worker_without_app_crash(window):
    window.open_assembly_response(); window.cancel_analysis()
    wait_worker(window)
    assert window.current_result is None
    assert '取消' in window.assembly_response_status.text()
