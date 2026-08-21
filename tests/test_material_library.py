from __future__ import annotations

import unittest

from material_test.io.material_library import load_material_library


class MaterialLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.library = load_material_library()

    def test_library_contains_unique_research_materials(self) -> None:
        ids = [entry.id for entry in self.library.entries]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue({"elastic", "sma", "self_centering", "steel02", "viscous"}.issubset(ids))

    def test_every_entry_renders_a_command(self) -> None:
        for entry in self.library.entries:
            with self.subTest(entry=entry.id):
                command = entry.as_template().render_command()
                self.assertIn("uniaxialMaterial", command)
                self.assertNotIn("{", command)
                self.assertTrue(all(parameter.label for parameter in entry.parameters))
                self.assertTrue(all(parameter.unit for parameter in entry.parameters))
                self.assertTrue(all(parameter.description for parameter in entry.parameters))

    def test_added_material_parameters_are_namespaced(self) -> None:
        for entry in self.library.entries:
            with self.subTest(entry=entry.id):
                template = entry.instantiate(tag=101)
                self.assertIn(" 101 ", f" {template.render_command()} ")
                self.assertTrue(
                    all(parameter.name.startswith("m101_") for parameter in template.parameters)
                )

    def test_search_matches_chinese_and_english_keywords(self) -> None:
        self.assertIn("sma", {entry.id for entry in self.library.entries if entry.matches("SMA")})
        self.assertIn("elastic_pp_gap", {entry.id for entry in self.library.entries if entry.matches("摩擦")})
        self.assertIn("viscous", {entry.id for entry in self.library.entries if entry.matches("dashpot")})


if __name__ == "__main__":
    unittest.main()
