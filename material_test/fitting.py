from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import yaml
from scipy.optimize import differential_evolution, least_squares

from material_test.domain import ExperimentData, FitConfig, MaterialTemplate, ProjectConfig, SimulationResult
from material_test.io.experiment_data import load_experiment_data
from material_test.opensees.runner import run_material_history
from material_test.plotting import save_overlay_plot


class EvaluationBudgetExhausted(RuntimeError):
    pass


@dataclass
class FitMetrics:
    rmse: float
    normalized_rmse: float
    mae: float
    max_absolute_error: float


@dataclass
class FitResult:
    run_dir: Path
    simulation: SimulationResult
    experiment: ExperimentData
    fitted_material: MaterialTemplate
    metrics: FitMetrics
    global_algorithm: str
    global_evaluations: int
    local_evaluations: int
    total_evaluations: int
    figure_file: Path
    simulation_csv: Path
    material_file: Path
    summary_file: Path


class _FittingEvaluator:
    def __init__(self, config: ProjectConfig, experiment: ExperimentData, run_dir: Path) -> None:
        if config.fitting is None:
            raise ValueError("fitting configuration is required")
        self.config = config
        self.fit_config = config.fitting
        self.experiment = experiment
        self.run_dir = run_dir
        self.parameter_specs = self.fit_config.parameters
        self.lower, self.upper = _transformed_bounds(self.fit_config)
        self.force_scale = max(float(np.max(np.abs(experiment.force))), 1.0)
        self.global_evaluations = 0
        self.local_evaluations = 0
        self.total_evaluations = 0
        self.best_x: np.ndarray | None = None
        self.best_value = np.inf

    def global_objective(self, x: np.ndarray) -> float:
        residual = self._evaluate(x, phase="global")
        return float(np.mean(residual**2))

    def local_residual(self, x: np.ndarray) -> np.ndarray:
        return self._evaluate(x, phase="local")

    def _evaluate(self, x: np.ndarray, *, phase: str) -> np.ndarray:
        if phase == "global":
            if self.global_evaluations >= self.fit_config.max_evaluations:
                raise EvaluationBudgetExhausted
            self.global_evaluations += 1
        elif phase == "local":
            if self.local_evaluations >= self.fit_config.local_max_evaluations:
                raise EvaluationBudgetExhausted
            self.local_evaluations += 1
        else:
            raise ValueError(f"unknown fitting phase: {phase}")

        self.total_evaluations += 1
        x = np.asarray(x, dtype=float)
        candidate_dir = self.run_dir / "candidates" / f"evaluation-{self.total_evaluations:05d}"
        try:
            material = _material_with_values(self.config.material, self.fit_config, x)
            simulation = run_material_history(
                solver=self.config.solver,
                material=material,
                displacement_history=self.experiment.displacement,
                work_dir=candidate_dir,
                area=self.config.area,
                length=self.config.length,
                modeling=self.config.modeling,
            )
            force = _force_at_experiment_steps(simulation, self.experiment)
            residual = (force - self.experiment.force) / self.force_scale
            if not np.isfinite(residual).all():
                raise ValueError("simulation returned non-finite force values")
        except Exception:
            # A failed OpenSees candidate must be worse than a viable fit but must
            # not terminate a population-based search.
            residual = np.full(self.experiment.force.shape, 10.0, dtype=float)

        value = float(np.mean(residual**2))
        if value < self.best_value:
            self.best_value = value
            self.best_x = x.copy()
        return residual


