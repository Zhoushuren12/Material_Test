from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from material_test.domain import MaterialTemplate, SolverConfig, TopologyNode
from material_test.io.material_library import load_material_library
from material_test.io.project_config import load_project_config, save_project_config
from material_test.material_commands import parse_material_commands
from material_test.material_preview import calculate_material_preview, standard_preview_history
from material_test.ui.main_window import MainWindow
from material_test.ui.material_library_dialog import MaterialLibraryDialog


ROOT = Path(__file__).resolve().parents[1]


class CompositeMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = load_material_library()

    def test_preview_defines_children_before_wrapper(self) -> None:
        for entry_id in ("series", "parallel", "min_max"):
            with self.subTest(material=entry_id):
                entry = self.library.get(entry_id)
                commands = parse_material_commands(entry.as_template(tag=11))
                self.assertEqual(commands[-1].tag, 11)
                self.assertEqual(commands[-1].material_type, entry.name)
                self.assertEqual(commands[-1].referenced_tags, tuple(c.tag for c in commands[:-1]))

    def test_add_uses_existing_tags_without_example_children(self) -> None:
        for entry_id, tags in (("series", (3, 8)), ("parallel", (3, 8)), ("min_max", (8,))):
            with self.subTest(material=entry_id):
                material = self.library.get(entry_id).instantiate(tag=12, references=tags)
                commands = parse_material_commands(material)
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0].referenced_tags, tags)
                self.assertNotIn("901", material.command)
                self.assertTrue(all(p.name.startswith("m12_") for p in material.parameters))

    def test_invalid_reference_counts_duplicates_and_self_are_rejected(self) -> None:
        cases = [
            ("series", None), ("series", (1,)), ("series", (1, 1)),
            ("series", (1, 12)), ("parallel", ()), ("parallel", (1, 12)),
            ("min_max", ()), ("min_max", (1, 2)), ("min_max", (12,)),
        ]
        for entry_id, tags in cases:
            with self.subTest(material=entry_id, tags=tags), self.assertRaises(ValueError):
                self.library.get(entry_id).instantiate(tag=12, references=tags)

    def test_options_and_comments_are_not_treated_as_child_tags(self) -> None:
        template = MaterialTemplate(
            name="optional-arguments",
            command=(
                "uniaxialMaterial Series 3 1 2 -iter 20 1e-10 # children\n"
                "uniaxialMaterial Parallel 4 1 2 -factors 0.5 2\n"
                "uniaxialMaterial MinMax 5 4 -min -0.02 -max 0.02"
            ),
        )
        self.assertEqual([c.referenced_tags for c in parse_material_commands(template)], [(1, 2), (1, 2), (4,)])


class CompositeMaterialUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.library = load_material_library()

    def make_dialog(self, entry_id: str, tags: set[int]) -> MaterialLibraryDialog:
        dialog = MaterialLibraryDialog(self.library, default_tag=3, existing_tags=tags)
        self.addCleanup(dialog.close)
        dialog.search_edit.setText(entry_id.replace("min_max", "MinMax"))
        self.assertEqual(dialog.selected_entry.id, entry_id)
        return dialog

    def test_series_and_parallel_require_selected_children(self) -> None:
        for entry_id in ("series", "parallel"):
            with self.subTest(material=entry_id):
                dialog = self.make_dialog(entry_id, {1, 2})
                self.assertFalse(dialog.reference_widget.isHidden())
                self.assertFalse(dialog.add_button.isEnabled())
                self.assertTrue(dialog.replace_button.isEnabled())
                dialog.reference_list.item(0).setSelected(True)
                self.assertFalse(dialog.add_button.isEnabled())
                dialog.reference_list.item(1).setSelected(True)
                self.assertTrue(dialog.add_button.isEnabled())
                self.assertEqual(dialog.selected_references, (1, 2))
                dialog.tag_spin.setValue(1)
                self.assertFalse(dialog.add_button.isEnabled())

    def test_minmax_uses_one_child_and_shows_limit_parameters(self) -> None:
        dialog = self.make_dialog("min_max", {1, 2})
        self.assertEqual(dialog.parameter_table.rowCount(), 2)
        dialog.reference_list.setCurrentRow(1)
        self.assertEqual(dialog.selected_references, (2,))
        self.assertTrue(dialog.add_button.isEnabled())
        dialog._accept_action("add")
        self.assertEqual(dialog.result(), MaterialLibraryDialog.DialogCode.Accepted)

    def test_empty_resources_can_use_example_and_reject_example_tag_collision(self) -> None:
        for entry_id in ("series", "parallel", "min_max"):
            with self.subTest(material=entry_id):
                dialog = self.make_dialog(entry_id, set())
                self.assertFalse(dialog.add_button.isEnabled())
                self.assertTrue(dialog.replace_button.isEnabled())
                dialog.tag_spin.setValue(901)
                self.assertFalse(dialog.replace_button.isEnabled())
                dialog.tag_spin.setValue(3)
                dialog._accept_action("replace")
                self.assertEqual(dialog.selected_action, "replace")

    def test_switching_to_plain_material_hides_reference_selector(self) -> None:
        dialog = self.make_dialog("series", {1, 2})
        dialog.search_edit.setText("Steel01")
        self.assertTrue(dialog.reference_widget.isHidden())
        self.assertTrue(dialog.add_button.isEnabled())
        dialog.search_edit.setText("no matching material")
        self.assertFalse(dialog.add_button.isEnabled())
        self.assertFalse(dialog.replace_button.isEnabled())

    def make_window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(lambda: (setattr(window, "_project_dirty", False), window.close()))
        children = [self.library.get("elastic").instantiate(tag=tag) for tag in (1, 2)]
        material = MaterialTemplate(
            name="composite-ui", tag=1,
            command="\n".join(child.command for child in children),
            parameters=[p for child in children for p in child.parameters],
        )
        window._populate_material(material, selected_tag=1)
        return window

    def add_from_dialog(self, window: MainWindow, entry_id: str) -> None:
        def choose(dialog):
            dialog.search_edit.setText(entry_id.replace("min_max", "MinMax"))
            dialog.reference_list.item(0).setSelected(True)
            if entry_id != "min_max":
                dialog.reference_list.item(1).setSelected(True)
            dialog._accept_action("add")
            return dialog.result()

        with patch.object(MaterialLibraryDialog, "_calculate_previews"), patch.object(
            MaterialLibraryDialog, "exec", choose,
        ), patch("material_test.ui.main_window.QMessageBox.critical") as error:
            window.open_material_library()
            error.assert_not_called()

    def test_all_three_materials_add_and_round_trip_through_project(self) -> None:
        for entry_id in ("series", "parallel", "min_max"):
            with self.subTest(material=entry_id):
                window = self.make_window()
                self.add_from_dialog(window, entry_id)
                commands = parse_material_commands(window._material_from_ui())
                self.assertEqual([c.tag for c in commands], [1, 2, 3])
                self.assertEqual(commands[-1].material_type, self.library.get(entry_id).name)
                self.assertEqual(commands[-1].referenced_tags, (1,) if entry_id == "min_max" else (1, 2))
                window.topology_editor.set_root(TopologyNode.material(3))
                if entry_id == "min_max":
                    window._set_material_parameter_value("m3_maxStrain", 0.01)
                with TemporaryDirectory() as tmp:
                    path = Path(tmp) / "project.yaml"
                    save_project_config(window._config_from_ui(), path)
                    loaded = load_project_config(path)
                self.assertEqual(loaded.material.render_command(), window._material_from_ui().render_command())
                self.assertEqual(loaded.modeling.root.material_tag, 3)
                if entry_id == "min_max":
                    self.assertEqual(loaded.material.parameter_values["m3_maxStrain"], 0.01)
                window._project_dirty = False
                window.close()

    def test_used_child_cannot_be_deleted_until_wrapper_is_removed(self) -> None:
        window = self.make_window()
        self.add_from_dialog(window, "parallel")
        original = window._material_from_ui().render_command()
        window._select_material_tag(1)
        self.assertFalse(window.delete_selected_material(confirm=False))
        self.assertEqual(window._material_from_ui().render_command(), original)
        window._select_material_tag(3)
        self.assertTrue(window.delete_selected_material(confirm=False))
        window._select_material_tag(1)
        self.assertTrue(window.delete_selected_material(confirm=False))


@unittest.skipUnless((ROOT / "OS_terminal" / "OpenSees.exe").exists(), "OpenSees.exe is not available")
class CompositeSolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.solver = SolverConfig(executable_path=ROOT / "OS_terminal" / "OpenSees.exe")
        self.library = load_material_library()

    def test_elastic_series_and_parallel_match_analytical_stiffness(self) -> None:
        history = np.array([0.0, 0.001, -0.001, 0.002, 0.0])
        for entry_id, modulus in (("series", 80.0), ("parallel", 360.0)):
            with self.subTest(material=entry_id):
                wrapper = self.library.get(entry_id).instantiate(tag=3, references=(1, 2))
                material = MaterialTemplate(
                    name=entry_id, tag=3,
                    command="uniaxialMaterial Elastic 1 120\nuniaxialMaterial Elastic 2 240\n" + wrapper.command,
                )
                curve = calculate_material_preview(
                    solver=self.solver, material=material, material_type=self.library.get(entry_id).name,
                    area=2.0, length=4.0, displacement_history=history,
                )
                np.testing.assert_allclose(curve.force, history * modulus * 2.0 / 4.0, atol=1e-8)

    def test_all_preview_histories_complete_and_minmax_remains_failed(self) -> None:
        for entry_id in ("series", "parallel", "min_max"):
            with self.subTest(material=entry_id):
                entry = self.library.get(entry_id)
                curve = calculate_material_preview(solver=self.solver, material=entry.as_template(), material_type=entry.name)
                np.testing.assert_allclose(curve.displacement, standard_preview_history(entry.name), atol=1e-10)
                self.assertTrue(np.isfinite(curve.force).all())
                self.assertGreater(float(np.max(np.abs(curve.force))), 1.0)
                if entry_id == "min_max":
                    failure_index = np.flatnonzero(np.abs(curve.displacement) > 0.005 + 1e-10)[0]
                    np.testing.assert_allclose(curve.force[failure_index:], 0.0, atol=1e-8)


if __name__ == "__main__":
    unittest.main()
