from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pytest

from material_test.cancellation import AnalysisCancelled
from material_test.components2d.model import load_component, save_component, ordered_history
from material_test.components2d.templates import make_translation, make_rotation, make_plate_joint
from material_test.components2d.solver import run_component, write_component_tcl, load_component_result
from material_test.components2d.fitting import fit_component
from material_test.components2d.digitization import AxisCalibration, digitize_curve, save_digitized, load_digitized
from material_test.components2d.comparison import image_metrics


SOLVER = Path(os.environ.get("MATERIAL_TEST_SOLVER", str(Path(__file__).resolve().parents[1]/"OS_terminal"/"OpenSees.exe")))


def test_noncollinear_graph_and_independent_coincident_nodes_roundtrip(tmp_path):
    project = make_plate_joint(tmp_path,SOLVER,specimen=True)
    save_component(project,tmp_path/"joint.yaml")
    loaded = load_component(tmp_path/"joint.yaml")
    assert loaded.data == project.data
    assert {n["y"] for n in loaded.data["nodes"]} == {0.,150.,-150.,-750.,750.}
    model, _, _ = write_component_tcl(loaded,tmp_path/"export")
    text = model.read_text()
    assert "rigidLink beam 101 111" in text
    assert "element elasticBeamColumn 303" in text
    assert "element zeroLength 203 101 102 -mat 21 22 -dir 1 2 " in text


@pytest.mark.parametrize("mutation,error",[
    (lambda p:p.data["nodes"][3].update(x=1.),"endpoints must coincide"),
    (lambda p:p.data["materials"][0].update(response="stress_strain"),"dimension mismatch"),
    (lambda p:p.data["constraints"].append({"type":"rigidLink","retained":111,"constrained":112}),"multiply constrained"),
    (lambda p:p.data["constraints"].append({"type":"rigidLink","retained":111,"constrained":999}),"Invalid"),
    (lambda p:p.data["loading"].update(node=111),"independent node"),
    (lambda p:p.data["materials"][0].update(command="uniaxialMaterial Elastic {tag} {k}\nnode 999 0 0"),"not model scripts"),
])
def test_invalid_graph_rejected(tmp_path,mutation,error):
    project = make_plate_joint(tmp_path,SOLVER); mutation(project)
    with pytest.raises(ValueError,match=error): project.validate()


def test_constraint_chain_rejected(tmp_path):
    p = make_plate_joint(tmp_path,SOLVER)
    p.data["constraints"][2]["retained"] = 111
    with pytest.raises(ValueError,match="chains"): p.validate()


def test_general_ports_groups_and_relocated_references(tmp_path):
    p = make_plate_joint(tmp_path,SOLVER,specimen=True)
    p.data['fitting'] = {'observations':[{'kind':'history','path':'measured.csv','x':'theta','y':'moment'}]}
    destination = tmp_path/'relocated'/'project.yaml'
    save_component(p,destination)
    loaded = load_component(destination)
    assert loaded.output_root == p.output_root
    assert Path(loaded.data['fitting']['observations'][0]['path']) == tmp_path/'measured.csv'
    assert loaded.data['ports']['column']['node'] == 101
    assert {group['role'] for group in loaded.data['groups']} == {'component','fixture'}
    loaded.data['ports']['column']['node'] = 999
    with pytest.raises(ValueError,match='ports'): loaded.validate()


@pytest.mark.skipif(not SOLVER.exists(),reason='OpenSees executable unavailable')
def test_diagonal_truss_uses_physical_xy_length_and_area(tmp_path):
    from material_test.components2d.templates import elastic,element_channel
    p = make_translation(tmp_path,SOLVER)
    p.data['nodes'][1].update(x=1000.,y=1000.)
    p.data['materials'] = [elastic(1,200.,response='stress_strain')]
    p.data['elements'] = [{'tag':1,'type':'truss','i':1,'j':2,'A':300.,'material':1}]
    p.data['channels'][1] = element_channel('force',1,'axialForce')
    r = run_component(p)
    length = np.sqrt(2)*1000
    expected_axial = 200*300/length*r.channel('displacement')/np.sqrt(2)
    np.testing.assert_allclose(r.channel('force'),expected_axial,atol=1e-8)
    np.testing.assert_allclose(r.channel('load_multiplier'),expected_axial/np.sqrt(2),atol=1e-8)


