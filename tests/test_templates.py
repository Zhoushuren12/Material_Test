from __future__ import annotations

import unittest

from material_test.domain import MaterialTemplate, ParameterSpec


class MaterialTemplateTests(unittest.TestCase):
    def test_render_command_from_parameters(self) -> None:
        template = MaterialTemplate(
            name="Elastic",
            command="uniaxialMaterial Elastic {tag} {k}",
            tag=3,
            parameters=[ParameterSpec("k", 100.0)],
        )
        self.assertEqual(template.render_command(), "uniaxialMaterial Elastic 3 100.0")

    def test_missing_placeholder_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MaterialTemplate(
                name="Bad",
                command="uniaxialMaterial Bad {tag} {missing}",
                parameters=[ParameterSpec("k", 100.0)],
            ).validate()

    def test_duplicate_parameter_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MaterialTemplate(
                name="Bad",
                command="uniaxialMaterial Bad {tag} {k}",
                parameters=[ParameterSpec("k", 100.0), ParameterSpec("k", 200.0)],
            ).validate()


if __name__ == "__main__":
    unittest.main()
