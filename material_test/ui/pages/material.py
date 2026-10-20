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
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(7)
    template_header = QHBoxLayout()
    template_header.setSpacing(8)
    template_header.addWidget(self._section_title("材料资源"))
    self.template_path_edit = QLineEdit()
    self.template_path_edit.setReadOnly(True)
    self.template_path_edit.setPlaceholderText("项目内材料，随项目保存")
    template_header.addWidget(self.template_path_edit, 1)
    # Use a split button so the common action is available from the whole
    # button body while the menu arrow is reserved for importing a YAML
    # template.  A QPushButton with setMenu() makes the entire button open
    # the menu, which makes adding a material unnecessarily indirect.
    self.material_library_button = QToolButton()
    self.material_library_button.setText("添加材料")
    self.material_library_button.setObjectName("primaryButton")
    self.material_library_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    self.material_library_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    self.material_library_button.clicked.connect(self.open_material_library)
    menu = QMenu(self.material_library_button)
    menu.addAction("导入 YAML 模板…", self.browse_material_template)
    self.material_library_button.setMenu(menu)
    template_header.addWidget(self.material_library_button)
    layout.addLayout(template_header)
    identity = QHBoxLayout()
    self.material_name_edit = QLineEdit()
    self.material_tag_spin = MaterialTagSpinBox()
    self.material_tag_spin.setFixedWidth(150)
    self.material_tag_spin.setToolTip("使用上下箭头切换已添加的材料 Tag")
    self.material_tag_spin.valueChanged.connect(self._select_material_tag)
    identity.addWidget(QLabel("材料名称"))
    identity.addWidget(self.material_name_edit, 3)
    self.material_tag_spin.hide()
    layout.addLayout(identity)

    command_panel = QWidget()
    command_layout = QVBoxLayout(command_panel)
    command_layout.setContentsMargins(0, 0, 0, 0)
    command_layout.setSpacing(7)
    command_layout.addWidget(self._section_title("已定义的 OpenSees 材料命令"))
    self.material_command_table = QTableWidget(0, 5)
    self.material_command_table.setHorizontalHeaderLabels(
        ["Tag", "材料类型", "OpenSees 命令", "装配使用", "操作"]
    )
    self.material_command_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    self.material_command_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    self.material_command_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
    self.material_command_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    self.material_command_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
    self.material_command_table.verticalHeader().setVisible(False)
    self.material_command_table.setAlternatingRowColors(True)
    self.material_command_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    self.material_command_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    self.material_command_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    self.material_command_table.setMinimumHeight(80)
    self.material_command_table.currentCellChanged.connect(
        lambda row, column, previous_row, previous_column: self._select_material_command(row)
    )
    command_layout.addWidget(self.material_command_table, 1)

    detail_splitter = QSplitter(Qt.Orientation.Horizontal)
    detail_splitter.setObjectName("materialDetailSplitter")
    editor_panel = QWidget()
    editor_layout = QVBoxLayout(editor_panel)
    editor_layout.setContentsMargins(0, 0, 14, 0)
    editor_layout.setSpacing(8)
    self.selected_material_label = self._section_title("材料参数说明")
    editor_layout.addWidget(self.selected_material_label)
    self.selected_material_usage_label = QLabel("尚未分配到装配单元")
    self.selected_material_usage_label.setObjectName("previewNote")
    editor_layout.addWidget(self.selected_material_usage_label)
    editor_layout.addWidget(QLabel("选中材料命令"))
    self.material_command_edit = QPlainTextEdit()
    self.material_command_edit.setReadOnly(True)
    self.material_command_edit.setMinimumHeight(58)
    self.material_command_edit.setMaximumHeight(76)
    self.material_command_edit.setPlaceholderText("uniaxialMaterial ...")
    editor_layout.addWidget(self.material_command_edit)
    self.param_table = QTableWidget(0, 5)
    self.param_table.setHorizontalHeaderLabels(["参数", "中文含义", "默认/当前值", "单位", "对曲线的作用"])
    self.param_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    self.param_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    self.param_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    self.param_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    self.param_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
    self.param_table.verticalHeader().setVisible(False)
    self.param_table.setAlternatingRowColors(True)
    self.param_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    editor_layout.addWidget(self.param_table, 1)

    preview_panel = QWidget()
    preview_layout = QVBoxLayout(preview_panel)
    preview_layout.setContentsMargins(14, 0, 0, 0)
    preview_layout.setSpacing(8)
    preview_header = QHBoxLayout()
    preview_header.addWidget(self._section_title("单个材料滞回曲线"))
    preview_header.addStretch(1)
    self.preview_protocol_combo = QComboBox()
    self.preview_protocol_combo.addItem("材料标准循环", "standard")
    self.preview_protocol_combo.addItem("当前加载协议", "current")
    self.calculate_preview_button = QPushButton("计算曲线")
    self.calculate_preview_button.setObjectName("primaryButton")
    self.calculate_preview_button.clicked.connect(self.calculate_selected_material_preview)
    self.preview_protocol_combo.setCurrentIndex(self.preview_protocol_combo.findData("current"))
    self.preview_protocol_combo.hide()
    source_label = QLabel(); source_label.setWordWrap(True)
    self._source_badges.append(source_label); preview_layout.addWidget(source_label)
    preview_header.addWidget(self.calculate_preview_button)
    preview_layout.addLayout(preview_header)
    self.material_preview_plot = HysteresisPreviewWidget()
    self.material_preview_plot.setMinimumHeight(250)
    preview_layout.addWidget(self.material_preview_plot, 1)
    self.material_preview_note = QLabel()
    self.material_preview_note.setWordWrap(True)
    self.material_preview_note.setObjectName("previewNote")
    preview_layout.addWidget(self.material_preview_note)

    detail_splitter.addWidget(editor_panel)
    detail_splitter.addWidget(preview_panel)
    detail_splitter.setSizes([650, 520])
    detail_splitter.handle(1).setToolTip("左右拖动，调整参数说明与滞回曲线的宽度")

    self.material_page_splitter = QSplitter(Qt.Orientation.Vertical)
    self.material_page_splitter.setObjectName("materialPageSplitter")
    self.material_page_splitter.addWidget(command_panel)
    self.material_page_splitter.addWidget(detail_splitter)
    self.material_page_splitter.setStretchFactor(0, 0)
    self.material_page_splitter.setStretchFactor(1, 1)
    self.material_page_splitter.setSizes([180, 580])
    self.material_page_splitter.handle(1).setToolTip("上下拖动，调整材料命令区与参数/曲线区的高度")
    layout.addWidget(self.material_page_splitter, 1)
    return page


class MaterialPage(QWidget):
    def __init__(self, window):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build(window))
