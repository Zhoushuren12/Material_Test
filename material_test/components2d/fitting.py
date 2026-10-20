from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
from scipy.optimize import differential_evolution, minimize

from material_test.cancellation import AnalysisCancelled
from .comparison import image_metrics
from .digitization import load_digitized
from .model import save_component
from .solver import run_component


@dataclass
class ComponentFitResult:
    project: object
    simulation: object
    fit_dir: Path
    objective: float
    evaluations: int
    observation_metrics: list[dict]


def apply_parameters(project, specs, values):
    candidate = project.clone()
    for spec, value in zip(specs, values):
        for target in spec["targets"]:
            bound_value = float(value)*float(target.get('factor',1.))+float(target.get('offset',0.))
            if not np.isfinite(bound_value):
                raise ValueError('Parameter binding returned a non-finite value')
            if "material" in target:
                material = next(m for m in candidate.data["materials"] if m["tag"] == target["material"])
                parameter = next(p for p in material["parameters"] if p["name"] == target["parameter"])
                parameter["value"] = bound_value
            elif "element" in target:
                element = next(e for e in candidate.data["elements"] if e["tag"] == target["element"])
                if target["field"] not in {"A", "E", "Iz"}:
                    raise ValueError("Only explicit element A/E/Iz scalar bindings supported")
                element[target["field"]] = bound_value
            else:
                raise ValueError("Fitting target must identify material parameter or member property")
    candidate.validate()
    return candidate


def load_observations(project):
    observations = []
    channels = {c['name']:c for c in project.data['channels']}
    for source in project.data.get("fitting", {}).get("observations", []):
        path = Path(source["path"])
        if not path.is_absolute():
            path = project.base_dir / path
        if source["kind"] == "image":
            observed = load_digitized(path)
            for key,unit in (('x',observed.calibration.x_unit),('y',observed.calibration.y_unit)):
                declared = channels.get(source[key],{}).get('unit')
                if declared and declared != unit and key+'_scale' not in source:
                    raise ValueError(f'Image {key} unit {unit} does not match channel {declared}; declare signed conversion explicitly')
            observations.append((source, observed))
        elif source["kind"] == "history":
            values = np.genfromtxt(path, delimiter=",", names=True)
            observed = np.column_stack((values[source.get("x_column", "displacement")], values[source.get("y_column", "force")]))
            if len(observed) < 2 or not np.isfinite(observed).all():
                raise ValueError('Ordered observations must contain at least two finite samples')
            observations.append((source, observed))
        else:
            raise ValueError("Observation kind must be image or history")
        for key in ('x_scale','y_scale'):
            value = float(source.get(key,1.))
            if not np.isfinite(value) or value == 0:
                raise ValueError('Observation conversions must be finite and nonzero')
    if not observations:
        raise ValueError("Component fitting requires explicit observations")
    return observations


def evaluate_observations(result, observations):
    metrics, objective, total_weight = [], 0., 0.
    for source, observed in observations:
        x, y = result.channel(source["x"]), result.channel(source["y"])
        xscale,yscale = float(source.get('x_scale',1.)),float(source.get('y_scale',1.))
        if source["kind"] == "image":
            item = image_metrics(observed, x/xscale, y/yscale)
            score = item["symmetric_distance_pixels"]
        else:
            if len(x) != len(observed) or not np.allclose(x, observed[:, 0]*xscale, atol=1e-8, rtol=1e-8):
                raise ValueError("Ordered observations require the same complete loading sequence; no curve interpolation")
            scale = max(float(np.max(np.abs(observed[:, 1]*yscale))), 1e-12)
            rmse = float(np.sqrt(np.mean((y-observed[:, 1]*yscale)**2)))
            item = {"sequence_rmse": rmse, "normalized_rmse": rmse/scale}
            score = rmse/scale
        weight = float(source.get("weight", 1.))
        if not np.isfinite(weight) or weight <= 0:
            raise ValueError("Observation weights must be finite and positive")
        metrics.append({"kind": source["kind"], "source": source["path"], "x": source["x"], "y": source["y"],
                        'x_scale':xscale,'y_scale':yscale, **item})
        objective += weight*score; total_weight += weight
    return objective/total_weight, metrics