def fit_project(config: ProjectConfig, *, run_dir: Path | None = None) -> FitResult:
    """Fit selected material parameters to the experiment displacement history."""
    fit_config = config.fitting
    if fit_config is None:
        raise ValueError("project config requires a fitting section for the fit workflow")
    fit_config.validate()
    _validate_fitting_parameters(config.material, fit_config)
    experiment = _load_experiment(config)
    if experiment is None:
        raise ValueError("automatic fitting requires experiment.path and its displacement/force columns")

    run_dir = (run_dir or _default_fit_dir(config.output_dir, config.material.name)).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    evaluator = _FittingEvaluator(config, experiment, run_dir)
    global_x = _run_global_search(evaluator)
    best_x = global_x

    if fit_config.local_refine:
        try:
            refined = least_squares(
                evaluator.local_residual,
                x0=global_x,
                bounds=(evaluator.lower, evaluator.upper),
                method="trf",
                loss=fit_config.loss,
                f_scale=fit_config.f_scale,
                x_scale="jac",
                max_nfev=fit_config.local_max_evaluations,
            )
            best_x = np.asarray(refined.x, dtype=float)
        except EvaluationBudgetExhausted:
            # The best evaluated candidate remains valid when local refinement
            # consumes its evaluation budget during numerical differentiation.
            best_x = evaluator.best_x if evaluator.best_x is not None else global_x

    if evaluator.best_x is not None:
        best_x = evaluator.best_x
    fitted_material = _material_with_values(config.material, fit_config, best_x)
    simulation = run_material_history(
        solver=config.solver,
        material=fitted_material,
        displacement_history=experiment.displacement,
        work_dir=run_dir / "best",
        area=config.area,
        length=config.length,
        modeling=config.modeling,
    )
    fitted_force = _force_at_experiment_steps(simulation, experiment)
    metrics = _metrics(fitted_force, experiment.force)

    simulation_csv = run_dir / "simulation_curve.csv"
    np.savetxt(
        simulation_csv,
        np.column_stack([simulation.displacement, simulation.force]),
        delimiter=",",
        header="displacement,force",
        comments="",
    )
    figure_file = save_overlay_plot(run_dir / "fit_overlay.png", simulation, experiment)
    material_file = run_dir / "fitted_material.yaml"
    material_file.write_text(yaml.safe_dump(fitted_material.to_mapping(), sort_keys=False), encoding="utf-8")
    summary_file = run_dir / "fit_summary.json"
    summary_file.write_text(
        json.dumps(
            {
                "algorithm": fit_config.algorithm,
                "best_parameters": fitted_material.parameter_values,
                "metrics": asdict(metrics),
                "global_evaluations": evaluator.global_evaluations,
                "local_evaluations": evaluator.local_evaluations,
                "total_evaluations": evaluator.total_evaluations,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    return FitResult(
        run_dir=run_dir,
        simulation=simulation,
        experiment=experiment,
        fitted_material=fitted_material,
        metrics=metrics,
        global_algorithm=fit_config.algorithm,
        global_evaluations=evaluator.global_evaluations,
        local_evaluations=evaluator.local_evaluations,
        total_evaluations=evaluator.total_evaluations,
        figure_file=figure_file,
        simulation_csv=simulation_csv,
        material_file=material_file,
        summary_file=summary_file,
    )


def _run_global_search(evaluator: _FittingEvaluator) -> np.ndarray:
    if evaluator.fit_config.algorithm == "pso":
        return _particle_swarm(evaluator)

    n_parameters = len(evaluator.parameter_specs)
    requested_population = min(evaluator.fit_config.population_size, evaluator.fit_config.max_evaluations)
    population_multiplier = max(1, int(np.ceil(requested_population / n_parameters)))
    population_size = max(5, population_multiplier * n_parameters)
    max_iterations = max(0, evaluator.fit_config.max_evaluations // population_size - 1)
    try:
        result = differential_evolution(
            evaluator.global_objective,
            bounds=list(zip(evaluator.lower, evaluator.upper, strict=True)),
            popsize=population_multiplier,
            maxiter=max_iterations,
            polish=False,
            seed=evaluator.fit_config.seed,
            updating="immediate",
            workers=1,
        )
        return np.asarray(result.x, dtype=float)
    except EvaluationBudgetExhausted:
        if evaluator.best_x is None:
            raise RuntimeError("differential evolution did not evaluate a candidate")
        return evaluator.best_x


def _particle_swarm(evaluator: _FittingEvaluator) -> np.ndarray:
    rng = np.random.default_rng(evaluator.fit_config.seed)
    dimensions = len(evaluator.parameter_specs)
    swarm_size = min(evaluator.fit_config.population_size, evaluator.fit_config.max_evaluations)
    lower, upper = evaluator.lower, evaluator.upper
    positions = lower + rng.random((swarm_size, dimensions)) * (upper - lower)
    velocities = np.zeros_like(positions)
    personal_best = positions.copy()
    personal_scores = np.full(swarm_size, np.inf)

    for index in range(swarm_size):
        personal_scores[index] = evaluator.global_objective(positions[index])
    global_index = int(np.argmin(personal_scores))
    global_best = personal_best[global_index].copy()
    global_score = float(personal_scores[global_index])

    # Clerc's constriction coefficients provide stable default PSO dynamics.
    inertia, cognitive, social = 0.7298, 1.49618, 1.49618
    while evaluator.global_evaluations < evaluator.fit_config.max_evaluations:
        for index in range(swarm_size):
            if evaluator.global_evaluations >= evaluator.fit_config.max_evaluations:
                break
            velocities[index] = (
                inertia * velocities[index]
                + cognitive * rng.random(dimensions) * (personal_best[index] - positions[index])
                + social * rng.random(dimensions) * (global_best - positions[index])
            )
            positions[index] = np.clip(positions[index] + velocities[index], lower, upper)
            score = evaluator.global_objective(positions[index])
            if score < personal_scores[index]:
                personal_best[index] = positions[index]
                personal_scores[index] = score
                if score < global_score:
                    global_best = positions[index].copy()
                    global_score = float(score)
    return global_best


def _material_with_values(material: MaterialTemplate, fit_config: FitConfig, x: np.ndarray) -> MaterialTemplate:
    candidate = copy.deepcopy(material)
    for spec, transformed_value in zip(fit_config.parameters, x, strict=True):
        value = float(np.exp(transformed_value)) if spec.scale == "log" else float(transformed_value)
        candidate.set_parameter_value(spec.name, value)
    return candidate


def _transformed_bounds(fit_config: FitConfig) -> tuple[np.ndarray, np.ndarray]:
    lower = []
    upper = []
    for spec in fit_config.parameters:
        if spec.scale == "log":
            lower.append(np.log(spec.min))
            upper.append(np.log(spec.max))
        else:
            lower.append(spec.min)
            upper.append(spec.max)
    return np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)


def _validate_fitting_parameters(material: MaterialTemplate, fit_config: FitConfig) -> None:
    values = material.parameter_values
    missing = [spec.name for spec in fit_config.parameters if spec.name not in values]
    if missing:
        raise ValueError(f"fitting parameters are not defined by the material: {', '.join(missing)}")


def _load_experiment(config: ProjectConfig) -> ExperimentData | None:
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


def _force_at_experiment_steps(simulation: SimulationResult, experiment: ExperimentData) -> np.ndarray:
    if simulation.force.size == experiment.force.size:
        return simulation.force
    if simulation.force.size == experiment.force.size + 1 and np.isclose(simulation.displacement[0], 0.0):
        return simulation.force[1:]
    raise ValueError(
        "simulation did not complete the experimental displacement history "
        f"({simulation.force.size} simulated steps, {experiment.force.size} experimental steps)"
    )


def _metrics(simulated_force: np.ndarray, experimental_force: np.ndarray) -> FitMetrics:
    error = np.asarray(simulated_force) - np.asarray(experimental_force)
    scale = max(float(np.max(np.abs(experimental_force))), 1.0)
    return FitMetrics(
        rmse=float(np.sqrt(np.mean(error**2))),
        normalized_rmse=float(np.sqrt(np.mean(error**2)) / scale),
        mae=float(np.mean(np.abs(error))),
        max_absolute_error=float(np.max(np.abs(error))),
    )


def _default_fit_dir(output_dir: Path, material_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(char.lower() if char.isalnum() else "_" for char in material_name).strip("_")
    return Path(output_dir) / f"{timestamp}-{safe_name or 'material'}-fit"
