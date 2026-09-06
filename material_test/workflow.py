from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from material_test.domain import ExperimentData, ProjectConfig, SimulationResult
from material_test.io.experiment_data import load_experiment_data
from material_test.opensees.runner import run_material_history
from material_test.plotting import save_overlay_plot
from material_test.protocols import history_from_protocol


@dataclass
class WorkflowResult:
    run_dir: Path
    simulation: SimulationResult
    experiment: ExperimentData | None
    figure_file: Path
    simulation_csv: Path


def run_project(config: ProjectConfig, *, run_dir: Path | None = None) -> WorkflowResult:
    from material_test.brace_export import input_identity, checksum
    import json
    import hashlib
    identity = input_identity(config)
    run_dir = (run_dir or _default_run_dir(config.output_dir, config.material.name)).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    experiment = _load_optional_experiment(config)
    displacement_history = history_from_protocol(config.loading)

    simulation = run_material_history(
        solver=config.solver,
        material=config.material,
        displacement_history=displacement_history,
        work_dir=run_dir,
        area=config.area,
        length=config.length,
        modeling=config.modeling,
    )

    simulation_csv = run_dir / "simulation_curve.csv"
    np.savetxt(
        simulation_csv,
        np.column_stack([simulation.displacement, simulation.force]),
        delimiter=",",
        header="displacement,force",
        comments="",
    )
    figure_file = save_overlay_plot(run_dir / "overlay.png", simulation, experiment)
    if checksum(input_identity(config)) != checksum(identity):
        raise ValueError('计算期间输入发生变化，请重新计算以建立可靠的实验与计算来源')
    (run_dir / 'input-manifest.json').write_text(
        json.dumps({'input_sha256': checksum(identity), 'input_snapshot': identity,
                    'simulation_sha256': hashlib.sha256(simulation_csv.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2),
        encoding='utf-8')

    return WorkflowResult(
        run_dir=run_dir,
        simulation=simulation,
        experiment=experiment,
        figure_file=figure_file,
        simulation_csv=simulation_csv,
    )


def _load_optional_experiment(config: ProjectConfig) -> ExperimentData | None:
    if not config.experiment_path:
        return None
    if config.displacement_column is None or config.force_column is None:
        raise ValueError("experiment displacement_column and force_column are required when experiment.path is set")
    return load_experiment_data(
        config.experiment_path,
        displacement_column=config.displacement_column,
        force_column=config.force_column,
        sheet_name=config.experiment_sheet,
    )


def _default_run_dir(output_dir: Path, material_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_name = "".join(
        char.lower() if char.isascii() and char.isalnum() else "_"
        for char in material_name
    ).strip("_")
    return Path(output_dir) / f"{timestamp}-{safe_name or 'material'}"
