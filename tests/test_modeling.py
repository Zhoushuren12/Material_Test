from __future__ import annotations

import unittest

from material_test.domain import ModelingConfig, TopologyNode
from material_test.modeling import compile_topology, defined_material_tags
from material_test.opensees.tcl_writer import _explicit_model_commands


class NestedModelingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = TopologyNode.group(
            "series",
            [
                TopologyNode.group(
                    "parallel",
                    [
                        TopologyNode.material(101, name="A"),
                        TopologyNode.material(102, name="B", area=2.0),
                    ],
                    length_weight=2.0,
                ),
                TopologyNode.material(103, name="C", length_weight=1.0),
            ],
        )

    def test_nested_config_round_trip(self) -> None:
        original = ModelingConfig(topology="nested", root=self.root)
        restored = ModelingConfig.from_mapping(original.to_mapping())
        self.assertEqual(restored.to_mapping(), original.to_mapping())

    def test_compiles_parallel_branch_in_series(self) -> None:
        model = compile_topology(self.root, total_length=3.0)
        self.assertEqual(len(model.nodes), 3)
        self.assertEqual(len(model.elements), 3)
        self.assertEqual(
            [(element.node_i, element.node_j, element.material_tag) for element in model.elements],
            [(1, 3, 101), (1, 3, 102), (3, 2, 103)],
        )
        self.assertAlmostEqual(next(node.x for node in model.nodes if node.tag == 3), 2.0)
        self.assertEqual(model.elements[1].area, 2.0)

    def test_same_material_tag_can_be_reused_by_multiple_elements(self) -> None:
        root = TopologyNode.group(
            "parallel",
            [TopologyNode.material(101), TopologyNode.material(101)],
        )
        model = compile_topology(root)
        self.assertEqual([element.material_tag for element in model.elements], [101, 101])

    def test_tcl_uses_support_reaction_for_total_force(self) -> None:
        commands, controlled_node, force_expression = _explicit_model_commands(
            topology="nested",
            material_tags=None,
            test_material_tag=1,
            area=1.0,
            length=3.0,
            topology_root=self.root,
        )
        self.assertEqual(controlled_node, 2)
        self.assertEqual(force_expression, "[expr -1.0 * [nodeReaction 1 1]]")
        self.assertIn("element Truss 1 1 3 1 101", commands)
        self.assertIn("element Truss 2 1 3 2 102", commands)
        self.assertIn("element Truss 3 3 2 1 103", commands)

    def test_finds_rendered_material_tags(self) -> None:
        commands = """
        uniaxialMaterial Elastic 101 100.0
        uniaxialMaterial Steel01 202 300.0 200000.0 0.01
        """
        self.assertEqual(defined_material_tags(commands), {101, 202})


if __name__ == "__main__":
    unittest.main()
