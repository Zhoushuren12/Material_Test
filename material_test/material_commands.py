from __future__ import annotations

import re
import shlex
import math
import string
from dataclasses import dataclass

from material_test.domain import MaterialTemplate


_COMMAND_PATTERN = re.compile(r"^\s*uniaxialMaterial\s+(\S+)\s+(\d+)(?:\s|$)")


@dataclass(frozen=True)
class ParsedMaterialCommand:
    line_index: int
    source: str
    rendered: str
    material_type: str
    tag: int
    parameter_names: tuple[str, ...]
    referenced_tags: tuple[int, ...] = ()


def _referenced_material_tags(material_type: str, rendered: str) -> tuple[int, ...]:
    if material_type not in {"Series", "Parallel", "MinMax"}:
        return ()
    arguments = shlex.split(rendered, comments=True)[3:]
    if material_type == "MinMax":
        arguments = arguments[:1]
    tags = []
    for argument in arguments:
        if argument.startswith("-"):
            break
        tags.append(int(argument))
    return tuple(tags)


def parse_material_commands(template: MaterialTemplate) -> list[ParsedMaterialCommand]:
    """Find selectable uniaxial material commands while preserving their source lines."""
    source_lines = template.command.splitlines()
    rendered_lines = template.render_command().splitlines()
    commands: list[ParsedMaterialCommand] = []
    for line_index, (source, rendered) in enumerate(zip(source_lines, rendered_lines)):
        match = _COMMAND_PATTERN.match(rendered)
        if match is None:
            continue
        parameter_names = tuple(
            field_name.split(".", 1)[0].split("[", 1)[0]
            for _, field_name, _, _ in string.Formatter().parse(source)
            if field_name and field_name != "tag"
        )
        commands.append(
            ParsedMaterialCommand(
                line_index=line_index,
                source=source.strip(),
                rendered=rendered.strip(),
                material_type=match.group(1),
                tag=int(match.group(2)),
                parameter_names=parameter_names,
                referenced_tags=_referenced_material_tags(match.group(1), rendered),
            )
        )
    return commands


def validate_solver_materials(template: MaterialTemplate) -> None:
    """Explain invalid explicit Steel02 parameters before native NaN failures."""
    for line in template.render_command().splitlines():
        tokens = shlex.split(line, comments=True)
        if len(tokens) < 9 or tokens[:2] != ["uniaxialMaterial", "Steel02"]:
            continue
        try:
            values = dict(zip(("Fy", "E0", "b", "R0", "cR1", "cR2"), map(float, tokens[3:9])))
        except ValueError:
            continue  # Tcl expressions are resolved by the solver.
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError(f"Steel02 Tag {tokens[2]} 参数必须为有限数值")
        for name in ("Fy", "E0", "R0", "cR2"):
            if values[name] <= 0:
                raise ValueError(f"Steel02 Tag {tokens[2]}：{name} 必须大于 0，当前为 {values[name]:g}；请核对参数所在行。")
