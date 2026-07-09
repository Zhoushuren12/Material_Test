from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from material_test.domain import LoadingProtocol, ProjectConfig, SolverConfig
from material_test.io.material_templates import load_material_template, material_template_from_mapping


def load_project_config(path: str | Path) -> ProjectConfig:
    path = Path(path).resolve()
    base_dir = path.parent
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"project config must be a mapping: {path}")

    solver_data = data.get("solver")
    if not isinstance(solver_data, dict):
        raise ValueError("project config requires solver mapping")
    solver = SolverConfig.from_mapping(solver_data, base_dir=base_dir)

    material_template_path = _resolve_path(data.get("material_template"), base_dir)
    material_data = data.get("material")
    if material_template_path:
        material = load_material_template(material_template_path)
        if isinstance(material_data, dict):
            for name, value in material_data.get("parameter_values", {}).items():
                material.set_parameter_value(name, float(value))
    elif isinstance(material_data, dict):
        material = material_template_from_mapping(material_data)
    else:
        raise ValueError("project config requires material_template or inline material")

    loading_data = data.get("loading")
    if not isinstance(loading_data, dict):
        raise ValueError("project config requires loading mapping")
    loading = LoadingProtocol.from_mapping(loading_data, base_dir=base_dir)

    experiment = data.get("experiment") or {}
    if experiment is None:
        experiment = {}
    if not isinstance(experiment, dict):
        raise ValueError("experiment must be a mapping when provided")

    return ProjectConfig(
        solver=solver,
        material=material,
        loading=loading,
        experiment_path=_resolve_path(experiment.get("path"), base_dir),
        experiment_sheet=experiment.get("sheet", 0),
        displacement_column=experiment.get("displacement_column"),
        force_column=experiment.get("force_column"),
        output_dir=_resolve_path(data.get("output_dir"), base_dir) or (base_dir / "outputs" / "runs"),
        material_template_path=material_template_path,
        area=float(data.get("area", 1.0)),
        length=float(data.get("length", 1.0)),
    )


def save_project_config(config: ProjectConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "solver": config.solver.to_mapping(),
        "loading": config.loading.to_mapping(),
        "experiment": {
            "path": str(config.experiment_path) if config.experiment_path else None,
            "sheet": config.experiment_sheet,
            "displacement_column": config.displacement_column,
            "force_column": config.force_column,
        },
        "output_dir": str(config.output_dir),
        "area": config.area,
        "length": config.length,
    }
    if config.material_template_path:
        data["material_template"] = str(config.material_template_path)
        data["material"] = {
            "parameter_values": config.material.parameter_values,
        }
    else:
        data["material"] = config.material.to_mapping()
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _resolve_path(value: Any, base_dir: Path) -> Path | None:
    if value is None or value == "":
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()
