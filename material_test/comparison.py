from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CurveMetrics:
    peak_absolute_force: float
    initial_stiffness: float | None
    hysteretic_integral: float
    residual_force: float


@dataclass(frozen=True)
class ErrorMetrics:
    rmse: float
    normalized_rmse: float


def calculate_curve_metrics(displacement: np.ndarray, force: np.ndarray) -> CurveMetrics:
    displacement = np.asarray(displacement, dtype=float).ravel()
    force = np.asarray(force, dtype=float).ravel()
    if displacement.size == 0 or displacement.shape != force.shape:
        raise ValueError("curve displacement and force must be non-empty and have matching shapes")
    if not np.isfinite(displacement).all() or not np.isfinite(force).all():
        raise ValueError("curve contains non-finite values")

    stiffness = _initial_stiffness(displacement, force)
    integral = float(abs(np.trapezoid(force, displacement)))
    return CurveMetrics(
        peak_absolute_force=float(np.max(np.abs(force))),
        initial_stiffness=stiffness,
        hysteretic_integral=integral,
        residual_force=float(force[-1]),
    )


def calculate_error_metrics(
    force: np.ndarray,
    reference_force: np.ndarray,
) -> ErrorMetrics | None:
    force = np.asarray(force, dtype=float).ravel()
    reference_force = np.asarray(reference_force, dtype=float).ravel()
    if force.size == 0 or force.shape != reference_force.shape:
        return None
    error = force - reference_force
    rmse = float(np.sqrt(np.mean(error**2)))
    scale = max(float(np.max(np.abs(reference_force))), 1.0)
    return ErrorMetrics(rmse=rmse, normalized_rmse=rmse / scale)


def _initial_stiffness(displacement: np.ndarray, force: np.ndarray) -> float | None:
    tolerance = max(float(np.max(np.abs(displacement))) * 1.0e-10, 1.0e-14)
    nonzero = np.flatnonzero(np.abs(displacement) > tolerance)
    if nonzero.size == 0:
        return None

    first = int(nonzero[0])
    direction = float(np.sign(displacement[first]))
    branch: list[int] = []
    previous = float(displacement[first - 1]) if first else 0.0
    for index in range(first, displacement.size):
        current = float(displacement[index])
        increment = current - previous
        if branch and increment * direction < -tolerance:
            break
        if abs(current) > tolerance and current * direction > 0.0:
            branch.append(index)
        previous = current
        if len(branch) >= 8:
            break
    if not branch:
        return None

    u = displacement[branch]
    f = force[branch]
    denominator = float(np.dot(u, u))
    if denominator <= tolerance**2:
        return None
    return float(np.dot(u, f) / denominator)
