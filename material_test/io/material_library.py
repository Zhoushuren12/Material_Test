from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from material_test.domain import MaterialTemplate, ParameterSpec


DEFAULT_LIBRARY_PATH = Path(__file__).resolve().parents[1] / "resources" / "material_library.yaml"


@dataclass(frozen=True)
class LibraryParameter:
    name: str
    label: str
    value: float
    min: float | None = None
    max: float | None = None
    unit: str = ""
    description: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "LibraryParameter":
        if "name" not in data or "value" not in data:
            raise ValueError("library parameters require name and value")
        return cls(
            name=str(data["name"]),
            label=str(data.get("label", data["name"])),
            value=float(data["value"]),
            min=float(data["min"]) if data.get("min") is not None else None,
            max=float(data["max"]) if data.get("max") is not None else None,
            unit=str(data.get("unit", "")),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class MaterialLibraryEntry:
    id: str
    name: str
    display_name: str
    category: str
    description: str
    command: str
    parameters: tuple[LibraryParameter, ...]
    source_type: str = ""
    source_url: str = ""
    compatibility: str = ""
    notes: str = ""
    keywords: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "MaterialLibraryEntry":
        required = ("id", "name", "display_name", "category", "command")
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise ValueError(f"material library entry is missing: {', '.join(missing)}")
        entry = cls(
            id=str(data["id"]),
            name=str(data["name"]),
            display_name=str(data["display_name"]),
            category=str(data["category"]),
            description=str(data.get("description", "")),
            command=str(data["command"]),
            parameters=tuple(LibraryParameter.from_mapping(item) for item in data.get("parameters", [])),
            source_type=str(data.get("source_type", "")),
            source_url=str(data.get("source_url", "")),
            compatibility=str(data.get("compatibility", "")),
            notes=str(data.get("notes", "")),
            keywords=tuple(str(item) for item in data.get("keywords", [])),
        )
        entry.as_template()
        return entry

    def as_template(self, *, tag: int = 1) -> MaterialTemplate:
        template = MaterialTemplate(
            name=self.name,
            description=self.description,
            command=self.command,
            tag=int(tag),
            parameters=[
                ParameterSpec(
                    name=parameter.name,
                    value=parameter.value,
                    min=parameter.min,
                    max=parameter.max,
                    description=parameter.label,
                    unit=parameter.unit,
                    effect=parameter.description,
                )
                for parameter in self.parameters
            ],
        )
        template.validate()
        return template

    def instantiate(self, *, tag: int) -> MaterialTemplate:
        """Create a namespaced command that can be appended to a multi-material project."""
        prefix = f"m{int(tag)}_"
        command = self.command.replace("{tag}", str(int(tag)))
        parameters: list[ParameterSpec] = []
        for parameter in self.parameters:
            scoped_name = f"{prefix}{parameter.name}"
            command = command.replace("{" + parameter.name + "}", "{" + scoped_name + "}")
            parameters.append(
                ParameterSpec(
                    name=scoped_name,
                    value=parameter.value,
                    min=parameter.min,
                    max=parameter.max,
                    description=f"{self.display_name} · {parameter.label}",
                    unit=parameter.unit,
                    effect=parameter.description,
                )
            )
        template = MaterialTemplate(
            name=self.display_name,
            description=self.description,
            command=command,
            tag=int(tag),
            parameters=parameters,
        )
        template.validate()
        return template

    def matches(self, query: str) -> bool:
        query = query.strip().lower()
        if not query:
            return True
        haystack = " ".join(
            (self.id, self.name, self.display_name, self.category, self.description, *self.keywords)
        ).lower()
        return query in haystack


@dataclass(frozen=True)
class MaterialLibrary:
    schema_version: int
    name: str
    entries: tuple[MaterialLibraryEntry, ...]

    @property
    def categories(self) -> list[str]:
        return sorted({entry.category for entry in self.entries})

    def get(self, entry_id: str) -> MaterialLibraryEntry:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        raise KeyError(f"unknown material library entry: {entry_id}")


def load_material_library(path: str | Path = DEFAULT_LIBRARY_PATH) -> MaterialLibrary:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"material library must be a mapping: {path}")
    entries = tuple(MaterialLibraryEntry.from_mapping(item) for item in data.get("materials", []))
    if not entries:
        raise ValueError("material library has no entries")
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("material library entry ids must be unique")
    return MaterialLibrary(
        schema_version=int(data.get("schema_version", 1)),
        name=str(data.get("library_name", path.stem)),
        entries=entries,
    )
