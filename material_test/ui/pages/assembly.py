from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QFrame,
    QSpinBox, QSplitter, QTableWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget,
    QStyle, QMenu, QDockWidget, QProgressBar,
)
from material_test.ui.editors import CompactDoubleSpinBox, MaterialTagSpinBox
from material_test.ui.plot_canvas import HysteresisPreviewWidget, OverlayPlotWidget, SpecimenOverviewWidget, StagePlotWidget
from material_test.ui.topology_editor import TopologyEditor
ROOT = Path(__file__).resolve().parents[3]

def build(self) -> QWidget:
    splitter = QSplitter(Qt.Orientation.Horizontal)
    self.topology_editor = TopologyEditor()
    self.topology_editor.changed.connect(self._on_topology_changed)

    response_panel = QWidget()
    response_layout = QVBoxLayout(response_panel)
    response_layout.setContentsMargins(18, 0, 0, 0)
    response_layout.setSpacing(8)
    response_header = QHBoxLayout()
    response_header.addWidget(self._section_title("支撑装配整体响应"))
    response_header.addStretch(1)
    self.model_preview_protocol_combo = QComboBox()
    self.model_preview_protocol_combo.addItem("当前加载制度", "current")
    self.model_preview_protocol_combo.addItem("材料标准循环", "standard")
    self.calculate_model_preview_button = QPushButton("计算整体曲线")
    self.calculate_model_preview_button.setObjectName("primaryButton")
    self.calculate_model_preview_button.clicked.connect(self.calculate_model_preview)
    self.model_preview_protocol_combo.hide()
    source_label = QLabel(); source_label.setWordWrap(True)
    self._source_badges.append(source_label); response_layout.addWidget(source_label)
    response_header.addWidget(self.calculate_model_preview_button)
    response_layout.addLayout(response_header)
    self.model_assignment_label = QLabel("正在检查材料赋值…")
    self.model_assignment_label.setObjectName("previewNote")
    self.model_assignment_label.setWordWrap(True)
    response_layout.addWidget(self.model_assignment_label)
    self.model_preview_plot = HysteresisPreviewWidget()
    self.model_preview_plot.clear("完成材料赋值后计算整体响应")
    self.model_preview_plot.setMinimumHeight(330)
    response_layout.addWidget(self.model_preview_plot, 1)
    self.model_preview_note = QLabel("曲线由当前拓扑、材料参数和加载制度共同决定")
    self.model_preview_note.setObjectName("previewNote")
    self.model_preview_note.setWordWrap(True)
    response_layout.addWidget(self.model_preview_note)

    splitter.addWidget(self.topology_editor)
    splitter.addWidget(response_panel)
    splitter.setSizes([600, 600])
    return splitter


class AssemblyPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build(window))
