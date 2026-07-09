from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from material_test.domain import MaterialTemplate


def load_material_template(path: str | Path) -> MaterialTemplate:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"material template must be a mapping: {path}")
    return MaterialTemplate.from_mapping(data)


def save_material_template(template: MaterialTemplate, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(template.to_mapping(), sort_keys=False), encoding="utf-8")


def material_template_from_mapping(data: dict[str, Any]) -> MaterialTemplate:
    return MaterialTemplate.from_mapping(data)
