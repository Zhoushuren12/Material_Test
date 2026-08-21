from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from material_test.domain import TopologyNode


@dataclass(frozen=True)
class ModelNode:
    tag: int
    x: float


@dataclass(frozen=True)
class ModelElement:
    tag: int
    node_i: int
    node_j: int
    material_tag: int
    area: float
    name: str = ""


@dataclass(frozen=True)
class CompiledModel:
    nodes: tuple[ModelNode, ...]
    elements: tuple[ModelElement, ...]
    fixed_node: int
    controlled_node: int


class _Compiler:
    def __init__(self, *, default_area: float) -> None:
        self.default_area = float(default_area)
        self.nodes: list[ModelNode] = [ModelNode(1, 0.0), ModelNode(2, 1.0)]
        self.elements: list[ModelElement] = []

    def add_node(self, x: float) -> int:
        tag = len(self.nodes) + 1
        self.nodes.append(ModelNode(tag, float(x)))
        return tag

    def emit(self, component: TopologyNode, node_i: int, node_j: int, x_i: float, x_j: float) -> None:
        if component.kind == "material":
            self.elements.append(
                ModelElement(
                    tag=len(self.elements) + 1,
                    node_i=node_i,
                    node_j=node_j,
                    material_tag=int(component.material_tag),
                    area=float(component.area if component.area is not None else self.default_area),
                    name=component.name,
                )
            )
            return

        if component.kind == "parallel":
            for child in component.children:
                self.emit(child, node_i, node_j, x_i, x_j)
            return

        weights = np.asarray([child.length_weight for child in component.children], dtype=float)
        fractions = np.cumsum(weights / weights.sum())
        left_node = node_i
        left_x = x_i
        for index, child in enumerate(component.children):
            last = index == len(component.children) - 1
            right_x = x_j if last else x_i + (x_j - x_i) * float(fractions[index])
            right_node = node_j if last else self.add_node(right_x)
            self.emit(child, left_node, right_node, left_x, right_x)
            left_node = right_node
            left_x = right_x


def compile_topology(
    root: TopologyNode,
    *,
    default_area: float = 1.0,
    total_length: float = 1.0,
) -> CompiledModel:
    """Expand a recursive two-terminal topology into collinear OpenSees entities."""
    root.validate()
    if not np.isfinite(default_area) or default_area <= 0:
        raise ValueError("model area must be positive")
    if not np.isfinite(total_length) or total_length <= 0:
        raise ValueError("model length must be positive")

    compiler = _Compiler(default_area=default_area)
    compiler.nodes[1] = ModelNode(2, float(total_length))
    compiler.emit(root, 1, 2, 0.0, float(total_length))
    return CompiledModel(
        nodes=tuple(compiler.nodes),
        elements=tuple(compiler.elements),
        fixed_node=1,
        controlled_node=2,
    )


def defined_material_tags(material_command: str) -> set[int]:
    """Return numeric tags from rendered OpenSees uniaxialMaterial commands."""
    pattern = re.compile(r"^\s*uniaxialMaterial\s+\S+\s+(\d+)(?:\s|$)", re.MULTILINE)
    return {int(match.group(1)) for match in pattern.finditer(material_command)}
