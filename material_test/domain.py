from __future__ import annotations

from copy import deepcopy
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from material_test.deformation import DeformationHistory


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
    unit: str = ""
    effect: str = ""

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
            unit=str(data.get("unit", "")),
            effect=str(data.get("effect", "")),
        )

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "value": self.value}
        if self.min is not None:
            data["min"] = self.min
        if self.max is not None:
            data["max"] = self.max
        if self.description:
            data["description"] = self.description
        if self.unit:
            data["unit"] = self.unit
        if self.effect:
            data["effect"] = self.effect
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
    source: str = "auto"
    history_scale: float = 1.0
    samples: list[float] = field(default_factory=list)

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
            source=str(data.get("source", "external" if history.get("path") else "amplitudes")),
            history_scale=float(history.get("scale", 1.0)),
            samples=[float(value) for value in data.get("samples", [])],
        )

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {"amplitudes": list(self.amplitudes), "du": self.du,
                                "source": self.source if self.source != "auto" else ("external" if self.history_path else "amplitudes")}
        if self.history_path:
            data["history"] = {
                "path": str(self.history_path),
                "displacement_column": self.history_column,
                "sheet": self.history_sheet,
                "append_zero": self.append_zero,
                "scale": self.history_scale,
            }
        elif self.append_zero:
            data["append_zero"] = self.append_zero
        if self.samples:
            data['samples'] = list(self.samples)
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
    deformation: DeformationHistory | None = None

    def __post_init__(self) -> None:
        self.displacement = np.asarray(self.displacement, dtype=float).ravel()
        self.force = np.asarray(self.force, dtype=float).ravel()
        if self.displacement.shape != self.force.shape:
            raise ValueError("simulation displacement and force arrays must have the same length")


@dataclass
class FitParameterSpec:
    """A bounded material parameter selected for automatic fitting."""

    name: str
    min: float
    max: float
    scale: str = "linear"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FitParameterSpec":
        if "name" not in data or "min" not in data or "max" not in data:
            raise ValueError("fitting.parameters entries require name, min, and max")
        spec = cls(
            name=str(data["name"]),
            min=float(data["min"]),
            max=float(data["max"]),
            scale=str(data.get("scale", "linear")).lower(),
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if not np.isfinite(self.min) or not np.isfinite(self.max) or self.min >= self.max:
            raise ValueError(f"fitting bounds for {self.name!r} must be finite and min < max")
        if self.scale not in {"linear", "log"}:
            raise ValueError(f"fitting scale for {self.name!r} must be 'linear' or 'log'")
        if self.scale == "log" and self.min <= 0:
            raise ValueError(f"log-scaled fitting parameter {self.name!r} requires min > 0")

    def to_mapping(self) -> dict[str, Any]:
        return {"name": self.name, "min": self.min, "max": self.max, "scale": self.scale}


@dataclass
class FitConfig:
    """Controls a global material-parameter search and optional local refinement."""

    parameters: list[FitParameterSpec]
    algorithm: str = "de"
    max_evaluations: int = 240
    population_size: int = 16
    local_refine: bool = True
    local_max_evaluations: int = 120
    local_diff_step: float = 1.0e-4
    loss: str = "soft_l1"
    f_scale: float = 1.0
    seed: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FitConfig":
        parameters_data = data.get("parameters", [])
        if not isinstance(parameters_data, list):
            raise ValueError("fitting.parameters must be a list")
        config = cls(
            parameters=[FitParameterSpec.from_mapping(item) for item in parameters_data],
            algorithm=str(data.get("algorithm", "de")).lower(),
            max_evaluations=int(data.get("max_evaluations", 240)),
            population_size=int(data.get("population_size", 16)),
            local_refine=bool(data.get("local_refine", True)),
            local_max_evaluations=int(data.get("local_max_evaluations", 120)),
            local_diff_step=float(data.get("local_diff_step", 1.0e-4)),
            loss=str(data.get("loss", "soft_l1")),
            f_scale=float(data.get("f_scale", 1.0)),
            seed=int(data["seed"]) if data.get("seed") is not None else None,
        )
        config.validate()
        return config

    def validate(self) -> None:
        names = [parameter.name for parameter in self.parameters]
        if not names:
            raise ValueError("fitting requires at least one selected parameter")
        if len(set(names)) != len(names):
            raise ValueError("fitting parameters must not contain duplicates")
        if self.algorithm not in {"de", "pso"}:
            raise ValueError("fitting.algorithm must be 'de' or 'pso'")
        if self.max_evaluations < 4:
            raise ValueError("fitting.max_evaluations must be at least 4")
        if self.population_size < 2:
            raise ValueError("fitting.population_size must be at least 2")
        if self.local_max_evaluations < 1:
            raise ValueError("fitting.local_max_evaluations must be positive")
        if not np.isfinite(self.local_diff_step) or self.local_diff_step <= 0:
            raise ValueError("fitting.local_diff_step must be finite and positive")
        if self.loss not in {"linear", "soft_l1", "huber", "cauchy", "arctan"}:
            raise ValueError("fitting.loss is not supported by scipy.optimize.least_squares")
        if self.f_scale <= 0:
            raise ValueError("fitting.f_scale must be positive")
        for parameter in self.parameters:
            parameter.validate()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "parameters": [parameter.to_mapping() for parameter in self.parameters],
            "max_evaluations": self.max_evaluations,
            "population_size": self.population_size,
            "local_refine": self.local_refine,
            "local_max_evaluations": self.local_max_evaluations,
            "local_diff_step": self.local_diff_step,
            "loss": self.loss,
            "f_scale": self.f_scale,
            "seed": self.seed,
        }


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
    fitting: FitConfig | None = None
    modeling: ModelingConfig = field(default_factory=lambda: ModelingConfig())
    force_unit: str = "N"
    length_unit: str = "mm"
    material_mode: str = "stress_strain"
    experiment_displacement_scale: float = 1.0
    experiment_force_scale: float = 1.0


