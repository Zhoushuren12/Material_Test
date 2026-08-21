from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QStyle, QToolButton, QVBoxLayout, QWidget

from material_test.domain import ExperimentData, SimulationResult


class OverlayPlotWidget(QWidget):
    """Interactive experiment/baseline/current comparison using PyQtGraph."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plot_widget = pg.PlotWidget()
        self._series_labels: list[str] = []
        self._configure_plot()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addStretch(1)
        reset_button = QToolButton()
        reset_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        reset_button.setToolTip("自动适配全部曲线")
        reset_button.clicked.connect(self.plot_widget.enableAutoRange)
        toolbar.addWidget(reset_button)
        layout.addLayout(toolbar)
        layout.addWidget(self.plot_widget, 1)
        self.clear()

    def _configure_plot(self) -> None:
        self.plot_widget.setBackground("white")
        self.plot_widget.showAxis("top")
        self.plot_widget.showAxis("right")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.09)
        self.plot_widget.setMenuEnabled(True)
        self.plot_widget.setMouseEnabled(x=True, y=True)
        self.plot_widget.getViewBox().setDefaultPadding(0.02)
        axis_pen = pg.mkPen("#344054", width=1.1)
        text_pen = pg.mkPen("#344054")
        tick_font = QFont("Times New Roman", 11)
        for position in ("bottom", "top", "left", "right"):
            axis = self.plot_widget.getAxis(position)
            axis.setPen(axis_pen)
            axis.setTextPen(text_pen)
            axis.setTickFont(tick_font)
            axis.enableAutoSIPrefix(False)
        self.plot_widget.getAxis("top").setStyle(showValues=False)
        self.plot_widget.getAxis("right").setStyle(showValues=False)
        label_style = {
            "color": "#344054",
            "font-size": "15px",
            "font-family": "Times New Roman, SimSun",
        }
        self.plot_widget.setLabel("bottom", "Displacement", **label_style)
        self.plot_widget.setLabel("left", "Force", **label_style)

    def clear(self) -> None:
        self.plot_widget.clear()
        self._series_labels = []

    def update_curves(self, simulation: SimulationResult, experiment: ExperimentData | None = None) -> None:
        self.update_comparison(simulation, experiment=experiment)

    def update_comparison(
        self,
        current: SimulationResult,
        experiment: ExperimentData | None = None,
        baseline: SimulationResult | None = None,
    ) -> None:
        self.plot_widget.clear()
        self._series_labels = []
        legend = self.plot_widget.addLegend(offset=(12, 12), labelTextColor="#344054")
        legend.setBrush(pg.mkBrush(255, 255, 255, 225))
        if experiment is not None:
            self._add_curve(
                experiment.displacement,
                experiment.force,
                "Experiment",
                pg.mkPen("#d62728", width=1.8),
            )
        if baseline is not None:
            self._add_curve(
                baseline.displacement,
                baseline.force,
                "Baseline",
                pg.mkPen("#667085", width=1.7, style=Qt.PenStyle.DashLine),
            )
        self._add_curve(
            current.displacement,
            current.force,
            "Current",
            pg.mkPen("#1f77b4", width=2.2),
        )
        self.plot_widget.enableAutoRange()

    def _add_curve(
        self,
        displacement: np.ndarray,
        force: np.ndarray,
        label: str,
        pen: QPen,
    ) -> None:
        self.plot_widget.plot(
            np.asarray(displacement, dtype=float),
            np.asarray(force, dtype=float),
            pen=pen,
            name=label,
            antialias=True,
        )
        self._series_labels.append(label)

    @property
    def series_labels(self) -> list[str]:
        return list(self._series_labels)


class HysteresisPreviewWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(5, 3), dpi=100)
        self._cjk_font = FontProperties(family="Microsoft YaHei")
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.clear("选择材料后计算滞回曲线")

    def clear(self, message: str = "暂无曲线") -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            transform=axes.transAxes,
            color="#64748b",
            fontproperties=self._cjk_font,
        )
        axes.set_xticks([])
        axes.set_yticks([])
        for spine in axes.spines.values():
            spine.set_color("#d7dee9")
        self.figure.tight_layout(pad=1.0)
        self.canvas.draw_idle()

    def update_curve(
        self,
        displacement: np.ndarray,
        force: np.ndarray,
        *,
        title: str = "材料滞回响应",
    ) -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.plot(displacement, force, color="#1f5fae", linewidth=1.7)
        axes.axhline(0.0, color="#94a3b8", linewidth=0.7)
        axes.axvline(0.0, color="#94a3b8", linewidth=0.7)
        axes.set_title(title, fontsize=10, fontproperties=self._cjk_font)
        axes.set_xlabel("Displacement", fontsize=8)
        axes.set_ylabel("Force", fontsize=8)
        axes.tick_params(axis="both", direction="in", labelsize=7)
        axes.grid(True, color="#e5eaf1", linewidth=0.6)
        self.figure.tight_layout(pad=1.0)
        self.canvas.draw_idle()


class StagePlotWidget(QWidget):
    """Compact stage-specific plot without the full result toolbar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.figure = Figure(figsize=(5, 3.4), dpi=100)
        self._cjk_font = FontProperties(family="Microsoft YaHei")
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.clear()

    def clear(self, message: str = "暂无可视化数据") -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        axes.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            transform=axes.transAxes,
            color="#64748b",
            fontproperties=self._cjk_font,
        )
        axes.set_xticks([])
        axes.set_yticks([])
        for spine in axes.spines.values():
            spine.set_color("#d7dee9")
        self.figure.tight_layout(pad=1.0)
        self.canvas.draw_idle()

    def update_series(
        self,
        series: list[tuple[np.ndarray, np.ndarray, str, str]],
        *,
        title: str,
        x_label: str,
        y_label: str,
        zero_axes: bool = False,
    ) -> None:
        self.figure.clear()
        axes = self.figure.add_subplot(111)
        for x, y, label, color in series:
            x_values = np.asarray(x, dtype=float)
            y_values = np.asarray(y, dtype=float)
            if x_values.size and y_values.size:
                axes.plot(x_values, y_values, color=color, linewidth=1.65, label=label)
        if zero_axes:
            axes.axhline(0.0, color="#94a3b8", linewidth=0.7)
            axes.axvline(0.0, color="#94a3b8", linewidth=0.7)
        axes.set_title(title, fontsize=10, fontproperties=self._cjk_font)
        axes.set_xlabel(x_label, fontsize=8, fontproperties=self._cjk_font)
        axes.set_ylabel(y_label, fontsize=8, fontproperties=self._cjk_font)
        axes.tick_params(axis="both", direction="in", labelsize=7)
        axes.grid(True, color="#e5eaf1", linewidth=0.6)
        if len(series) > 1 or (series and series[0][2]):
            axes.legend(prop=self._cjk_font, fontsize=8, frameon=False)
        self.figure.tight_layout(pad=1.0)
        self.canvas.draw_idle()


