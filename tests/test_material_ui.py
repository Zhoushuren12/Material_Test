from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QMessageBox

from material_test.domain import ExperimentData, MaterialTemplate, SimulationResult, TopologyNode
from material_test.io.material_library import load_material_library
from material_test.io.project_config import load_project_config, save_project_config
from material_test.material_commands import parse_material_commands
from material_test.material_preview import MaterialPreviewCurve, preview_error_summary
from material_test.ui.material_library_dialog import MaterialLibraryDialog
from material_test.ui.main_window import DEFAULT_TEMPLATE, PAGES, MainWindow
from material_test.workflow import WorkflowResult


class MaterialSelectionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()
        self.window._load_template(DEFAULT_TEMPLATE)
        self.window.topology_editor.set_root(TopologyNode.material(1, name="E01"))

    def tearDown(self) -> None:
        self.window.close()

    def test_tag_arrows_switch_material_and_preserve_edits(self) -> None:
        base = self.window._material_from_ui()
        library = load_material_library()
        steel = library.get("steel01").instantiate(tag=2)
        self_centering = library.get("self_centering").instantiate(tag=5)
        combined = MaterialTemplate(
            name="selection-test",
            command="\n".join((base.command, steel.command, self_centering.command)),
            tag=base.tag,
            parameters=[*base.parameters, *steel.parameters, *self_centering.parameters],
        )
        self.window._populate_material(combined, selected_tag=1)

        self.window.material_tag_spin.stepBy(1)
        self.assertEqual(self.window.material_tag_spin.value(), 2)
        self.assertEqual(self.window._material_commands[self.window._selected_material_row].tag, 2)
        editor = self.window.param_table.cellWidget(0, 2)
        self.assertIsInstance(editor, QDoubleSpinBox)
        editor.setValue(420.0)

        self.window.material_tag_spin.stepBy(1)
        self.assertEqual(self.window.material_tag_spin.value(), 5)
        self.window.material_tag_spin.stepBy(-1)
        self.assertEqual(self.window.material_tag_spin.value(), 2)
        self.assertEqual(self.window.param_table.cellWidget(0, 2).value(), 420.0)
        self.assertEqual(self.window._material_from_ui().parameter_values["m2_Fy"], 420.0)

    def test_combined_material_edit_survives_project_round_trip(self) -> None:
        base = self.window._material_from_ui()
        added = load_material_library().get("sma").instantiate(tag=2)
        combined = MaterialTemplate(
            name="round-trip",
            command=f"{base.command}\n{added.command}",
            tag=base.tag,
            parameters=[*base.parameters, *added.parameters],
        )
        self.window._populate_material(combined, selected_tag=2)
        self.window.current_template_path = None
        self.window.param_table.cellWidget(0, 2).setValue(1234.5)

        with TemporaryDirectory(prefix="material_ui_roundtrip_") as temp_dir:
            project_path = Path(temp_dir) / "project.yaml"
            save_project_config(self.window._config_from_ui(), project_path)
            loaded = load_project_config(project_path)

        self.assertEqual(loaded.material.parameter_values["m2_E"], 1234.5)
        parameter = next(item for item in loaded.material.parameters if item.name == "m2_E")
        self.assertEqual(parameter.unit, "model stress unit")
        self.assertTrue(parameter.effect)

    def test_delete_material_reassigns_elements_without_changing_topology(self) -> None:
        base = self.window._material_from_ui()
        library = load_material_library()
        steel = library.get("steel01").instantiate(tag=2)
        self_centering = library.get("self_centering").instantiate(tag=5)
        combined = MaterialTemplate(
            name="delete-test",
            command="\n".join((base.command, steel.command, self_centering.command)),
            tag=base.tag,
            parameters=[*base.parameters, *steel.parameters, *self_centering.parameters],
        )
        self.window._populate_material(combined, selected_tag=2)
        self.window.topology_editor.set_root(
            TopologyNode.group(
                "parallel",
                [
                    TopologyNode.material(2),
                    TopologyNode.group(
                        "series",
                        [TopologyNode.material(1), TopologyNode.material(2), TopologyNode.material(5)],
                    ),
                ],
            )
        )

        self.assertTrue(self.window.delete_selected_material(confirm=False, replacement_tag=5))

        self.assertEqual([command.tag for command in self.window._material_commands], [1, 5])
        self.assertEqual(self.window.material_tag_spin.value(), 5)
        self.assertFalse(any(name.startswith("m2_") for name in self.window._material_parameter_order))
        modeling = self.window.topology_editor.modeling()
        self.assertEqual(modeling.root.kind, "parallel")
        self.assertEqual(modeling.root.material_tags(), [5, 1, 5, 5])
        self.assertIsNone(self.window.current_template_path)

        with TemporaryDirectory(prefix="material_ui_delete_") as temp_dir:
            project_path = Path(temp_dir) / "project.yaml"
            save_project_config(self.window._config_from_ui(), project_path)
            loaded = load_project_config(project_path)

        self.assertEqual([command.tag for command in parse_material_commands(loaded.material)], [1, 5])
        self.assertEqual(loaded.modeling.root.material_tags(), [5, 1, 5, 5])

    def test_delete_material_can_leave_referenced_elements_unassigned(self) -> None:
        base = self.window._material_from_ui()
        added = load_material_library().get("steel01").instantiate(tag=2)
        combined = MaterialTemplate(
            name="unassign-test",
            command=f"{base.command}\n{added.command}",
            tag=base.tag,
            parameters=[*base.parameters, *added.parameters],
        )
        self.window._populate_material(combined, selected_tag=2)
        self.window.topology_editor.set_root(
            TopologyNode.group(
                "series",
                [TopologyNode.material(1, name="E01"), TopologyNode.material(2, name="E02")],
            )
        )

        self.assertTrue(self.window.delete_selected_material(confirm=False))

        unassigned, undefined = self.window.topology_editor.assignment_issues()
        self.assertEqual(unassigned, ["E02"])
        self.assertEqual(undefined, [])
        root_item = self.window.topology_editor.tree.topLevelItem(0)
        self.assertEqual(root_item.childCount(), 2)
        with self.assertRaisesRegex(ValueError, "E02"):
            self.window.topology_editor.modeling()

    def test_last_material_can_be_deleted_and_empty_draft_round_trips(self) -> None:
        self.assertEqual(len(self.window._material_commands), 1)
        delete_button = self.window.material_command_table.cellWidget(0, 4)
        self.assertIsNotNone(delete_button)
        self.assertTrue(delete_button.isEnabled())
        self.assertTrue(self.window.delete_selected_material(confirm=False))
        self.assertEqual(len(self.window._material_commands), 0)
        self.assertEqual(self.window.material_command_table.rowCount(), 0)
        self.assertEqual(self.window.selected_material_label.text(), "尚未添加材料")
        self.assertEqual(self.window.topology_editor.assignment_issues()[0], ["E01"])

        with TemporaryDirectory(prefix="material_ui_empty_") as temp_dir:
            project_path = Path(temp_dir) / "empty-project.yaml"
            save_project_config(
                self.window._config_from_ui(require_ready=False),
                project_path,
            )
            loaded = load_project_config(project_path)

        self.assertEqual(parse_material_commands(loaded.material), [])
        self.assertIsNone(loaded.modeling.root.material_tag)

    def test_new_window_starts_with_empty_material_resources(self) -> None:
        empty_window = MainWindow()
        try:
            self.assertEqual(empty_window._material_commands, [])
            self.assertEqual(empty_window.material_command_table.rowCount(), 0)
            self.assertEqual(empty_window.selected_material_label.text(), "尚未添加材料")
            with self.assertRaisesRegex(ValueError, "材料资源为空"):
                empty_window._config_from_ui(require_ready=True)
        finally:
            empty_window.close()

    def test_row_delete_button_deletes_its_material(self) -> None:
        base = self.window._material_from_ui()
        added = load_material_library().get("steel01").instantiate(tag=2)
        combined = MaterialTemplate(
            name="row-delete-test",
            command=f"{base.command}\n{added.command}",
            tag=base.tag,
            parameters=[*base.parameters, *added.parameters],
        )
        self.window._populate_material(combined, selected_tag=1)
        delete_button = self.window.material_command_table.cellWidget(1, 4)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            delete_button.click()

        self.assertEqual([command.tag for command in self.window._material_commands], [1])

    def test_loading_history_plot_updates_from_protocol_controls(self) -> None:
        self.window.history_path_edit.clear()
        self.window.amplitudes_edit.setPlainText("0, 1, 0, -1, 0")
        self.window.du_spin.setValue(0.25)
        self.window._refresh_loading_preview()

        axes = self.window.loading_history_plot.figure.axes[0]
        self.assertEqual(len(axes.lines), 3)  # history plus horizontal and vertical zero axes
        values = axes.lines[0].get_ydata()
        self.assertAlmostEqual(float(max(values)), 1.0)
        self.assertAlmostEqual(float(min(values)), -1.0)

    def test_preview_error_summary_does_not_expose_solver_output_in_page(self) -> None:
        error = RuntimeError(
            "OpenSees did not write a response file\nstdout:\n" + "solver output " * 100
        )

        summary = preview_error_summary(error)

        self.assertLess(len(summary), 100)
        self.assertNotIn("stdout", summary)
        self.assertIn("运行日志", summary)

    def test_material_preview_failure_keeps_details_out_of_page(self) -> None:
        detail = "OpenSees did not write a response file\nstdout:\n" + "solver output " * 100

        with patch(
            "material_test.ui.main_window.calculate_material_preview",
            side_effect=RuntimeError(detail),
        ):
            self.window.calculate_selected_material_preview()

        self.assertLess(len(self.window.material_preview_note.text()), 100)
        self.assertNotIn("stdout", self.window.material_preview_note.text())
        self.assertIn(detail, self.window.log_edit.toPlainText())

    def test_library_preview_status_distinguishes_failures_from_transient_materials(self) -> None:
        library = load_material_library()
        failed = MaterialPreviewCurve(None, None, "OpenSees 计算失败")

        self.assertEqual(
            MaterialLibraryDialog._preview_status(library.get("steel01"), failed),
            "预览失败",
        )
        self.assertEqual(
            MaterialLibraryDialog._preview_status(library.get("viscous"), failed),
            "需瞬态协议",
        )

    def test_comparison_page_replaces_standalone_results_page(self) -> None:
        self.assertEqual(PAGES[4][0], "计算与对比")
        self.assertEqual(PAGES[5][0], "参数拟合")
        self.assertNotIn("运行结果", [title for title, subtitle in PAGES])

    def test_comparison_parameter_editor_updates_material_definition(self) -> None:
        steel = load_material_library().get("steel01").instantiate(tag=1)
        self.window._populate_material(steel, selected_tag=1)
        self.window._sync_comparison_materials(selected_tag=1)

        editor = self.window.comparison_parameter_table.cellWidget(0, 2)
        self.assertIsInstance(editor, QDoubleSpinBox)
        editor.setValue(420.0)

        self.assertEqual(self.window._material_from_ui().parameter_values["m1_Fy"], 420.0)
        self.assertEqual(self.window.param_table.cellWidget(0, 2).value(), 420.0)
        self.assertTrue(self.window.param_table.cellWidget(0, 2).isReadOnly())
        self.assertFalse(editor.isReadOnly())

    def test_comparison_can_use_experiment_displacement_as_loading(self) -> None:
        with TemporaryDirectory(prefix="comparison_protocol_") as temp_dir:
            experiment_path = Path(temp_dir) / "experiment.csv"
            np.savetxt(
                experiment_path,
                np.array([[0.0, 0.0], [1.0, 10.0], [0.0, 0.0]]),
                delimiter=",",
                header="displacement,force",
                comments="",
            )
            self.window.experiment_path_edit.setText(str(experiment_path))
            self.window._load_experiment_columns(experiment_path)
            self.window._select_combo_text(self.window.displacement_combo, "displacement")
            self.window._select_combo_text(self.window.force_combo, "force")
            self.window.comparison_protocol_combo.setCurrentIndex(
                self.window.comparison_protocol_combo.findData("experiment")
            )

            config = self.window._comparison_config_from_ui()

        self.assertEqual(config.loading.history_path, experiment_path)
        self.assertEqual(config.loading.history_column, "displacement")
        self.assertFalse(config.loading.append_zero)

    def test_baseline_current_and_experiment_are_compared_together(self) -> None:
        displacement = np.array([0.0, 1.0, 0.0, -1.0, 0.0])
        experiment = ExperimentData(displacement, np.array([0.0, 10.0, 0.0, -10.0, 0.0]))
        baseline = self._workflow_result(
            displacement,
            np.array([0.0, 8.0, 0.0, -8.0, 0.0]),
            experiment,
            "baseline",
        )
        current = self._workflow_result(
            displacement,
            np.array([0.0, 9.0, 0.0, -9.0, 0.0]),
            experiment,
            "current",
        )

        self.window._run_finished(baseline)
        self.window.set_current_as_baseline()
        self.window._run_finished(current)

        self.assertEqual(
            self.window.plot_widget.series_labels,
            ["Experiment", "Baseline", "Current"],
        )
        self.assertEqual(self.window.comparison_metrics_table.rowCount(), 3)
        self.assertIs(self.window.baseline_result, baseline)
        self.assertIs(self.window.current_result, current)
        current_rmse = self.window.comparison_metrics_table.item(2, 5).text()
        self.assertNotEqual(current_rmse, "—")

    def test_material_parameter_editor_accepts_scientific_notation(self) -> None:
        steel = load_material_library().get("steel01").instantiate(tag=1)
        self.window._populate_material(steel, selected_tag=1)
        self.window._sync_comparison_materials(selected_tag=1)
        editor = self.window.comparison_parameter_table.cellWidget(0, 2)
        self.assertIsInstance(editor, QDoubleSpinBox)

        editor.lineEdit().setText("1e6")
        editor.interpretText()

        self.assertEqual(editor.value(), 1.0e6)
        self.assertEqual(self.window._material_from_ui().parameter_values["m1_Fy"], 1.0e6)

    @staticmethod
    def _workflow_result(
        displacement: np.ndarray,
        force: np.ndarray,
        experiment: ExperimentData,
        name: str,
    ) -> WorkflowResult:
        base = Path("unused") / name
        simulation = SimulationResult(
            displacement=displacement,
            force=force,
            tcl_file=base / "model.tcl",
            history_file=base / "history.txt",
            response_file=base / "response.txt",
        )
        return WorkflowResult(
            run_dir=base,
            simulation=simulation,
            experiment=experiment,
            figure_file=base / "overlay.png",
            simulation_csv=base / "simulation.csv",
        )


if __name__ == "__main__":
    unittest.main()
