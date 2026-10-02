from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from material_test.domain import ExperimentData, SimulationResult

CELSIUS = chr(0x2103)
TIGHT_LAYOUT_RECT = [0.01, 0.01, 0.95, 0.95]


def configure_plot_style() -> None:
    mpl.rcParams["font.family"] = "serif"
    mpl.rcParams["font.serif"] = ["Times New Roman", "SimSun"]
    mpl.rcParams["font.sans-serif"] = ["SimSun"]
    mpl.rcParams["axes.unicode_minus"] = False
    mpl.rcParams["text.usetex"] = False


def normalize_temperature_label(text: object) -> str:
    replacements = {
        "degC": CELSIUS,
        "DegC": CELSIUS,
        "degree C": CELSIUS,
        "degrees C": CELSIUS,
        chr(0x00B0) + "C": CELSIUS,
        r"$^\circ$C": CELSIUS,
        r"$\degree C$": CELSIUS,
        r"\degree C": CELSIUS,
    }
    normalized = str(text)
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized


def style_force_displacement_axes(
    figure: Figure,
    *,
    xlabel: str = "Displacement",
    ylabel: str = "Force",
) -> None:
    ax = figure.axes[0]
    ax.minorticks_on()
    ax.tick_params(axis="both", direction="in", which="both", labelsize=18)
    ax.set_xlabel(normalize_temperature_label(xlabel), fontsize=25)
    ax.set_ylabel(normalize_temperature_label(ylabel), fontsize=25)
    ax.grid(True, linewidth=0.6)
    figure.tight_layout(rect=TIGHT_LAYOUT_RECT)


def create_empty_figure(*, figure: Figure | None = None) -> Figure:
    configure_plot_style()
    fig = figure or Figure(figsize=(8, 6), dpi=100)
    fig.clear()
    fig.add_subplot(111)
    style_force_displacement_axes(fig)
    return fig


def create_overlay_figure(
    simulation: SimulationResult,
    experiment: ExperimentData | None = None,
    *, figure: Figure | None = None, force_unit: str = "N", length_unit: str = "mm",
) -> Figure:
    return create_comparison_figure(simulation, experiment, figure=figure,
                                    force_unit=force_unit, length_unit=length_unit)


def create_comparison_figure(
    current: SimulationResult,
    experiment: ExperimentData | None = None,
    baseline: SimulationResult | None = None,
    *,
    figure: Figure | None = None,
    force_unit: str = "N", length_unit: str = "mm",
) -> Figure:
    """Create the manual parameter-comparison figure used by the UI."""
    configure_plot_style()
    fig = figure or Figure(figsize=(8, 6), dpi=100)
    fig.clear()
    ax = fig.add_subplot(111)

    x_values: list[np.ndarray] = []
    y_values: list[np.ndarray] = []
    if experiment is not None:
        ax.plot(
            experiment.displacement,
            experiment.force,
            linewidth=2,
            label="试验", color="#111827",
        )
        x_values.append(np.asarray(experiment.displacement, dtype=float))
        y_values.append(np.asarray(experiment.force, dtype=float))
    if baseline is not None:
        ax.plot(
            baseline.displacement,
            baseline.force,
            linewidth=2,
            linestyle="--",
            label="基准", color="#94a3b8",
        )
        x_values.append(np.asarray(baseline.displacement, dtype=float))
        y_values.append(np.asarray(baseline.force, dtype=float))
    ax.plot(
        current.displacement,
        current.force,
        linewidth=2.0,
        label="当前", color="#1f5fae",
    )
    x_values.append(np.asarray(current.displacement, dtype=float))
    y_values.append(np.asarray(current.force, dtype=float))
    _style_publication_comparison_axes(
        ax,
        np.concatenate(x_values),
        np.concatenate(y_values),
        force_unit=force_unit, length_unit=length_unit,
    )
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3, prop={"family": "SimSun", "size": 14}, frameon=False)
    fig.tight_layout(rect=[0.01, 0.01, 0.95, 0.95])
    return fig


def save_comparison_plot(
    output: str | Path,
    current: SimulationResult,
    experiment: ExperimentData | None = None,
    baseline: SimulationResult | None = None,
    *, force_unit: str = "N", length_unit: str = "mm",
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure = create_comparison_figure(current, experiment, baseline, force_unit=force_unit, length_unit=length_unit)
    figure.savefig(output, dpi=300, bbox_inches="tight")
    return output


def _style_publication_comparison_axes(
    axes,
    displacement: np.ndarray,
    force: np.ndarray,
    *, force_unit: str = "N", length_unit: str = "mm",
) -> None:
    x_ticks = _major_ticks(displacement)
    y_ticks = _major_ticks(force)
    axes.set_xticks(x_ticks)
    axes.set_yticks(y_ticks)
    axes.set_xlim(float(x_ticks[0]), float(x_ticks[-1]))
    axes.set_ylim(float(y_ticks[0]), float(y_ticks[-1]))
    axes.minorticks_off()
    axes.tick_params(axis="both", direction="in", which="major", labelsize=18)
    axes.set_xlabel(f"位移 [{length_unit}]", fontsize=25, fontname="SimSun")
    axes.set_ylabel(f"力 [{force_unit}]", fontsize=25, fontname="SimSun")
    axes.grid(True, which="major", linewidth=0.65, alpha=0.22)


def _major_ticks(values: np.ndarray, bins: int = 6) -> np.ndarray:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return np.array([0.0, 1.0])
    lower = float(np.min(finite))
    upper = float(np.max(finite))
    if np.isclose(lower, upper):
        span = max(abs(lower) * 0.1, 1.0)
        lower -= span
        upper += span
    ticks = np.asarray(
        MaxNLocator(nbins=bins, min_n_ticks=3).tick_values(lower, upper),
        dtype=float,
    )
    return ticks if ticks.size >= 2 else np.array([lower, upper])


def save_overlay_plot(
    output: str | Path,
    simulation: SimulationResult,
    experiment: ExperimentData | None = None,
    *, force_unit: str = "N", length_unit: str = "mm",
    dpi: int = 300,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig = create_overlay_figure(simulation, experiment, force_unit=force_unit, length_unit=length_unit)
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    return output
