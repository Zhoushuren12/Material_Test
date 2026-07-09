from __future__ import annotations

from pathlib import Path


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
) -> None:
    """Write a Tcl model for a uniaxial material cyclic displacement test."""
    tcl_file.parent.mkdir(parents=True, exist_ok=True)
    history_tcl = history_file.as_posix()
    response_tcl = response_file.as_posix()
    tcl_file.write_text(
        f"""wipe
model basic -ndm 2 -ndf 3
node 1 0.0 0.0
node 2 {length:.12g} 0.0

{material_command}
element Truss 1 1 2 {area:.12g} {test_material_tag}

fix 1 1 1 1
fix 2 0 1 1

timeSeries Path 1 -dt 1 -filePath "{history_tcl}"
pattern Plain 1 1 {{
    sp 2 1 1.0
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
    set u [nodeDisp 2 1]
    set f [expr -1.0 * [eleForce 1 1]]
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
