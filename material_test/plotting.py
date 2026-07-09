from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
from matplotlib.figure import Figure

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
    *,
    figure: Figure | None = None,
) -> Figure:
    """Create or update a Matplotlib figure with experiment and simulation curves."""
    configure_plot_style()
    fig = figure or Figure(figsize=(8, 6), dpi=100)
    fig.clear()
    ax = fig.add_subplot(111)

    if experiment is not None:
        ax.plot(
            experiment.displacement,
            experiment.force,
            linewidth=2,
            label="Experiment",
        )

    ax.plot(
        simulation.displacement,
        simulation.force,
        linewidth=2,
        label="Simulation",
    )
    ax.legend(loc="best")
    style_force_displacement_axes(fig)
    return fig


def save_overlay_plot(
    output: str | Path,
    simulation: SimulationResult,
    experiment: ExperimentData | None = None,
    *,
    dpi: int = 300,
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig = create_overlay_figure(simulation, experiment)
    fig.savefig(output, dpi=dpi, bbox_inches="tight")
    return output
