from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget

from material_test.domain import ExperimentData, SimulationResult
from material_test.plotting import create_empty_figure, create_overlay_figure


class OverlayPlotWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(7, 5), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas)
        self.clear()

    def clear(self) -> None:
        create_empty_figure(figure=self.figure)
        self.canvas.draw_idle()

    def update_curves(self, simulation: SimulationResult, experiment: ExperimentData | None = None) -> None:
        create_overlay_figure(simulation, experiment, figure=self.figure)
        self.canvas.draw_idle()

    def save_png(self, path: str) -> None:
        self.figure.savefig(path, dpi=300, bbox_inches="tight")
