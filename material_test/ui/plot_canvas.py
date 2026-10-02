from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyle, QToolButton, QVBoxLayout, QWidget

from material_test.domain import ExperimentData, SimulationResult


class OverlayPlotWidget(QWidget):
    """Shared interactive plot for previews, loading and comparison."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.force_unit, self.length_unit = 'N', 'mm'
        self._series_labels = []
        self._current_curve = None
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('white')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.12)
        self.plot_widget.setMenuEnabled(True)
        for position in ('bottom', 'left', 'top', 'right'):
            axis = self.plot_widget.getAxis(position)
            axis.setTickFont(QFont('Microsoft YaHei UI', 9))
            axis.enableAutoSIPrefix(False)
        self.message_label = QLabel()
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet('color:#64748b; background:transparent; padding:14px;')
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.caption, 1)
        reset = QToolButton()
        reset.setText('适配曲线')
        reset.clicked.connect(self.plot_widget.enableAutoRange)
        toolbar.addWidget(reset)
        layout.addLayout(toolbar)
        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.plot_widget, 1)
        self.set_units('N', 'mm')
        self.clear()

    def set_units(self, force, length):
        self.force_unit, self.length_unit = force, length
        self.plot_widget.setLabel('bottom', f'位移 [{length}]')
        self.plot_widget.setLabel('left', f'力 [{force}]')

    def clear(self, message='暂无计算曲线，请设置材料和加载后计算。'):
        self.plot_widget.clear()
        self._series_labels = []
        self._current_curve = None
        self.message_label.setText(message)
        self.message_label.show()
        self.plot_widget.hide()
        self.caption.clear()

    def _begin(self):
        self.plot_widget.clear()
        legend = self.plot_widget.getPlotItem().legend
        if legend is not None:
            legend.clear()
        else:
            self.plot_widget.addLegend(offset=(12, 12))
        self._series_labels = []
        self._current_curve = None
        self.message_label.hide()
        self.plot_widget.show()
        self.caption.setStyleSheet('color:#334155;')

    def _add_curve(self, displacement, force, label, pen):
        item = self.plot_widget.plot(np.asarray(displacement), np.asarray(force), pen=pen, name=label, antialias=True)
        self._series_labels.append(label)
        return item

    @property
    def series_labels(self):
        return list(self._series_labels)

    def update_curves(self, simulation, experiment=None):
        self.update_comparison(simulation, experiment=experiment)

    def update_comparison(self, current, experiment=None, baseline=None, stale=False):
        self._begin()
        self.caption.setText('试验 / 基准 / 当前')
        if experiment is not None:
            self._add_curve(experiment.displacement, experiment.force, '试验', pg.mkPen('#111827', width=1.8))
        if baseline is not None:
            self._add_curve(baseline.displacement, baseline.force, '基准', pg.mkPen('#94a3b8', width=1.7, style=Qt.PenStyle.DashLine))
        self._current_curve = self._add_curve(current.displacement, current.force, '当前', pg.mkPen('#1f5fae', width=2))
        self.set_stale(stale)
        self.plot_widget.enableAutoRange()

    def set_stale(self, stale=True):
        if self._current_curve is None:
            return
        self._current_curve.setPen(pg.mkPen('#9ca3af' if stale else '#1f5fae', width=2,
            style=Qt.PenStyle.DashLine if stale else Qt.PenStyle.SolidLine))
        if stale:
            self.caption.setText('曲线已过期 · 请重新计算')
            self.caption.setStyleSheet('color:#a16207; background:#fffbeb; padding:5px;')
        else:
            self.caption.setText('当前计算结果')
            self.caption.setStyleSheet('color:#334155;')


class HysteresisPreviewWidget(OverlayPlotWidget):
    def update_curve(self, displacement, force, *, title='材料滞回响应', experiment=None):
        self._begin()
        self.caption.setText(title)
        if experiment is not None:
            self._add_curve(experiment.displacement, experiment.force, '试验', pg.mkPen('#111827', width=1.8))
        self._current_curve = self._add_curve(displacement, force, '当前', pg.mkPen('#1f5fae', width=2))
        self.plot_widget.enableAutoRange()


class StagePlotWidget(OverlayPlotWidget):
    def update_series(self, series, *, title, x_label, y_label, zero_axes=False):
        self._begin()
        self.caption.setText(title)
        self.plot_widget.setLabel('bottom', f'{x_label} [{self.length_unit}]' if x_label == '位移' else x_label)
        self.plot_widget.setLabel('left', f'{y_label} [{self.force_unit}]' if y_label == '力' else y_label)
        for x, y, label, color in series:
            if len(x):
                item = self._add_curve(x, y, label, pg.mkPen('#111827' if '试验' in label else color, width=1.8))
                if '试验' not in label:
                    self._current_curve = item
        self.plot_widget.enableAutoRange()


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
