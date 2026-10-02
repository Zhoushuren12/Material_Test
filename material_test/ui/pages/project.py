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
    layout = QVBoxLayout(controls)
    layout.setContentsMargins(0, 0, 18, 0)
    layout.setSpacing(12)
    layout.addWidget(self._section_title("OpenSees 求解器"))
    form = QFormLayout()
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
    self.solver_path_edit = QLineEdit()
    self.tcl_library_edit = QLineEdit()
    self.timeout_spin = QDoubleSpinBox()
    self.timeout_spin.setRange(1.0, 3600.0)
    self.timeout_spin.setValue(120.0)
    self.timeout_spin.setSuffix(" s")
    self.timeout_spin.setDecimals(1)
    form.addRow("OpenSees", self._path_control(self.solver_path_edit, self.browse_solver, True))
    form.addRow("Tcl 库", self._path_control(self.tcl_library_edit, self.browse_tcl_library, False))
    form.addRow("超时时间", self.timeout_spin)
    self.output_dir_edit = QLineEdit(str(ROOT / 'outputs' / 'runs'))
    form.addRow('结果保存目录', self._path_control(self.output_dir_edit, self.browse_output_dir, False))
    layout.addLayout(form)
    layout.addSpacing(18)
    layout.addWidget(self._section_title("单位与本构输入"))
    units = QFormLayout()
    self.force_unit_combo = QComboBox(); self.force_unit_combo.addItems(["N", "kN", "MN"])
    self.length_unit_combo = QComboBox(); self.length_unit_combo.addItems(["mm", "cm", "m"])
    self.material_mode_combo = QComboBox()
    self.material_mode_combo.addItem("应力–应变（按面积、长度换算）", "stress_strain")
    self.material_mode_combo.addItem("力–变形直接输入（A=L=1）", "force_deformation")
    self.material_mode_combo.setCurrentIndex(1)
    units.addRow("力单位", self.force_unit_combo); units.addRow("长度单位", self.length_unit_combo)
    units.addRow("输入模式", self.material_mode_combo); layout.addLayout(units)
    note = QLabel("单位选择定义已有数值的含义，不自动换算材料参数。\n直接输入模式适用于 Excel 已计算好的支撑力、刚度和变形参数。")
    note.setWordWrap(True); layout.addWidget(note)
    layout.addWidget(self._section_title("试件几何"))
    geometry = QFormLayout()
    geometry.setHorizontalSpacing(18)
    geometry.setVerticalSpacing(12)
    self.area_spin = QDoubleSpinBox()
    self.area_spin.setDecimals(6)
    self.area_spin.setRange(1.0e-12, 1.0e12)
    self.area_spin.setValue(1.0)
    self.length_spin = QDoubleSpinBox()
    self.length_spin.setDecimals(6)
    self.length_spin.setRange(1.0e-12, 1.0e12)
    self.length_spin.setValue(1.0)
    self.area_spin.setEnabled(False)
    self.length_spin.setEnabled(False)
    geometry.addRow("默认面积", self.area_spin)
    geometry.addRow("总长度", self.length_spin)
    layout.addLayout(geometry)
    layout.addStretch(1)

    preview_panel = QWidget()
    preview_layout = QVBoxLayout(preview_panel)
    preview_layout.setContentsMargins(18, 0, 0, 0)
    preview_layout.addWidget(self._section_title("当前项目示意"))
    self.project_overview = SpecimenOverviewWidget()
    preview_layout.addWidget(self.project_overview, 1)
    self.solver_path_edit.textChanged.connect(self._refresh_project_overview)
    self.area_spin.valueChanged.connect(self._refresh_project_overview)
    self.length_spin.valueChanged.connect(self._refresh_project_overview)
    self.area_spin.valueChanged.connect(
        lambda value: self._mark_comparison_dirty("试件面积已修改")
    )
    self.length_spin.valueChanged.connect(
        lambda value: self._mark_comparison_dirty("试件长度已修改")
    )
    self._refresh_project_overview()

    splitter.addWidget(controls)
    splitter.addWidget(preview_panel)
    splitter.setSizes([520, 680])
    return splitter


class ProjectPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build(window))
