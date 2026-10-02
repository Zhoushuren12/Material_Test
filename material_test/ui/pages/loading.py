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
    controls = QWidget()
    controls_layout = QVBoxLayout(controls)
    controls_layout.setContentsMargins(0, 0, 18, 0)
    controls_layout.setSpacing(10)
    self.loading_source_combo = QComboBox()
    self.loading_source_combo.addItem("幅值序列", "amplitudes")
    self.loading_source_combo.addItem("外部文件", "external")
    self.loading_source_combo.addItem("试验位移", "experiment")
    controls_layout.addWidget(QLabel("全局加载来源（所有页面共用）"))
    controls_layout.addWidget(self.loading_source_combo)
    controls_layout.addWidget(self._section_title("循环加载制度"))
    template_row = QHBoxLayout()
    self.loading_template_combo = QComboBox()
    self.loading_template_combo.addItem("等幅循环", "constant")
    self.loading_template_combo.addItem("逐级递增", "increasing")
    self.loading_amplitude_spin = CompactDoubleSpinBox(); self.loading_amplitude_spin.setRange(1e-8, 1e9); self.loading_amplitude_spin.setValue(10)
    self.loading_amplitude_spin.setToolTip("最大位移幅值（项目长度单位）")
    self.loading_cycles_spin = QSpinBox(); self.loading_cycles_spin.setRange(1, 100); self.loading_cycles_spin.setValue(2); self.loading_cycles_spin.setSuffix(" 圈/级")
    apply_template = QPushButton("填入"); apply_template.clicked.connect(self.apply_loading_template)
    for widget in (self.loading_template_combo, apply_template): template_row.addWidget(widget)
    controls_layout.addLayout(template_row)
    template_fields = QFormLayout()
    template_fields.addRow("最大位移幅值", self.loading_amplitude_spin)
    template_fields.addRow("每级循环数", self.loading_cycles_spin)
    controls_layout.addLayout(template_fields)
    loading_form = QFormLayout()
    loading_form.setVerticalSpacing(9)
    self.amplitudes_edit = QPlainTextEdit()
    self.amplitudes_edit.setMinimumHeight(110)
    self.amplitudes_edit.setMaximumHeight(140)
    self.du_spin = QDoubleSpinBox()
    self.du_spin.setDecimals(6)
    self.du_spin.setRange(1.0e-9, 1.0e9)
    self.du_spin.setValue(0.25)
    loading_form.addRow("位移幅值", self.amplitudes_edit)
    loading_form.addRow("位移增量 du", self.du_spin)
    edit_table = QPushButton("表格编辑转折点…"); edit_table.clicked.connect(self.edit_loading_table)
    loading_form.addRow("", edit_table)
    controls_layout.addLayout(loading_form)
    controls_layout.addWidget(self._section_title("外部加载历史"))
    history_form = QFormLayout()
    history_form.setVerticalSpacing(9)
    self.history_path_edit = QLineEdit()
    self.history_column_combo = QComboBox()
    self.append_zero_check = QCheckBox("在历史末尾补零")
    history_form.addRow("文件", self._path_control(self.history_path_edit, self.browse_loading_history, True))
    history_form.addRow("位移列", self.history_column_combo)
    self.history_scale_spin = CompactDoubleSpinBox(); self.history_scale_spin.setDecimals(10)
    self.history_scale_spin.setRange(-1e12, 1e12); self.history_scale_spin.setValue(1)
    history_form.addRow("换算系数（可负）", self.history_scale_spin)
    history_form.addRow("", self.append_zero_check)
    controls_layout.addLayout(history_form)
    controls_layout.addWidget(self._section_title("试验数据（可选）"))
    experiment_form = QFormLayout()
    experiment_form.setVerticalSpacing(9)
    self.experiment_path_edit = QLineEdit()
    self.displacement_combo = QComboBox()
    self.force_combo = QComboBox()
    experiment_form.addRow("数据文件", self._path_control(self.experiment_path_edit, self.browse_experiment_data, True))
    experiment_form.addRow("位移列", self.displacement_combo)
    experiment_form.addRow("力列", self.force_combo)
    self.experiment_displacement_scale_spin = CompactDoubleSpinBox()
    self.experiment_force_scale_spin = CompactDoubleSpinBox()
    for spin in (self.experiment_displacement_scale_spin, self.experiment_force_scale_spin):
        spin.setDecimals(10); spin.setRange(-1e12, 1e12); spin.setValue(1)
        spin.setToolTip("原始数据乘此系数转到项目单位；负数反转方向")
    experiment_form.addRow("位移换算系数", self.experiment_displacement_scale_spin)
    experiment_form.addRow("力换算系数", self.experiment_force_scale_spin)
    controls_layout.addLayout(experiment_form)
    controls_layout.addStretch(1)

    preview_panel = QWidget()
    preview_layout = QVBoxLayout(preview_panel)
    preview_layout.setContentsMargins(18, 0, 0, 0)
    preview_layout.addWidget(self._section_title("加载与试验预览"))
    self.loading_preview_tabs = QTabWidget()
    self.loading_history_plot = StagePlotWidget()
    self.experiment_preview_plot = StagePlotWidget()
    self.loading_preview_tabs.addTab(self.loading_history_plot, "加载历程")
    self.loading_preview_tabs.addTab(self.experiment_preview_plot, "试验滞回")
    preview_layout.addWidget(self.loading_preview_tabs, 1)
    self.loading_preview_note = QLabel("修改幅值或位移增量后，加载历程会自动更新")
    self.loading_preview_note.setObjectName("previewNote")
    preview_layout.addWidget(self.loading_preview_note)

    self.amplitudes_edit.textChanged.connect(self._refresh_loading_preview)
    self.du_spin.valueChanged.connect(self._refresh_loading_preview)
    self.history_path_edit.textChanged.connect(self._refresh_loading_preview)
    self.history_column_combo.currentIndexChanged.connect(self._refresh_loading_preview)
    self.append_zero_check.toggled.connect(self._refresh_loading_preview)
    self.experiment_path_edit.textChanged.connect(self._refresh_experiment_preview)
    self.displacement_combo.currentIndexChanged.connect(self._refresh_experiment_preview)
    self.force_combo.currentIndexChanged.connect(self._refresh_experiment_preview)
    self.amplitudes_edit.textChanged.connect(
        lambda: self._mark_comparison_dirty("加载制度已修改")
    )
    self.du_spin.valueChanged.connect(
        lambda value: self._mark_comparison_dirty("加载制度已修改")
    )
    self.history_path_edit.textChanged.connect(
        lambda text: self._mark_comparison_dirty("加载制度已修改")
    )

    scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(controls)
    splitter.addWidget(scroll)
    splitter.addWidget(preview_panel)
    splitter.setSizes([540, 660])
    return splitter


class LoadingPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build(window))
