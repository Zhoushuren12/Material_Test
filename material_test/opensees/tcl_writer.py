from __future__ import annotations

from pathlib import Path
from typing import Sequence

from material_test.domain import TopologyNode
from material_test.modeling import compile_topology


def write_material_test_tcl(
    *,
    tcl_file: Path,
    history_file: Path,
    response_file: Path,
    material_command: str,
    n_steps: int,
    area: float = 1.0,
    length: float = 1.0,
    test_material_tag: int = 1,
    topology: str = "single",
    material_tags: Sequence[int] | None = None,
    topology_root: TopologyNode | None = None,
    material_mode: str = "stress_strain",
) -> None:
    """Write a Tcl model for a uniaxial material cyclic displacement test."""
    tcl_file.parent.mkdir(parents=True, exist_ok=True)
    history_tcl = history_file.as_posix()
    response_tcl = response_file.as_posix()
    model_commands, controlled_node, force_expression = _explicit_model_commands(
        topology=topology,
        material_tags=material_tags,
        test_material_tag=test_material_tag,
        area=area,
        length=length,
        topology_root=topology_root,
        material_mode=material_mode,
    )
    tcl_file.write_text(
        f"""wipe
model basic -ndm 2 -ndf 3

{material_command}

{model_commands}

timeSeries Path 1 -dt 1 -filePath "{history_tcl}"
pattern Plain 1 1 {{
    sp {controlled_node} 1 1.0
}}

constraints Lagrange
numberer RCM
system BandGeneral
test EnergyIncr 1.0e-8 100
algorithm Newton
integrator LoadControl 1.0
analysis Static

set out [open "{response_tcl}" w]
for {{set i 0}} {{$i < {n_steps}}} {{incr i}} {{
    set u [nodeDisp {controlled_node} 1]
    reactions
    set f {force_expression}
    puts $out "$u $f"
    set ok [analyze 1]
    if {{$ok != 0}} {{
        puts "Analysis failed at step $i"
        break
    }}
}}
close $out
wipe
exit
""",
        encoding="utf-8",
    )


def _explicit_model_commands(
    *,
    topology: str,
    material_tags: Sequence[int] | None,
    test_material_tag: int,
    area: float,
    length: float,
    topology_root: TopologyNode | None = None,
    material_mode: str = "stress_strain",
) -> tuple[str, int, str]:
    topology = topology.lower()
    if topology_root is None:
        tags = [int(tag) for tag in (material_tags or [])]
        if topology not in {"single", "series", "parallel"}:
            raise ValueError("topology must be 'single', 'series', or 'parallel'")
        if topology == "single":
            topology_root = TopologyNode.material(test_material_tag)
        else:
            if len(tags) < 2:
                raise ValueError(f"explicit {topology} modeling requires at least two material tags")
            topology_root = TopologyNode.group(
                topology,
                [TopologyNode.material(tag) for tag in tags],
            )

    compiled = compile_topology(topology_root, default_area=area, total_length=length)
    lines = [f"node {node.tag} {node.x:.12g} 0.0" for node in compiled.nodes]
    if material_mode not in {"stress_strain", "force_deformation"}:
        raise ValueError("未知本构输入模式")
    for element in compiled.elements:
        if material_mode == "force_deformation":
            lines.append(f"element twoNodeLink {element.tag} {element.node_i} {element.node_j} "
                         f"-mat {element.material_tag} -dir 1 -orient 1 0 0")
        else:
            lines.append(f"element Truss {element.tag} {element.node_i} {element.node_j} "
                         f"{element.area:.12g} {element.material_tag}")
    lines.append(f"fix {compiled.fixed_node} 1 1 1")
    lines.extend(
        f"fix {node.tag} 0 1 1"
        for node in compiled.nodes
        if node.tag != compiled.fixed_node
    )
    force_expression = f"[expr -1.0 * [nodeReaction {compiled.fixed_node} 1]]"
    return "\n".join(lines), compiled.controlled_node, force_expression