class SpecimenOverviewWidget(QWidget):
    """Project-stage schematic for specimen geometry and solver readiness."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._area = 1.0
        self._length = 1.0
        self._solver_ready = False
        self.setMinimumSize(420, 300)

    def set_config(self, *, area: float, length: float, solver_ready: bool) -> None:
        self._area = float(area)
        self._length = float(length)
        self._solver_ready = bool(solver_ready)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))

        bounds = self.rect().adjusted(24, 22, -24, -22)
        painter.setPen(QColor("#263750"))
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(bounds.left(), bounds.top(), "试件与求解概览")

        center_y = bounds.center().y() - 16
        x0 = bounds.left() + 54
        x1 = bounds.right() - 54
        painter.setPen(QPen(QColor("#64748b"), 2.0))
        painter.drawLine(x0, center_y, x0 + 42, center_y)
        painter.drawLine(x1 - 42, center_y, x1, center_y)
        element_rect = QRectF(x0 + 42, center_y - 25, max(120, x1 - x0 - 84), 50)
        painter.setBrush(QColor("#eaf2ff"))
        painter.setPen(QPen(QColor("#1f5fae"), 1.8))
        painter.drawRoundedRect(element_rect, 4, 4)
        painter.setPen(QColor("#173b70"))
        painter.drawText(element_rect, Qt.AlignmentFlag.AlignCenter, "两端连接材料试件 / 支撑装配")

        painter.setBrush(QColor("#17243a"))
        painter.setPen(QColor("#17243a"))
        painter.drawEllipse(QPointF(x0, center_y), 5, 5)
        painter.drawEllipse(QPointF(x1, center_y), 5, 5)

        dimension_y = center_y + 64
        painter.setPen(QPen(QColor("#94a3b8"), 1.2))
        painter.drawLine(x0, dimension_y, x1, dimension_y)
        painter.drawLine(x0, dimension_y - 6, x0, dimension_y + 6)
        painter.drawLine(x1, dimension_y - 6, x1, dimension_y + 6)
        painter.setPen(QColor("#475569"))
        painter.drawText(x0, dimension_y + 12, x1 - x0, 24, Qt.AlignmentFlag.AlignCenter, f"总长度 L = {self._length:g}")

        badge_y = bounds.bottom() - 48
        area_rect = QRectF(bounds.left(), badge_y, 190, 36)
        status_rect = QRectF(bounds.right() - 210, badge_y, 210, 36)
        painter.setBrush(QColor("#f1f5f9"))
        painter.setPen(QColor("#cbd5e1"))
        painter.drawRoundedRect(area_rect, 4, 4)
        painter.drawText(area_rect, Qt.AlignmentFlag.AlignCenter, f"默认面积 A = {self._area:g}")
        painter.setBrush(QColor("#e8f6ee") if self._solver_ready else QColor("#fff4e5"))
        painter.setPen(QColor("#8cc6a4") if self._solver_ready else QColor("#e6b86a"))
        painter.drawRoundedRect(status_rect, 4, 4)
        painter.setPen(QColor("#26734d") if self._solver_ready else QColor("#9a6700"))
        painter.drawText(
            status_rect,
            Qt.AlignmentFlag.AlignCenter,
            "OpenSees 路径已设置" if self._solver_ready else "等待设置 OpenSees 路径",
        )

class SparklineWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._x: np.ndarray | None = None
        self._y: np.ndarray | None = None
        self._message = ""
        self.setMinimumSize(116, 46)

    def set_curve(self, displacement: np.ndarray | None, force: np.ndarray | None, message: str = "") -> None:
        self._x = None if displacement is None else np.asarray(displacement, dtype=float)
        self._y = None if force is None else np.asarray(force, dtype=float)
        self._message = message
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self._x is None or self._y is None or self._x.size < 2:
            painter.setPen(QColor("#7b8798"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message or "需专用协议")
            return

        margin = 6.0
        width = max(1.0, self.width() - 2.0 * margin)
        height = max(1.0, self.height() - 2.0 * margin)
        x_min, x_max = float(np.min(self._x)), float(np.max(self._x))
        y_min, y_max = float(np.min(self._y)), float(np.max(self._y))
        x_span = max(x_max - x_min, 1.0e-12)
        y_span = max(y_max - y_min, 1.0e-12)

        painter.setPen(QPen(QColor("#d8e0ea"), 1.0))
        if x_min <= 0.0 <= x_max:
            x_zero = margin + (0.0 - x_min) / x_span * width
            painter.drawLine(QPointF(x_zero, margin), QPointF(x_zero, margin + height))
        if y_min <= 0.0 <= y_max:
            y_zero = margin + height - (0.0 - y_min) / y_span * height
            painter.drawLine(QPointF(margin, y_zero), QPointF(margin + width, y_zero))

        path = QPainterPath()
        for index, (x_value, y_value) in enumerate(zip(self._x, self._y)):
            x_pos = margin + (float(x_value) - x_min) / x_span * width
            y_pos = margin + height - (float(y_value) - y_min) / y_span * height
            if index == 0:
                path.moveTo(x_pos, y_pos)
            else:
                path.lineTo(x_pos, y_pos)
        painter.setPen(QPen(QColor("#1f5fae"), 1.5))
        painter.drawPath(path)