@dataclass
class TopologyNode:
    """A two-terminal material component or a recursive series/parallel group."""

    kind: str
    name: str = ""
    material_tag: int | None = None
    children: list["TopologyNode"] = field(default_factory=list)
    area: float | None = None
    length_weight: float = 1.0

    @classmethod
    def material(
        cls,
        material_tag: int,
        *,
        name: str = "",
        area: float | None = None,
        length_weight: float = 1.0,
    ) -> "TopologyNode":
        return cls(
            kind="material",
            name=name,
            material_tag=int(material_tag),
            area=area,
            length_weight=float(length_weight),
        )

    @classmethod
    def group(
        cls,
        kind: str,
        children: list["TopologyNode"],
        *,
        name: str = "",
        length_weight: float = 1.0,
    ) -> "TopologyNode":
        return cls(
            kind=kind,
            name=name,
            children=list(children),
            length_weight=float(length_weight),
        )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TopologyNode":
        if not isinstance(data, dict):
            raise ValueError("modeling.root must be a mapping")
        kind = str(data.get("type", data.get("kind", ""))).lower()
        node = cls(
            kind=kind,
            name=str(data.get("name", "")),
            material_tag=int(data["material_tag"]) if data.get("material_tag") is not None else None,
            children=[cls.from_mapping(child) for child in data.get("children", [])],
            area=_optional_float(data.get("area")),
            length_weight=float(data.get("length_weight", 1.0)),
        )
        node.validate(require_assigned=False)
        return node

    def validate(self, *, require_assigned: bool = True) -> None:
        if self.kind not in {"material", "series", "parallel"}:
            raise ValueError("topology component type must be 'material', 'series', or 'parallel'")
        if not np.isfinite(self.length_weight) or self.length_weight <= 0:
            raise ValueError("topology component length_weight must be positive")
        if self.kind == "material":
            if self.material_tag is None and require_assigned:
                raise ValueError("material topology components require a positive material_tag")
            if self.material_tag is not None and self.material_tag <= 0:
                raise ValueError("material topology components require a positive material_tag")
            if self.children:
                raise ValueError("material topology components cannot contain children")
            if self.area is not None and (not np.isfinite(self.area) or self.area <= 0):
                raise ValueError("material topology component area must be positive")
            return
        if self.material_tag is not None:
            raise ValueError(f"{self.kind} topology groups cannot define material_tag")
        if len(self.children) < 2:
            raise ValueError(f"{self.kind} topology groups require at least two children")
        for child in self.children:
            child.validate(require_assigned=require_assigned)

    def to_mapping(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.kind}
        if self.name:
            data["name"] = self.name
        if not np.isclose(self.length_weight, 1.0):
            data["length_weight"] = self.length_weight
        if self.kind == "material":
            data["material_tag"] = self.material_tag
            if self.area is not None:
                data["area"] = self.area
        else:
            data["children"] = [child.to_mapping() for child in self.children]
        return data

    def material_tags(self) -> list[int]:
        if self.kind == "material":
            return [] if self.material_tag is None else [int(self.material_tag)]
        tags: list[int] = []
        for child in self.children:
            tags.extend(child.material_tags())
        return tags


@dataclass
class ModelingConfig:
    """Controls how material tags are connected in the OpenSees test model."""

    topology: str = "single"
    material_tags: list[int] = field(default_factory=list)
    root: TopologyNode | None = None
    assembly: dict | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "ModelingConfig":
        data = data or {}
        root_data = data.get("root")
        config = cls(
            topology=str(data.get("topology", "nested" if root_data else "single")).lower(),
            material_tags=[int(tag) for tag in data.get("material_tags", [])],
            root=TopologyNode.from_mapping(root_data) if root_data is not None else None,
            assembly=deepcopy(data.get("assembly")),
        )
        config.validate(require_assigned=False)
        return config

    def validate(self, *, require_assigned: bool = True) -> None:
        if self.assembly is not None:
            from material_test.explicit_assembly import validate_assembly
            validate_assembly(self.assembly, ready=require_assigned)
            return
        if self.topology not in {"single", "series", "parallel", "nested"}:
            raise ValueError("modeling.topology must be 'single', 'series', 'parallel', or 'nested'")
        if self.root is not None:
            self.root.validate(require_assigned=require_assigned)
            return
        if self.topology == "nested":
            raise ValueError("nested modeling requires a root topology component")
        if any(tag <= 0 for tag in self.material_tags):
            raise ValueError("modeling.material_tags must contain positive integers")
        if self.topology != "single" and len(self.material_tags) < 2:
            raise ValueError(f"explicit {self.topology} modeling requires at least two material tags")

    def to_mapping(self) -> dict[str, Any]:
        if self.assembly is not None:
            return {"topology": "explicit", "assembly": deepcopy(self.assembly)}
        if self.root is not None:
            return {
                "topology": "nested",
                "root": self.root.to_mapping(),
            }
        return {
            "topology": self.topology,
            "material_tags": list(self.material_tags),
        }

    def resolved_root(self, test_material_tag: int) -> TopologyNode:
        """Return the recursive form while keeping legacy project files readable."""
        if self.assembly is not None:
            raise ValueError("显式节点装配不能转换为串并联本构，请使用显式装配求解。")
        if self.root is not None:
            return self.root
        if self.topology == "single":
            return TopologyNode.material(test_material_tag)
        return TopologyNode.group(
            self.topology,
            [TopologyNode.material(tag) for tag in self.material_tags],
        )


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
