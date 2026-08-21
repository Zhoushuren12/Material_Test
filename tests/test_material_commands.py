from __future__ import annotations

import unittest

from material_test.domain import MaterialTemplate, ParameterSpec
from material_test.material_commands import parse_material_commands
from material_test.material_preview import standard_preview_history


class MaterialCommandTests(unittest.TestCase):
    def test_parses_each_material_tag_and_its_parameters(self) -> None:
        template = MaterialTemplate(
            name="combined",
            command=(
                "uniaxialMaterial Elastic {tag} {k}\n"
                "uniaxialMaterial Steel01 5 {m5_Fy} {m5_E0} {m5_b}"
            ),
            tag=1,
            parameters=[
                ParameterSpec("k", 100.0),
                ParameterSpec("m5_Fy", 355.0),
                ParameterSpec("m5_E0", 200000.0),
                ParameterSpec("m5_b", 0.01),
            ],
        )
        commands = parse_material_commands(template)
        self.assertEqual([(item.tag, item.material_type) for item in commands], [(1, "Elastic"), (5, "Steel01")])
        self.assertEqual(commands[0].parameter_names, ("k",))
        self.assertEqual(commands[1].parameter_names, ("m5_Fy", "m5_E0", "m5_b"))

    def test_standard_protocols_match_material_behavior(self) -> None:
        steel = standard_preview_history("Steel02")
        concrete = standard_preview_history("Concrete02")
        self.assertGreater(float(steel.max()), 0.0)
        self.assertLess(float(steel.min()), 0.0)
        self.assertEqual(float(concrete.max()), 0.0)
        self.assertLess(float(concrete.min()), 0.0)
        self.assertIsNone(standard_preview_history("Viscous"))


if __name__ == "__main__":
    unittest.main()
