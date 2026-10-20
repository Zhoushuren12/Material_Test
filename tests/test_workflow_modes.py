from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from threading import Event
from unittest.mock import patch
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

from material_test.domain import (MaterialTemplate, ParameterSpec, SolverConfig, ProjectConfig,
                                 LoadingProtocol, ModelingConfig, TopologyNode, FitConfig, FitParameterSpec)
from material_test.brace_export import build_brace_package, export_constitutive
from material_test.workflow import run_project
from material_test.protocols import history_from_protocol
from material_test.io.project_config import save_project_config, load_project_config
from material_test.io.experiment_data import load_experiment_data
from material_test.io.material_library import load_material_library
from material_test.material_commands import validate_solver_materials
from material_test.cancellation import AnalysisCancelled
from material_test.fitting import fit_project
from material_test.ui.main_window import MainWindow
from material_test.ui.material_library_dialog import MaterialLibraryDialog
from material_test.ui.preview_jobs import PREVIEW_CACHE
from material_test.material_preview import MaterialPreviewCurve

ROOT = Path(__file__).resolve().parents[1]


def elastic_config(output):
    return ProjectConfig(
        solver=SolverConfig(ROOT / 'OS_terminal' / 'OpenSees.exe'),
        material=MaterialTemplate('Elastic', 'uniaxialMaterial Elastic {tag} {k}', parameters=[ParameterSpec('k', 120.)]),
        loading=LoadingProtocol(amplitudes=[0., 1., -1., 0.], du=.5, source='amplitudes'),
        output_dir=Path(output), force_unit='kN', length_unit='mm', material_mode='force_deformation')


