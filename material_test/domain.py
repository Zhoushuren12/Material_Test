from __future__ import annotations

import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


def _to_path(value: str | Path | None) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value)


@dataclass
class SolverConfig:
    executable_path: Path
    tcl_library_path: Path | None = None
    working_dir: Path | None = None
    timeout: float = 120.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, base_dir: Path | None = None) -> "SolverConfig":
        base_dir = base_dir or Path.cwd()
        executable = _resolve_optional_path(data.get("executable_path"), base_dir)
        if executable is None:
            raise ValueError("solver.executable_path is required")
        return cls(
            executable_path=executable,
            tcl_library_path=_resolve_optional_path(data.get("tcl_library_path"), base_dir),
            working_dir=_resolve_optional_path(data.get("working_dir"), base_dir),
            timeout=float(data.get("timeout", 120.0)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "executable_path": str(self.executable_path),
            "tcl_library_path": str(self.tcl_library_path) if self.tcl_library_path else None,
            "working_dir": str(self.working_dir) if self.working_dir else None,
            "timeout": self.timeout,
        }


@dataclass
class ParameterSpec:
    name: str
    value: float
    min: float | None = None
    max: float | None = None
    description: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ParameterSpec":
        if "name" not in data:
            raise ValueError("parameter.name is required")
        if "value" not in data:
            raise ValueError(f"parameter {data['name']!r} is missing value")
        return cls(
            name=str(data["name"]),
            value=float(data["value"]),
            min=_optional_float(data.get("min")),
            max=_optional_float(data.get("max")),
            description=str(data.get("description", "")),
        )

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "value": self.value}
        if self.min is not None:
            data["min"] = self.min
        if self.max is not None:
            data["max"] = self.max
        if self.description:
            data["description"] = self.description
        return data

    def validate_value(self) -> None:
        if self.min is not None and self.value < self.min:
            raise ValueError(f"parameter {self.name!r} is below min {self.min:g}")
        if self.max is not None and self.value > self.max:
            raise ValueError(f"parameter {self.name!r} is above max {self.max:g}")


@dataclass
class MaterialTemplate:
    name: str
    command: str
    tag: int = 1
    parameters: list[ParameterSpec] = field(default_factory=list)
    description: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "MaterialTemplate":
        if "name" not in data:
            raise ValueError("material template name is required")
        if "command" not in data:
            raise ValueError("material template command is required")
        parameters = [ParameterSpec.from_mapping(item) for item in data.get("parameters", [])]
        template = cls(
            name=str(data["name"]),
            command=str(data["command"]),
            tag=int(data.get("tag", 1)),
            parameters=parameters,
            description=str(data.get("description", "")),
        )
        template.validate()
        return template

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "command": self.command,
            "tag": self.tag,
            "parameters": [parameter.to_mapping() for parameter in self.parameters],
        }

    @property
    def parameter_values(self) -> dict[str, float]:
        return {parameter.name: parameter.value for parameter in self.parameters}

    def placeholders(self) -> set[str]:
        fields: set[str] = set()
        for _, field_name, _, _ in string.Formatter().parse(self.command):
            if field_name:
                fields.add(field_name.split(".", 1)[0].split("[", 1)[0])
        return fields

    def validate(self) -> None:
        names = [parameter.name for parameter in self.parameters]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate material parameters: {', '.join(duplicates)}")
        for parameter in self.parameters:
            parameter.validate_value()

        allowed = set(names) | {"tag"}
        missing = sorted(self.placeholders() - allowed)
        if missing:
            raise ValueError(f"material command has placeholders without parameters: {', '.join(missing)}")

    def render_command(self) -> str:
        self.validate()
        values: dict[str, Any] = {"tag": self.tag}
        values.update(self.parameter_values)
        try:
            return self.command.format(**values)
        except KeyError as exc:
            raise ValueError(f"missing material placeholder: {exc.args[0]}") from exc

    def set_parameter_value(self, name: str, value: float) -> None:
        for parameter in self.parameters:
            if parameter.name == name:
                parameter.value = float(value)
                parameter.validate_value()
                return
        raise KeyError(f"unknown material parameter: {name}")


@dataclass
class LoadingProtocol:
    amplitudes: list[float] = field(default_factory=list)
    du: float = 0.0
    history_path: Path | None = None
    history_column: str | int | None = None
    history_sheet: str | int | None = 0
    append_zero: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, base_dir: Path | None = None) -> "LoadingProtocol":
        base_dir = base_dir or Path.cwd()
        history = data.get("history") or {}
        if history is None:
            history = {}
        if not isinstance(history, dict):
            raise ValueError("loading.history must be a mapping when provided")
        return cls(
            amplitudes=[float(value) for value in data.get("amplitudes", [])],
            du=float(data.get("du", 0.0) or 0.0),
            history_path=_resolve_optional_path(history.get("path"), base_dir),
            history_column=history.get("displacement_column", history.get("column")),
            history_sheet=history.get("sheet", 0),
            append_zero=bool(data.get("append_zero", history.get("append_zero", False))),
        )

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {"amplitudes": list(self.amplitudes), "du": self.du}
        if self.history_path:
            data["history"] = {
                "path": str(self.history_path),
                "displacement_column": self.history_column,
                "sheet": self.history_sheet,
                "append_zero": self.append_zero,
            }
        elif self.append_zero:
            data["append_zero"] = self.append_zero
        return data


@dataclass
class ExperimentData:
    displacement: np.ndarray
    force: np.ndarray
    source_path: Path | None = None
    displacement_column: str | int | None = None
    force_column: str | int | None = None

    def __post_init__(self) -> None:
        self.displacement = np.asarray(self.displacement, dtype=float).ravel()
        self.force = np.asarray(self.force, dtype=float).ravel()
        if self.displacement.shape != self.force.shape:
            raise ValueError("experiment displacement and force arrays must have the same length")
        if self.displacement.size == 0:
            raise ValueError("experiment data is empty")
        if not np.isfinite(self.displacement).all() or not np.isfinite(self.force).all():
            raise ValueError("experiment data contains non-finite values")


@dataclass
class SimulationResult:
    displacement: np.ndarray
    force: np.ndarray
    tcl_file: Path
    history_file: Path
    response_file: Path
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0

    def __post_init__(self) -> None:
        self.displacement = np.asarray(self.displacement, dtype=float).ravel()
        self.force = np.asarray(self.force, dtype=float).ravel()
        if self.displacement.shape != self.force.shape:
            raise ValueError("simulation displacement and force arrays must have the same length")


@dataclass
class ProjectConfig:
    solver: SolverConfig
    material: MaterialTemplate
    loading: LoadingProtocol
    experiment_path: Path | None = None
    experiment_sheet: str | int | None = None
    displacement_column: str | int | None = None
    force_column: str | int | None = None
    output_dir: Path = Path("outputs") / "runs"
    material_template_path: Path | None = None
    area: float = 1.0
    length: float = 1.0


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _resolve_optional_path(value: Any, base_dir: Path) -> Path | None:
    path = _to_path(value)
    if path is None:
        return None
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()
