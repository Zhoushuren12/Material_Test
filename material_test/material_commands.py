from __future__ import annotations

import re
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
            )
        )
    return commands