class WorkflowModeTests(unittest.TestCase):
    def test_explicit_amplitudes_ignore_inactive_missing_file(self):
        p = LoadingProtocol(amplitudes=[0, 1, 0], du=.5, history_path=Path('does-not-exist.csv'), source='amplitudes')
        np.testing.assert_allclose(history_from_protocol(p), [0, .5, 1, .5, 0])

    def test_missing_selected_loading_file_is_not_silently_replaced(self):
        with self.assertRaises(ValueError):
            history_from_protocol(LoadingProtocol(amplitudes=[0, 1], du=.1, source='external'))

    def test_signed_conversion_applies_to_experiment_and_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'raw.csv'; path.write_text('u,f\n0,0\n1,2\n-1,-2\n')
            experiment = load_experiment_data(path, displacement_column='u', force_column='f', displacement_scale=-1000, force_scale=-.001)
            history = history_from_protocol(LoadingProtocol(source='experiment', history_path=path, history_column='u', history_scale=-1000))
            np.testing.assert_allclose(history, experiment.displacement)
            np.testing.assert_allclose(experiment.force, [0, -.002, .002])
            with self.assertRaises(ValueError):
                load_experiment_data(path, displacement_column='u', force_column='f', force_scale=0)

    def test_project_roundtrip_preserves_units_modes_source_and_scales(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = elastic_config(tmp)
            config.experiment_displacement_scale = -1000
            config.experiment_force_scale = .001
            config.loading.source = 'external'; config.loading.history_path = Path(tmp) / 'history.csv'
            config.loading.history_column = 'u'; config.loading.history_scale = -2
            path = Path(tmp) / 'project.yaml'; save_project_config(config, path)
            loaded = load_project_config(path)
            self.assertEqual((loaded.force_unit, loaded.length_unit, loaded.material_mode), ('kN', 'mm', 'force_deformation'))
            self.assertEqual(loaded.loading.source, 'external')
            self.assertEqual(loaded.loading.history_scale, -2)
            self.assertEqual(loaded.experiment_displacement_scale, -1000)
            self.assertEqual(loaded.experiment_force_scale, .001)

    def test_steel02_error_names_invalid_parameter(self):
        material = MaterialTemplate('bad', 'uniaxialMaterial Steel02 2 369238.427 92060.251 18 0.925 0.15 0')
        with self.assertRaisesRegex(ValueError, 'Steel02 Tag 2.*cR2'):
            validate_solver_materials(material)

    def test_fitting_can_cancel_before_first_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = elastic_config(tmp)
            path = Path(tmp) / 'target.csv'; path.write_text('u,f\n0,0\n1,120\n-1,-120\n0,0\n')
            config.experiment_path = path; config.displacement_column='u'; config.force_column='f'
            config.fitting = FitConfig([FitParameterSpec('k', 60, 240)], max_evaluations=8, local_refine=False)
            with self.assertRaises(AnalysisCancelled):
                fit_project(config, cancel_check=lambda: True)
            self.assertEqual(config.material.parameter_values['k'], 120)

    def test_cancellation_terminates_active_solver_process(self):
        from material_test.opensees.runner import run_material_history
        with tempfile.TemporaryDirectory() as tmp:
            config = elastic_config(tmp)
            flags = iter([False, True])
            with patch('material_test.opensees.runner.subprocess.Popen') as process:
                with self.assertRaises(AnalysisCancelled):
                    run_material_history(solver=config.solver, material=config.material,
                        displacement_history=np.array([0, 1]), work_dir=Path(tmp), cancel_check=lambda: next(flags))
                process.return_value.kill.assert_called_once()
                process.return_value.communicate.assert_called_once()

    @unittest.skipUnless((ROOT / 'OS_terminal' / 'OpenSees.exe').exists(), 'solver unavailable')
    def test_numerical_only_run_publishes_and_downstream_accepts_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = elastic_config(tmp)
            result = run_project(config, run_dir=Path(tmp) / 'run')
            np.testing.assert_allclose(result.simulation.force, result.simulation.displacement * 120, atol=1e-8)
            package = build_brace_package(config, result_dir=result.run_dir)
            self.assertEqual(package['evidence']['source_type'], 'manual-assumption')
            self.assertIsNone(package['evidence']['experiment_path'])
            self.assertIsNone(package['evidence']['experiment_sha256'])
            self.assertEqual([curve['kind'] for curve in package['curves']], ['numerical'])
            self.assertEqual(package['units'], {'force':'kN', 'displacement':'mm'})
            structural_root = ROOT.parents[1] / 'Structural analysis'
            if structural_root.exists():
                sys.path.insert(0, str(structural_root))
                try:
                    from structure_app.services.brace_package import validate_brace_package
                    validate_brace_package(package)
                finally:
                    sys.path.remove(str(structural_root))
            config.force_unit = 'N'
            with self.assertRaisesRegex(ValueError, '已改变'):
                build_brace_package(config, result_dir=result.run_dir)

    @unittest.skipUnless((ROOT / 'OS_terminal' / 'OpenSees.exe').exists(), 'solver unavailable')
    def test_direct_input_series_does_not_rescale_by_length_weights(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = elastic_config(tmp)
            config.material = MaterialTemplate('series', 'uniaxialMaterial Elastic 1 120\nuniaxialMaterial Elastic 2 240', tag=1)
            config.modeling = ModelingConfig(root=TopologyNode.group('series', [
                TopologyNode.material(1, area=10, length_weight=1), TopologyNode.material(2, area=20, length_weight=3)]))
            result = run_project(config, run_dir=Path(tmp) / 'run')
            np.testing.assert_allclose(result.simulation.force, result.simulation.displacement * 80, atol=1e-8)
            self.assertIn('twoNodeLink', result.simulation.tcl_file.read_text())
            contract = export_constitutive(config)
            self.assertEqual([c['parameters']['k'] for c in contract['children']], [120, 240])


class WorkflowUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = MainWindow()
        self.w.output_dir_edit.setText(self.tmp.name)
        self.w._populate_material(load_material_library().get('elastic').instantiate(tag=1))
        self.w.topology_editor.set_root(TopologyNode.material(1))
        self.w._sync_fit_table()

    def tearDown(self):
        self.w._project_dirty = False
        self.w.close()
        self.tmp.cleanup()

    def test_ctrl_s_saves_existing_path_without_save_as_prompt(self):
        path=Path(self.tmp.name)/'project.yaml'; self.w.current_project_path=path
        self.w.param_table.cellWidget(0,2).setValue(321)
        self.assertIn('*', self.w.windowTitle())
        with patch('material_test.ui.project_session.QFileDialog.getSaveFileName') as picker:
            self.assertTrue(self.w.save_project()); picker.assert_not_called()
        self.assertNotIn('*', self.w.windowTitle())
        self.assertEqual(load_project_config(path).material.parameter_values['m1_E'], 321)

    def test_close_cancel_keeps_dirty_project_open(self):
        self.w.show(); self.w._mark_project_dirty()
        with patch('material_test.ui.project_session.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(self.w.close())
        self.assertTrue(self.w._project_dirty)

    def test_open_cancel_does_not_show_file_picker(self):
        self.w._mark_project_dirty()
        with patch('material_test.ui.project_session.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel), patch('material_test.ui.project_session.QFileDialog.getOpenFileName') as picker:
            self.w.load_project(); picker.assert_not_called()

    def test_main_open_routes_planar_model_into_analysis_workflow(self):
        from material_test.components2d.model import save_component
        from material_test.components2d.templates import make_plate_joint
        project = make_plate_joint(self.tmp.name, ROOT / 'OS_terminal' / 'OpenSees.exe', specimen=True)
        path = Path(self.tmp.name) / 'joint.yaml'
        save_component(project, path)
        self.w._project_dirty = False
        with patch('material_test.ui.project_session.QFileDialog.getOpenFileName', return_value=(str(path), 'YAML')), \
             patch('material_test.ui.project_session.load_project_config') as legacy_loader, \
             patch('material_test.ui.project_session.QMessageBox.critical') as error, \
             patch('material_test.ui.main_window.QFileDialog.getExistingDirectory') as directory:
            self.w.load_project()
            legacy_loader.assert_not_called()
            directory.assert_not_called()
            error.assert_not_called()
        self.w.show()
        self.app.processEvents()
        graph = self.w.topology_editor.graph
        self.assertEqual(len(graph['nodes']),9)
        self.assertEqual(len(graph['elements']),6)
        self.assertEqual(self.w.pages.currentIndex(),3)
        self.assertFalse(hasattr(self.w,'component_workbench'))
        self.assertIsNone(self.w.current_project_path)
        self.assertTrue(self.w._project_dirty)
        np.testing.assert_array_equal(history_from_protocol(self.w._loading_protocol_from_ui()),
                                      history_from_protocol(self.w._config_from_ui().loading))

    def test_main_open_still_loads_material_project(self):
        path = Path(self.tmp.name) / 'material.yaml'
        save_project_config(elastic_config(self.tmp.name), path)
        self.w._project_dirty = False
        with patch('material_test.ui.project_session.QFileDialog.getOpenFileName', return_value=(str(path), 'YAML')), \
             patch('material_test.ui.project_session.QMessageBox.critical') as error, \
             patch.object(self.w, 'import_component_project') as component:
            self.w.load_project()
            error.assert_not_called()
            component.assert_not_called()
        self.assertEqual(self.w.current_project_path, path.resolve())
        self.assertEqual(self.w._material_from_ui().parameter_values['k'], 120.)

    def test_invalid_planar_model_reports_its_validation_error(self):
        path = Path(self.tmp.name) / 'invalid_joint.yaml'
        path.write_text('schema: material-test/component-2d/v1\nunits: {force: kN, length: mm}\nnodes: []\n', encoding='utf-8')
        self.w._project_dirty = False
        with patch('material_test.ui.project_session.QFileDialog.getOpenFileName', return_value=(str(path), 'YAML')), \
             patch('material_test.ui.project_session.QMessageBox.critical') as error:
            self.w.load_project()
        error.assert_called_once()
        self.assertIn('At least two nodes', error.call_args.args[2])
        self.assertNotIn('material_template', error.call_args.args[2])

    def test_replacement_confirmation_lists_lost_assignments(self):
        replacement=load_material_library().get('elastic').instantiate(tag=2)
        with patch('material_test.ui.project_session.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(self.w._confirm_material_replacement(replacement))
        self.assertIn('E01 · Tag 1', question.call_args.args[2])
        self.assertEqual(self.w._material_commands[0].tag,1)

    def test_parameters_sync_and_rendered_commands_update(self):
        self.w.param_table.cellWidget(0,2).setValue(456)
        self.assertEqual(self.w.comparison_parameter_table.cellWidget(0,2).value(),456)
        self.assertIn('456',self.w.material_command_table.item(0,2).text())
        self.assertNotIn('{',self.w.material_command_table.item(0,2).text())

    def test_replacing_material_does_not_commit_previous_parameter_widgets(self):
        previous = MaterialTemplate('previous', 'uniaxialMaterial Elastic 1 {shared}',
            parameters=[ParameterSpec('shared', 100., min=0., max=200.)])
        replacement = MaterialTemplate('replacement', 'uniaxialMaterial Elastic 1 {shared}',
            parameters=[ParameterSpec('shared', 5., min=0., max=10.)])
        self.w._populate_material(previous)
        self.w._populate_material(replacement)
        self.assertEqual(self.w._material_from_ui().parameter_values['shared'], 5.)
        self.assertEqual(self.w.param_table.cellWidget(0,2).value(), 5.)

    def test_auto_run_debounces_parameter_changes(self):
        with patch.object(self.w,'start_run') as run:
            self.w.auto_recalculate_check.setChecked(True)
            self.w._set_material_parameter_value('m1_E',121)
            self.w._set_material_parameter_value('m1_E',122)
            self.assertTrue(self.w._auto_timer.isActive())
            self.assertEqual(self.w._auto_timer.interval(),400)
            self.assertEqual(run.call_count,0)
            QTest.qWait(480)
            self.assertEqual(run.call_count,1)

    def test_auto_run_turning_off_cancels_pending_timer(self):
        with patch.object(self.w,'start_run') as run:
            self.w.auto_recalculate_check.setChecked(True)
            self.w.auto_recalculate_check.setChecked(False)
            QTest.qWait(480)
            run.assert_not_called()

    def test_invalid_bounds_disable_fitting_and_mark_cells(self):
        path=Path(self.tmp.name)/'experiment.csv'; path.write_text('u,f\n0,0\n1,120\n')
        self.w.experiment_path_edit.setText(str(path)); self.w._load_experiment_columns(path)
        self.w.fit_table.item(0,0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(self.w.fit_page_button.isEnabled())
        self.w.fit_table.item(0,2).setText('1000'); self.w.fit_table.item(0,3).setText('10')
        self.assertFalse(self.w.fit_page_button.isEnabled())
        self.assertIn('边界',self.w.fit_validation_label.text())

    def test_absent_experiment_disables_fitting_but_not_running(self):
        self.assertFalse(self.w.fit_page_button.isEnabled())
        self.assertTrue(self.w.run_button.isEnabled())
        self.assertIn('参数验证模式',self.w.fit_validation_label.text())

    def test_loading_source_is_shared_by_all_pages_and_experiment_defaults(self):
        path=Path(self.tmp.name)/'experiment.csv'; path.write_text('u,f\n0,0\n1,120\n')
        self.w.experiment_path_edit.setText(str(path)); self.w._load_experiment_columns(path)
        self.assertEqual(self.w.loading_source_combo.currentData(),'experiment')
        self.assertTrue(all('试验位移' in label.text() for label in self.w._source_badges))
        self.w.amplitudes_edit.setPlainText('0,2,0')
        self.w.loading_source_combo.setCurrentIndex(0)
        self.assertIsNone(self.w._comparison_config_from_ui().loading.history_path)

    def test_removing_experiment_immediately_disables_fit(self):
        path=Path(self.tmp.name)/'experiment.csv'; path.write_text('u,f\n0,0\n1,120\n')
        self.w.experiment_path_edit.setText(str(path)); self.w._load_experiment_columns(path)
        self.w.fit_table.item(0,0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(self.w.fit_page_button.isEnabled())
        self.w.experiment_path_edit.clear()
        self.assertFalse(self.w.fit_page_button.isEnabled())

    def test_apply_fit_can_be_undone_and_queues_complete_run(self):
        original=self.w._material_from_ui().parameter_values['m1_E']
        fitted=copy.deepcopy(self.w._material_from_ui());fitted.set_parameter_value('m1_E',4321)
        with patch('material_test.ui.fitting_workflow.QTimer.singleShot') as schedule:
            self.w._apply_fit_result(SimpleNamespace(fitted_material=fitted))
        self.assertEqual(self.w._material_from_ui().parameter_values['m1_E'],4321)
        self.assertTrue(self.w.undo_fit_button.isEnabled())
        schedule.assert_called_once()
        self.w.undo_fit()
        self.assertEqual(self.w._material_from_ui().parameter_values['m1_E'],original)

    def test_declining_fit_preserves_parameters_and_current_result(self):
        fitted=copy.deepcopy(self.w._material_from_ui());fitted.set_parameter_value('m1_E',4321)
        original=self.w._material_from_ui().parameter_values.copy()
        self.w._fit_original_values=original.copy();self.w._run_generation=self.w._generation
        arrays=SimpleNamespace(displacement=np.array([0,1,0]),force=np.array([0,1,0]))
        candidate=SimpleNamespace(fitted_material=fitted,metrics=SimpleNamespace(normalized_rmse=.01),experiment=arrays,simulation=arrays)
        with patch('material_test.ui.fitting_workflow.QDialog.exec',return_value=0):
            self.w._fit_finished(candidate)
        self.assertEqual(self.w._material_from_ui().parameter_values,original)
        self.assertIsNone(self.w.current_result)

    def test_existing_result_turns_gray_and_cannot_publish_when_stale(self):
        config=elastic_config(self.tmp.name)
        result=run_project(config,run_dir=Path(self.tmp.name)/'result')
        self.w._run_generation=self.w._generation;self.w._run_finished(result)
        self.w._set_material_parameter_value('m1_E',321)
        self.assertTrue(self.w._comparison_dirty)
        self.assertFalse(self.w.publish_button.isEnabled())
        self.assertFalse(self.w.export_button.isEnabled())
        self.assertFalse(self.w.stale_notice.isHidden())
        self.assertEqual(self.w.plot_widget._current_curve.opts['pen'].style(),Qt.PenStyle.DashLine)

    def test_library_preview_is_background_and_cached(self):
        PREVIEW_CACHE.clear()
        gate=Event()
        def calculate(**kwargs):
            gate.wait(2)
            return MaterialPreviewCurve(np.array([0,1,0]),np.array([0,2,0]))
        solver=SolverConfig(ROOT/'OS_terminal'/'OpenSees.exe')
        with patch('material_test.ui.preview_jobs.calculate_material_preview',side_effect=calculate) as calculate_mock:
            dialog=MaterialLibraryDialog(load_material_library(),default_tag=1,existing_tags=set(),solver=solver)
            self.assertNotIn('elastic',dialog.preview_curves)
            gate.set()
            for _ in range(100):
                QTest.qWait(10)
                if 'elastic' in dialog.preview_curves:break
            self.assertTrue(dialog.preview_curves['elastic'].available)
            dialog.close()
            count=calculate_mock.call_count
            cached=MaterialLibraryDialog(load_material_library(),default_tag=1,existing_tags=set(),solver=solver)
            self.assertTrue(cached.preview_curves['elastic'].available)
            self.assertEqual(calculate_mock.call_count,count)
            cached.close()


if __name__ == '__main__':
    unittest.main()