@pytest.mark.skipif(not SOLVER.exists(), reason='OpenSees executable unavailable')
@pytest.mark.parametrize('element_type', ['truss', 'corotTruss'])
def test_truss_deformation_and_local_force_in_compact_run(tmp_path, element_type):
    from material_test.components2d.templates import elastic, element_channel
    p = make_translation(tmp_path, SOLVER)
    p.data['nodes'][1].update(x=1000., y=1000.)
    p.data['materials'] = [elastic(1, 200., response='stress_strain')]
    p.data['elements'] = [{'tag': 1, 'type': element_type, 'i': 1, 'j': 2, 'A': 300., 'material': 1}]
    p.data['channels'] += [element_channel('extension', 1, 'basicDeformation'),
                           element_channel('axial', 1, 'basicForce'),
                           element_channel('local_i', 1, 'localForce', 0),
                           element_channel('local_j', 1, 'localForce', 3)]
    r = run_component(p, scratch=True)
    u = r.channel('displacement')
    length = np.sqrt(2.) * 1000.
    extension = np.hypot(1000. + u, 1000.) - length if element_type == 'corotTruss' else u / np.sqrt(2.)
    np.testing.assert_allclose(r.channel('extension'), extension, atol=1e-9)
    np.testing.assert_allclose(r.channel('axial'), 200. * 300. / length * extension, atol=1e-8)
    np.testing.assert_allclose(r.channel('local_i'), -r.channel('axial'), atol=1e-8)
    np.testing.assert_allclose(r.channel('local_j'), r.channel('axial'), atol=1e-8)


def test_scaled_parameter_binding(tmp_path):
    from material_test.components2d.fitting import apply_parameters
    p = make_translation(tmp_path,SOLVER)
    specs = [{'targets':[{'material':1,'parameter':'k','factor':2.,'offset':10.}]}]
    fitted = apply_parameters(p,specs,[70.])
    assert fitted.materials[1].parameter_values['k'] == 150.
    assert p.materials[1].parameter_values['k'] == 100.


def test_dynamic_material_cannot_use_static_test(tmp_path):
    p = make_translation(tmp_path,SOLVER,viscous=True)
    p.data["loading"]["analysis"] = "static"
    with pytest.raises(ValueError,match="physical time"): p.validate()


def test_history_keeps_reversals_and_exact_amplitudes():
    np.testing.assert_allclose(ordered_history({"path":[0,1,-1,0],"step":.5}),[0,.5,1,.5,0,-.5,-1,-.5,0])
    with pytest.raises(ValueError,match="begin at zero"): ordered_history({"history":[1,2]})


@pytest.mark.parametrize('value', [0., -1., float('nan'), float('inf')])
def test_invalid_equilibrium_tolerance_rejected(tmp_path, value):
    project = make_translation(tmp_path, SOLVER)
    project.data['loading']['equilibrium_tolerance'] = value
    with pytest.raises(ValueError, match='equilibrium_tolerance'):
        project.validate()


def test_static_equilibrium_tolerance_roundtrip_and_transient_isolation(tmp_path):
    project = make_translation(tmp_path, SOLVER)
    project.data['loading']['equilibrium_tolerance'] = .001
    save_component(project, tmp_path / 'project.yaml')
    loaded = load_component(tmp_path / 'project.yaml')
    model, _, _ = write_component_tcl(loaded, tmp_path / 'static')
    assert 'test NormUnbalance 0.001 80' in model.read_text()
    loaded.data['loading'].pop('equilibrium_tolerance')
    model, _, _ = write_component_tcl(loaded, tmp_path / 'default')
    assert 'test NormUnbalance 1e-06 80' in model.read_text()
    loaded.data['loading'].update(analysis='transient', equilibrium_tolerance=.001)
    model, _, _ = write_component_tcl(loaded, tmp_path / 'transient')
    assert 'test NormDispIncr 1.0e-9 80' in model.read_text()