def fit_component(project, *, fit_dir=None, progress=None, cancel_check=None):
    project.validate()
    settings = project.data["fitting"]
    specs = settings["parameters"]
    if not specs or len({s["name"] for s in specs}) != len(specs):
        raise ValueError("Unique selected fitting parameters required")
    bounds = np.asarray([(float(s["min"]), float(s["max"])) for s in specs])
    if not np.isfinite(bounds).all() or np.any(bounds[:, 0] >= bounds[:, 1]):
        raise ValueError("Finite increasing parameter bounds required")
    logarithmic = np.asarray([s.get("scale", "linear") == "log" for s in specs])
    if np.any(bounds[logarithmic] <= 0):
        raise ValueError("Logarithmic bounds must be positive")
    transformed = bounds.copy(); transformed[logarithmic] = np.log(bounds[logarithmic])
    def decode(x):
        values = np.asarray(x).copy(); values[logarithmic] = np.exp(values[logarithmic])
        return values
    observations = load_observations(project)
    requested = {s[0][key] for s in observations for key in ('x','y')}
    available = {c['name'] for c in project.data['channels']} | {'input','time','sample','load_multiplier'}
    if not requested <= available:
        raise ValueError('Fitting observations reference undefined response channels')
    root = project.output_root
    directory = Path(fit_dir).resolve() if fit_dir else root / (datetime.now(timezone.utc).strftime("fit-%Y%m%d-%H%M%S-")+uuid4().hex[:8])
    if not directory.is_relative_to(root):
        raise ValueError("Fit outputs must stay inside the project output directory")
    directory.mkdir(parents=True, exist_ok=False)
    save_component(project, directory / "input.yaml")
    budget = int(settings.get("max_evaluations", 300))
    if budget < 2:
        raise ValueError("At least two fitting evaluations required")
    best, best_values, count = np.inf, None, 0
    class BudgetExhausted(Exception):
        pass
    with (directory / "evaluations.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream); writer.writerow(["evaluation", "objective", "valid", *[s["name"] for s in specs]])
        def objective(x):
            nonlocal best, best_values, count
            if cancel_check and cancel_check():
                raise AnalysisCancelled("Component fitting cancelled; original parameters unchanged")
            if count >= budget:
                raise BudgetExhausted
            count += 1; values = decode(x); valid = True
            try:
                candidate = apply_parameters(project, specs, values)
                result = run_component(candidate, run_dir=directory / "scratch", cancel_check=cancel_check, scratch=True)
                score, _ = evaluate_observations(result, observations)
                if not np.isfinite(score):
                    raise ValueError("Non-finite fitting score")
            except AnalysisCancelled:
                raise
            except Exception as exc:
                score, valid = 1e6, False
                (directory / "last_failure.txt").write_text(str(exc), encoding="utf-8")
            writer.writerow([count, score, valid, *values]); stream.flush()
            if valid and score < best:
                best, best_values = score, values.copy()
                save_component(candidate, directory / "best_so_far.yaml")
            if progress:
                progress(count, budget, float(best))
            return score
        initial = []
        for spec in specs:
            target = spec["targets"][0]
            if "material" in target:
                material = project.materials[target["material"]]
                initial.append(material.parameter_values[target["parameter"]])
            else:
                initial.append(next(e for e in project.data["elements"] if e["tag"] == target["element"])[target["field"]])
            factor = float(target.get('factor',1.))
            if not np.isfinite(factor) or factor == 0:
                raise ValueError('Fitting parameter binding factor must be finite and nonzero')
            initial[-1] = (initial[-1]-float(target.get('offset',0.)))/factor
        initial = np.clip(initial, bounds[:, 0], bounds[:, 1]); initial[logarithmic] = np.log(initial[logarithmic])
        try:
            objective(initial)
            optimum = initial
            if settings.get('global_search',True):
                population_size = max(5, int(settings.get("population_size", 24)))
                generator = np.random.default_rng(int(settings.get("seed", 42)))
                population = generator.uniform(transformed[:, 0], transformed[:, 1], size=(population_size, len(specs)))
                population[0] = initial
                result = differential_evolution(objective, transformed.tolist(), init=population,
                                                maxiter=max(1, int(budget*.7/population_size)-1), polish=False,
                                                seed=int(settings.get("seed", 42)), tol=1e-5)
                optimum = result.x
            minimize(objective, optimum, method="Powell", bounds=transformed.tolist(), options={"maxfev": max(1, budget-count)})
        except BudgetExhausted:
            pass
    if best_values is None:
        raise RuntimeError(f"No valid complete component candidates; see {directory}")
    fitted = apply_parameters(project, specs, best_values)
    image_fit = any(s[0]['kind'] == 'image' for s in observations)
    fitted.data["evidence"] = {**fitted.data.get("evidence", {}), "fit_type": "image-shape" if image_fit else "ordered-history"}
    if image_fit: fitted.data['evidence']['geometry_and_protocol_assumptions'] = True
    simulation = run_component(fitted, run_dir=directory / "verified_best", cancel_check=cancel_check)
    verified_objective, metrics = evaluate_observations(simulation, observations)
    if not np.isclose(verified_objective,best,atol=1e-7,rtol=1e-7):
        raise RuntimeError('Fresh best-model verification did not reproduce the fitting objective')
    fitted.data['last_result'] = str(simulation.run_dir)
    save_component(fitted, directory / "fitted_project.yaml")
    summary = {"schema": "material-test/component-fit/v1", "objective": verified_objective, "evaluations": count,
               "parameters": {s["name"]: float(v) for s, v in zip(specs, best_values)}, "observations": metrics,
               "parameter_identifiability": "not established by image matching" if image_fit else 'requires sensitivity/identifiability assessment', "experimental_time_history_recovered": False,
               "geometry_status": fitted.data.get("evidence", {}).get("geometry_status", "user declared")}
    (directory / "fit_summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    return ComponentFitResult(fitted, simulation, directory, verified_objective, count, metrics)
