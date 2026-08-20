from __future__ import annotations

from pathlib import Path
from typing import Sequence


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
) -> tuple[str, int, str]:
    topology = topology.lower()
    tags = [int(tag) for tag in (material_tags or [])]
    if topology not in {"single", "series", "parallel"}:
        raise ValueError("topology must be 'single', 'series', or 'parallel'")
    if topology == "single":
        tags = [int(test_material_tag)]
    elif len(tags) < 2:
        raise ValueError(f"explicit {topology} modeling requires at least two material tags")
    if any(tag <= 0 for tag in tags):
        raise ValueError("material tags must be positive integers")
    if len(set(tags)) != len(tags):
        raise ValueError("material tags must not contain duplicates")

    if topology == "series":
        segment_length = length / len(tags)
        lines = [f"node {index + 1} {index * segment_length:.12g} 0.0" for index in range(len(tags) + 1)]
        lines.extend(
            f"element Truss {index} {index} {index + 1} {area:.12g} {tag}"
            for index, tag in enumerate(tags, start=1)
        )
        lines.append("fix 1 1 1 1")
        lines.extend(f"fix {node} 0 1 1" for node in range(2, len(tags) + 2))
        return "\n".join(lines), len(tags) + 1, "[expr -1.0 * [eleForce 1 1]]"

    lines = [
        "node 1 0.0 0.0",
        f"node 2 {length:.12g} 0.0",
    ]
    lines.extend(
        f"element Truss {index} 1 2 {area:.12g} {tag}"
        for index, tag in enumerate(tags, start=1)
    )
    lines.extend(["fix 1 1 1 1", "fix 2 0 1 1"])
    if topology == "parallel":
        element_forces = " + ".join(f"[eleForce {index} 1]" for index in range(1, len(tags) + 1))
        force_expression = f"[expr -1.0 * ({element_forces})]"
    else:
        force_expression = "[expr -1.0 * [eleForce 1 1]]"
    return "\n".join(lines), 2, force_expression