@pytest.mark.skipif(not SOLVER.exists(),reason="OpenSees executable unavailable")
@pytest.mark.parametrize("direction",[1,2,3])
def test_translational_and_rotational_elastic_laws(tmp_path,direction):
    p = make_rotation(tmp_path,SOLVER) if direction == 3 else make_translation(tmp_path,SOLVER,direction=direction)
    r = run_component(p)
    x,y = ("rotation","moment") if direction == 3 else ("displacement","force")
    np.testing.assert_allclose(r.channel(y),100*r.channel(x),atol=1e-7)
    np.testing.assert_allclose(r.channel("input"),ordered_history(p.data["loading"]),atol=1e-8)
    with pytest.raises(FileExistsError): run_component(p,run_dir=r.run_dir)
    loaded = load_component_result(p,r.run_dir)
    np.testing.assert_allclose(loaded.values,r.values)
    p.data['materials'][0]['parameters'][0]['value'] = 101.
    with pytest.raises(ValueError,match='stale'): load_component_result(p,r.run_dir)


@pytest.mark.skipif(not SOLVER.exists(),reason="OpenSees executable unavailable")
def test_eccentric_joint_stiffness_slip_and_virtual_work(tmp_path):
    p = make_plate_joint(tmp_path,SOLVER,h_upper=120.,h_lower=180.,plate_k=50.)
    r = run_component(p)
    theta = r.channel("theta")
    slip = r.channel("node_102_u1")-r.channel("node_101_u1")
    np.testing.assert_allclose(r.channel("upper_deformation"),slip-120*theta,atol=1e-9)
    np.testing.assert_allclose(r.channel("lower_deformation"),slip+180*theta,atol=1e-9)
    coupling = 50*(-120+180)
    expected_k = 50*(120**2+180**2)-coupling**2/(10000+100.)
    np.testing.assert_allclose(r.channel("moment"),expected_k*theta,atol=1e-7)
    np.testing.assert_allclose(r.channel("pin_x")+r.channel("upper_force")+r.channel("lower_force"),0,atol=1e-8)
    a,b = 0,25
    u = r.channel("upper_deformation"); l = r.channel("lower_deformation")
    component_energy = .5*50*(u[b]**2-u[a]**2+l[b]**2-l[a]**2)+.5*10000*(slip[b]**2-slip[a]**2)
    rotation_energy = .5*expected_k*(theta[b]**2-theta[a]**2)
    assert component_energy == pytest.approx(rotation_energy,abs=1e-7)


@pytest.mark.skipif(not SOLVER.exists(),reason="OpenSees executable unavailable")
def test_complete_specimen_and_viscous_force_velocity(tmp_path):
    p = make_plate_joint(tmp_path,SOLVER,specimen=True)
    r = run_component(p)
    assert len(r.values) == len(ordered_history(p.data["loading"]))
    assert max(abs(r.channel("actuator_force"))) > 1.
    assert max(abs(r.channel("pin_y"))) > 1.
    dynamic = make_translation(tmp_path,SOLVER,viscous=True)
    v = run_component(dynamic)
    np.testing.assert_allclose(v.channel("force"),10*v.channel("velocity"),atol=1e-5)
    assert v.channel("time")[-1] == pytest.approx(.8)


@pytest.mark.skipif(not SOLVER.exists(),reason="OpenSees executable unavailable")
def test_fit_ordered_response_recovers_known_material(tmp_path):
    p = make_translation(tmp_path,SOLVER)
    history = ordered_history(p.data["loading"])
    np.savetxt(tmp_path/"observed.csv",np.column_stack((history,137*history)),delimiter=",",comments="",header="displacement,force")
    p.data["fitting"] = {"parameters":[{"name":"k","min":50.,"max":200.,"targets":[{"material":1,"parameter":"k"}]}],
                         "observations":[{"kind":"history","path":"observed.csv","x":"displacement","y":"force"}],
                         "max_evaluations":60,"population_size":6,"seed":42}
    result = fit_component(p)
    assert result.project.materials[1].parameter_values["k"] == pytest.approx(137.,abs=.2)
    assert p.materials[1].parameter_values["k"] == 100.
    assert result.objective < .001
    assert (result.fit_dir/"verified_best"/"result.json").exists()


def test_cancelled_before_solver_and_output_escape(tmp_path):
    p = make_translation(tmp_path,SOLVER)
    with pytest.raises(AnalysisCancelled): run_component(p,cancel_check=lambda:True)
    if SOLVER.exists():
        with pytest.raises(ValueError,match="stay inside"): run_component(p,run_dir=tmp_path.parent/"outside")


@pytest.mark.skipif(not SOLVER.exists(),reason="OpenSees executable unavailable")
def test_legacy_material_runner_in_unicode_project(tmp_path):
    from material_test.domain import MaterialTemplate,ParameterSpec,SolverConfig
    from material_test.opensees.runner import run_material_history
    material = MaterialTemplate('Elastic','uniaxialMaterial Elastic {tag} {k}',parameters=[ParameterSpec('k',100.)])
    result = run_material_history(solver=SolverConfig(SOLVER),material=material,
                                  displacement_history=np.array([0.,1.,-1.,0.]),work_dir=tmp_path/'unicode_\u8282\u70b9',material_mode='force_deformation')
    np.testing.assert_allclose(result.force,100*result.displacement,atol=1e-8)


def test_image_points_remain_unordered_and_shape_metric(tmp_path):
    from PIL import Image,ImageDraw
    image = Image.new("L",(120,120),255)
    ImageDraw.Draw(image).line([(10,100),(100,10)],fill=0,width=1)
    path = tmp_path/"source.png"; image.save(path)
    curve = digitize_curve(path,AxisCalibration("test",(0,0,110,110),(0,11),(0,11)))
    save_digitized(curve,tmp_path,path)
    loaded = load_digitized(tmp_path/"test_metadata.json")
    np.testing.assert_allclose(loaded.points,curve.points)
    assert json.loads((tmp_path/"test_metadata.json").read_text())["time_history_recovered"] is False
    score = image_metrics(curve,[1,10],[1,10])
    assert score["symmetric_distance_pixels"] < .1
    assert score["coverage"] == 1.
    assert score["experimental_sequence_rmse"] is None


def test_component_window_roundtrip_and_editing(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase,QFont
    from material_test.ui.component_window import ComponentWindow
    app = QApplication.instance() or QApplication([])
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if font.exists(): QFontDatabase.addApplicationFont(str(font)); app.setFont(QFont("Microsoft YaHei UI",9))
    window = ComponentWindow(tmp_path,SOLVER)
    window.show(); app.processEvents()
    project = window._collect()
    assert len(project.data["nodes"]) == 9
    assert len(project.data["elements"]) == 6
    window.nodes.item(2,3).setText("170")
    window.nodes.item(3,3).setText("170")
    assert window._collect().data["nodes"][2]["y"] == 170.
    window._dirty = False; window.close()


@pytest.mark.skipif(not SOLVER.exists(), reason='OpenSees executable unavailable')
def test_component_window_save_preserves_restorable_results(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from material_test.ui.component_window import ComponentWindow
    from material_test.components2d.solver import input_signature
    app = QApplication.instance() or QApplication([])
    p = make_plate_joint(tmp_path, SOLVER, specimen=True)
    r = run_component(p)
    p.data['last_result'] = str(r.run_dir)
    path = tmp_path / 'saved.yaml'
    save_component(p, path)
    window = ComponentWindow(tmp_path, SOLVER, path)
    app.processEvents()
    assert window.result is not None
    assert input_signature(window._collect()) == input_signature(p)
    assert window.save_project()
    np.testing.assert_allclose(load_component_result(load_component(path), r.run_dir).values, r.values)
    window.parameters.item(0, 1).setText('51.')
    assert window.result is None
    with pytest.raises(ValueError, match='stale'):
        load_component_result(window._collect(), r.run_dir)
    window._dirty = False
    window.close()


def test_component_canvas_labels_do_not_overlap_at_compact_size(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QGraphicsSimpleTextItem
    from PySide6.QtGui import QFontDatabase, QFont
    from material_test.ui.component_canvas import ComponentCanvas
    app = QApplication.instance() or QApplication([])
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont('Microsoft YaHei UI', 9))
    canvas = ComponentCanvas()
    canvas.resize(400, 300)
    canvas.show()
    canvas.set_project(make_plate_joint(tmp_path, SOLVER, specimen=True))
    app.processEvents()
    canvas.fit_all()
    boxes = [item.deviceTransform(canvas.viewportTransform()).mapRect(item.boundingRect())
             for item in canvas.scene().items() if isinstance(item, QGraphicsSimpleTextItem)]
    assert len(boxes) == 15
    assert all(not a.intersects(b) for index, a in enumerate(boxes) for b in boxes[index + 1:])
    canvas.close()
