from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRegularExpression, Qt, QThread
from PySide6.QtGui import QAction, QFont
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QDialog, QDialogButtonBox,
    QInputDialog, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QStyle, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from material_test.domain import (
    ExperimentData, FitConfig, FitParameterSpec, LoadingProtocol, MaterialTemplate, ModelingConfig,
    ParameterSpec, ProjectConfig, SimulationResult, SolverConfig, TopologyNode,
)
from material_test.comparison import calculate_curve_metrics, calculate_error_metrics
from material_test.fitting import FitResult
from material_test.io.experiment_data import list_data_columns, load_experiment_data
from material_test.io.material_library import load_material_library
from material_test.io.material_templates import load_material_template
from material_test.io.project_config import load_project_config, save_project_config
from material_test.material_commands import ParsedMaterialCommand, parse_material_commands
from material_test.material_preview import (
    calculate_material_preview, preview_error_summary, standard_preview_history,
)
from material_test.modeling import defined_material_tags
from material_test.plotting import save_comparison_plot
from material_test.protocols import history_from_protocol
from material_test.ui.material_library_dialog import MaterialLibraryDialog
from material_test.ui.plot_canvas import (
    HysteresisPreviewWidget, OverlayPlotWidget, SpecimenOverviewWidget, StagePlotWidget,
)
from material_test.ui.topology_editor import TopologyEditor
from material_test.ui.workers import FitWorker, RunWorker
from material_test.workflow import WorkflowResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / "examples" / "materials" / "twostage.yaml"
DEFAULT_PROJECT = ROOT / "examples" / "projects" / "twostage_demo.yaml"
PAGES = [
    ("项目设置", "求解器与工程文件"),
    ("材料库与本构", "可复用材料、参数与单材响应"),
    ("支撑装配", "串并联拓扑、材料赋值与整体响应"),
    ("加载与数据", "加载历程与试验曲线"),
    ("计算与对比", "手动调参、基准曲线与试验对比"),
    ("参数拟合", "参数边界、算法与拟合对比"),
]


class MaterialTagSpinBox(QSpinBox):
    """A spin box whose arrows move through the loaded material tags."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[int] = []
        self.lineEdit().setReadOnly(True)

    def set_material_tags(self, tags: list[int], selected: int | None = None) -> None:
        self._tags = sorted(set(int(tag) for tag in tags))
        self.blockSignals(True)
        if self._tags:
            self.setRange(self._tags[0], self._tags[-1])
            value = selected if selected in self._tags else self._tags[0]
            self.setValue(value)
        else:
            self.setRange(1, 1)
            self.setValue(1)
        self.blockSignals(False)

    def stepBy(self, steps: int) -> None:
        if not self._tags:
            return
        try:
            index = self._tags.index(int(self.value()))
        except ValueError:
            index = 0
        index = max(0, min(len(self._tags) - 1, index + (1 if steps > 0 else -1)))
        self.setValue(self._tags[index])

    def stepEnabled(self) -> QAbstractSpinBox.StepEnabledFlag:
        if not self._tags:
            return QAbstractSpinBox.StepEnabledFlag.StepNone
        flags = QAbstractSpinBox.StepEnabledFlag.StepNone
        try:
            index = self._tags.index(int(self.value()))
        except ValueError:
            return QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        if index > 0:
            flags |= QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        if index < len(self._tags) - 1:
            flags |= QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        return flags


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Compact numeric editor that accepts decimal and scientific notation."""

    _number_expression = QRegularExpression(
        r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d*)?"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scientific_validator = QRegularExpressionValidator(
            self._number_expression,
            self,
        )

    def textFromValue(self, value: float) -> str:
        return f"{value:.12g}"

    def valueFromText(self, text: str) -> float:
        return float(text.strip())

    def validate(self, text: str, position: int):
        return self._scientific_validator.validate(text.strip(), position)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Material Test · 材料测试与参数拟合")
        self.resize(1460, 900)
        self.setMinimumSize(1100, 700)
        self.current_template_path: Path | None = None
        self.current_project_path: Path | None = None
        self.current_result: WorkflowResult | FitResult | None = None
        self.baseline_result: WorkflowResult | FitResult | None = None
        self._comparison_dirty = False
        self._material_template_tag = 1
        self._material_template_description = ""
        self._material_lines: list[str] = []
        self._material_parameters: dict[str, ParameterSpec] = {}
        self._material_parameter_order: list[str] = []
        self._material_commands: list[ParsedMaterialCommand] = []
        self._selected_material_row = -1
        self._updating_material_editor = False
        self._thread: QThread | None = None
        self._worker: RunWorker | FitWorker | None = None
        self._build_actions()
        self._build_ui()
        self._apply_style()
        self._load_initial_state()

    def _build_actions(self) -> None:
        self.load_project_action = QAction("打开项目", self)
        self.load_project_action.setShortcut("Ctrl+O")
        self.load_project_action.triggered.connect(self.load_project)
        self.save_project_action = QAction("保存项目", self)
        self.save_project_action.setShortcut("Ctrl+S")
        self.save_project_action.triggered.connect(self.save_project)
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addActions([self.load_project_action, self.save_project_action])
        self.publish_brace_action = QAction('发布支撑模型到 Structural analysis…', self)
        self.publish_brace_action.setShortcut('Ctrl+Shift+P')
        self.publish_brace_action.triggered.connect(self.publish_brace)
        file_menu.addAction(self.publish_brace_action)

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_navigation())
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(26, 12, 26, 12)
        content_layout.setSpacing(8)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("pageSubtitle")
        content_layout.addWidget(self.page_title)
        content_layout.addWidget(self.page_subtitle)
        self.pages = QStackedWidget()
        for builder in (
            self._build_project_page, self._build_material_page, self._build_model_page,
            self._build_loading_page, self._build_comparison_page, self._build_fitting_page,
        ):
            self.pages.addWidget(builder())
        content_layout.addWidget(self.pages, 1)
        content_layout.addWidget(self._build_page_footer())
        body.addWidget(content, 1)
        body_widget = QWidget()
        body_widget.setLayout(body)
        outer.addWidget(body_widget, 1)
        self.setCentralWidget(central)
        self.nav_list.setCurrentRow(0)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("appHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 12, 22, 12)
        mark = QLabel("MT")
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Material Test")
        title.setObjectName("brandTitle")
        self.project_label = QLabel("未保存项目")
        self.project_label.setObjectName("projectLabel")
        layout.addWidget(mark)
        layout.addWidget(title)
        layout.addSpacing(12)
        layout.addWidget(self.project_label)
        layout.addStretch(1)
        open_button = QPushButton("打开")
        open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        open_button.clicked.connect(self.load_project)
        save_button = QPushButton("保存")
        save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        save_button.clicked.connect(self.save_project)
        layout.addWidget(open_button)
        layout.addWidget(save_button)
        return header

    def _build_navigation(self) -> QWidget:
        nav = QFrame()
        nav.setObjectName("navigation")
        nav.setFixedWidth(230)
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(14, 22, 14, 18)
        label = QLabel("分析流程")
        label.setObjectName("navCaption")
        layout.addWidget(label)
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setFrameShape(QFrame.Shape.NoFrame)
        for index, (title, subtitle) in enumerate(PAGES, start=1):
            item = QListWidgetItem(f"{index:02d}   {title}\n       {subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, index - 1)
            self.nav_list.addItem(item)
        self.nav_list.currentRowChanged.connect(self._navigate_to)
        layout.addWidget(self.nav_list, 1)
        self.model_status = QLabel("模型待校验")
        self.model_status.setObjectName("statusPill")
        self.model_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.model_status)
        return nav

    def _build_page_footer(self) -> QWidget:
        footer = QWidget()
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 4, 0, 0)
        self.page_counter = QLabel()
        self.page_counter.setObjectName("pageCounter")
        self.previous_button = QPushButton("上一步")
        self.previous_button.clicked.connect(lambda: self._navigate_relative(-1))
        self.next_button = QPushButton("下一步")
        self.next_button.setObjectName("primaryButton")
        self.next_button.clicked.connect(lambda: self._navigate_relative(1))
        layout.addWidget(self.page_counter)
        layout.addStretch(1)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.next_button)
        return footer

    def _build_project_page(self) -> QWidget:
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

    def _build_material_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        template_header = QHBoxLayout()
        template_header.setSpacing(8)
        template_header.addWidget(self._section_title("材料资源"))
        self.template_path_edit = QLineEdit()
        self.template_path_edit.setReadOnly(True)
        template_header.addWidget(
            self._path_control(self.template_path_edit, self.browse_material_template, True), 1
        )
        library_button = QPushButton("打开材料库")
        library_button.setObjectName("primaryButton")
        library_button.clicked.connect(self.open_material_library)
        template_header.addWidget(library_button)
        layout.addLayout(template_header)
        identity = QHBoxLayout()
        self.material_name_edit = QLineEdit()
        self.material_tag_spin = MaterialTagSpinBox()
        self.material_tag_spin.setFixedWidth(150)
        self.material_tag_spin.setToolTip("使用上下箭头切换已添加的材料 Tag")
        self.material_tag_spin.valueChanged.connect(self._select_material_tag)
        identity.addWidget(QLabel("材料名称"))
        identity.addWidget(self.material_name_edit, 3)
        identity.addWidget(QLabel("选择材料 Tag"))
        identity.addWidget(self.material_tag_spin)
        layout.addLayout(identity)

        layout.addWidget(self._section_title("已定义的 OpenSees 材料命令"))
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
        self.material_command_table.setMinimumHeight(104)
        self.material_command_table.setMaximumHeight(148)
        self.material_command_table.currentCellChanged.connect(
            lambda row, column, previous_row, previous_column: self._select_material_command(row)
        )
        layout.addWidget(self.material_command_table)

        detail_splitter = QSplitter(Qt.Orientation.Horizontal)
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
        preview_header.addWidget(self.preview_protocol_combo)
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
        layout.addWidget(detail_splitter, 1)
        return page

    def _build_model_page(self) -> QWidget:
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
        response_header.addWidget(self.model_preview_protocol_combo)
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

    def _build_loading_page(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 18, 0)
        controls_layout.setSpacing(10)
        controls_layout.addWidget(self._section_title("循环加载制度"))
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
        controls_layout.addLayout(loading_form)
        controls_layout.addWidget(self._section_title("外部加载历史"))
        history_form = QFormLayout()
        history_form.setVerticalSpacing(9)
        self.history_path_edit = QLineEdit()
        self.history_column_combo = QComboBox()
        self.append_zero_check = QCheckBox("在历史末尾补零")
        history_form.addRow("文件", self._path_control(self.history_path_edit, self.browse_loading_history, True))
        history_form.addRow("位移列", self.history_column_combo)
        history_form.addRow("", self.append_zero_check)
        controls_layout.addLayout(history_form)
        controls_layout.addWidget(self._section_title("试验数据"))
        experiment_form = QFormLayout()
        experiment_form.setVerticalSpacing(9)
        self.experiment_path_edit = QLineEdit()
        self.displacement_combo = QComboBox()
        self.force_combo = QComboBox()
        experiment_form.addRow("数据文件", self._path_control(self.experiment_path_edit, self.browse_experiment_data, True))
        experiment_form.addRow("位移列", self.displacement_combo)
        experiment_form.addRow("力列", self.force_combo)
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

        splitter.addWidget(controls)
        splitter.addWidget(preview_panel)
        splitter.setSizes([490, 710])
        return splitter

    def _build_fitting_page(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        settings = QWidget()
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(0, 0, 18, 0)
        layout.setSpacing(10)
        self.fit_algorithm_combo = QComboBox()
        self.fit_algorithm_combo.addItem("差分进化 DE", "de")
        self.fit_algorithm_combo.addItem("粒子群 PSO", "pso")
        self.fit_max_evaluations_spin = QSpinBox()
        self.fit_max_evaluations_spin.setRange(8, 100000)
        self.fit_max_evaluations_spin.setValue(240)
        self.fit_population_spin = QSpinBox()
        self.fit_population_spin.setRange(2, 1000)
        self.fit_population_spin.setValue(16)
        self.fit_local_refine_check = QCheckBox("局部最小二乘精修")
        self.fit_local_refine_check.setChecked(True)
        self.fit_local_evaluations_spin = QSpinBox()
        self.fit_local_evaluations_spin.setRange(1, 100000)
        self.fit_local_evaluations_spin.setValue(120)
        algorithm_form = QFormLayout()
        algorithm_form.setVerticalSpacing(9)
        algorithm_form.addRow("全局算法", self.fit_algorithm_combo)
        algorithm_form.addRow("全局评估次数", self.fit_max_evaluations_spin)
        algorithm_form.addRow("种群规模", self.fit_population_spin)
        algorithm_form.addRow("局部评估次数", self.fit_local_evaluations_spin)
        algorithm_form.addRow("", self.fit_local_refine_check)
        layout.addLayout(algorithm_form)
        layout.addWidget(self._section_title("待拟合参数"))
        self.fit_table = QTableWidget(0, 5)
        self.fit_table.setHorizontalHeaderLabels(["拟合", "参数", "下限", "上限", "尺度"])
        self.fit_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.fit_table.verticalHeader().setVisible(False)
        self.fit_table.setAlternatingRowColors(True)
        layout.addWidget(self.fit_table, 1)
        self.fit_page_button = QPushButton("开始参数拟合")
        self.fit_page_button.setObjectName("primaryButton")
        self.fit_page_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.fit_page_button.clicked.connect(self.start_fit)
        layout.addWidget(self.fit_page_button)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(18, 0, 0, 0)
        preview_layout.setSpacing(8)
        preview_layout.addWidget(self._section_title("拟合曲线对比"))
        self.fitting_preview_plot = StagePlotWidget()
        self.fitting_preview_plot.clear("导入试验数据后可查看曲线；拟合完成后显示最终对比")
        self.fitting_preview_plot.setMinimumHeight(340)
        preview_layout.addWidget(self.fitting_preview_plot, 1)
        self.fitting_preview_status = QLabel("尚未运行拟合")
        self.fitting_preview_status.setObjectName("previewNote")
        self.fitting_preview_status.setWordWrap(True)
        preview_layout.addWidget(self.fitting_preview_status)

        splitter.addWidget(settings)
        splitter.addWidget(preview_panel)
        splitter.setSizes([520, 680])
        return splitter

    def _build_comparison_page(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 14, 0)
        controls_layout.setSpacing(9)
        controls_layout.addWidget(self._section_title("手动调参"))
        self.comparison_material_combo = QComboBox()
        self.comparison_material_combo.currentIndexChanged.connect(
            self._populate_comparison_parameters
        )
        controls_layout.addWidget(self.comparison_material_combo)
        self.comparison_parameter_table = QTableWidget(0, 3)
        self.comparison_parameter_table.setHorizontalHeaderLabels(
            ["参数", "含义与单位", "当前值"]
        )
        self.comparison_parameter_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.comparison_parameter_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.comparison_parameter_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.comparison_parameter_table.verticalHeader().setVisible(False)
        self.comparison_parameter_table.setAlternatingRowColors(True)
        controls_layout.addWidget(self.comparison_parameter_table, 1)

        comparison_protocol_form = QFormLayout()
        self.comparison_protocol_combo = QComboBox()
        self.comparison_protocol_combo.addItem("当前加载制度", "current")
        self.comparison_protocol_combo.addItem("试验位移历程", "experiment")
        self.comparison_protocol_combo.currentIndexChanged.connect(
            lambda index: self._mark_comparison_dirty("计算加载来源已修改")
        )
        comparison_protocol_form.addRow("计算加载", self.comparison_protocol_combo)
        controls_layout.addLayout(comparison_protocol_form)
        self.run_button = QPushButton("计算当前模型")
        self.run_button.setObjectName("primaryButton")
        self.run_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_button.clicked.connect(self.start_run)
        controls_layout.addWidget(self.run_button)
        baseline_actions = QHBoxLayout()
        self.set_baseline_button = QPushButton("设为基准")
        self.set_baseline_button.setEnabled(False)
        self.set_baseline_button.clicked.connect(self.set_current_as_baseline)
        self.clear_baseline_button = QToolButton()
        self.clear_baseline_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self.clear_baseline_button.setToolTip("清除基准曲线")
        self.clear_baseline_button.setEnabled(False)
        self.clear_baseline_button.clicked.connect(self.clear_baseline)
        baseline_actions.addWidget(self.set_baseline_button, 1)
        baseline_actions.addWidget(self.clear_baseline_button)
        controls_layout.addLayout(baseline_actions)
        self.export_button = QPushButton("导出当前对比图")
        self.export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_button.clicked.connect(self.export_figure)
        self.export_button.setEnabled(False)
        controls_layout.addWidget(self.export_button)
        self.publish_button = QPushButton('发布支撑模型…')
        self.publish_button.setToolTip('保存两个轴向端口、显式内部装配及实验/计算来源，供 Structural analysis 导入')
        self.publish_button.clicked.connect(self.publish_brace)
        self.publish_button.setEnabled(False)
        controls_layout.addWidget(self.publish_button)
        self.run_state_label = QLabel("就绪")
        self.run_state_label.setObjectName("runState")
        self.run_state_label.setWordWrap(True)
        controls_layout.addWidget(self.run_state_label)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(14, 0, 0, 0)
        result_layout.setSpacing(7)
        result_layout.addWidget(self._section_title("曲线叠加"))
        self.plot_widget = OverlayPlotWidget()
        result_layout.addWidget(self.plot_widget, 1)
        detail_tabs = QTabWidget()
        metrics_panel = QWidget()
        metrics_layout = QVBoxLayout(metrics_panel)
        metrics_layout.setContentsMargins(8, 8, 8, 8)
        self.comparison_metrics_table = QTableWidget(0, 7)
        self.comparison_metrics_table.setHorizontalHeaderLabels(
            ["曲线", "峰值力", "初始刚度", "滞回积分", "末点力", "RMSE", "NRMSE"]
        )
        self.comparison_metrics_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.comparison_metrics_table.verticalHeader().setVisible(False)
        self.comparison_metrics_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.comparison_metrics_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        metrics_layout.addWidget(self.comparison_metrics_table)
        detail_tabs.addTab(metrics_panel, "对比指标")
        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_header = QHBoxLayout()
        log_header.addWidget(self._section_title("运行日志"))
        log_header.addStretch(1)
        clear_button = QToolButton()
        clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        clear_button.setToolTip("清空日志")
        clear_button.clicked.connect(self.clear_log)
        log_header.addWidget(clear_button)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(2000)
        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log_edit)
        detail_tabs.addTab(log_panel, "运行日志")
        detail_tabs.setMaximumHeight(190)
        result_layout.addWidget(detail_tabs)

        splitter.addWidget(controls)
        splitter.addWidget(result_panel)
        splitter.setSizes([390, 810])
        outer.addWidget(splitter)
        return page

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _scroll_page() -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 14, 0)
        layout.setSpacing(12)
        scroll.setWidget(panel)
        return scroll, layout

    def _path_control(self, edit: QLineEdit, callback, file_mode: bool) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button = QToolButton()
        button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        button.setToolTip("选择文件" if file_mode else "选择文件夹")
        button.clicked.connect(callback)
        row.addWidget(edit)
        row.addWidget(button)
        return widget

    def _navigate_to(self, index: int) -> None:
        if index < 0 or index >= len(PAGES):
            return
        if index == 5:
            self._sync_fit_table()
        self.pages.setCurrentIndex(index)
        self.page_title.setText(PAGES[index][0])
        self.page_subtitle.setText(PAGES[index][1])
        self.page_counter.setText(f"步骤 {index + 1} / {len(PAGES)}")
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < len(PAGES) - 1)
        if self.nav_list.currentRow() != index:
            self.nav_list.setCurrentRow(index)
        if index == 0:
            self._refresh_project_overview()
        elif index == 3:
            self._refresh_loading_preview()
            self._refresh_experiment_preview()
        elif index == 4:
            self._sync_comparison_materials()
            self._refresh_comparison_view()
        elif index == 5:
            self._refresh_fitting_preview()

    def _navigate_relative(self, offset: int) -> None:
        self._navigate_to(max(0, min(len(PAGES) - 1, self.pages.currentIndex() + offset)))

    def _refresh_project_overview(self, *args) -> None:
        if not hasattr(self, "project_overview"):
            return
        self.project_overview.set_config(
            area=float(self.area_spin.value()),
            length=float(self.length_spin.value()),
            solver_ready=bool(self.solver_path_edit.text().strip()),
        )

    def _refresh_loading_preview(self, *args) -> None:
        if not hasattr(self, "loading_history_plot"):
            return
        try:
            history = history_from_protocol(self._loading_protocol_from_ui())
        except Exception as exc:
            self.loading_history_plot.clear("加载制度尚未完整")
            self.loading_preview_note.setText(str(exc))
            return
        steps = np.arange(history.size, dtype=float)
        self.loading_history_plot.update_series(
            [(steps, history, "位移历程", "#1f5fae")],
            title="加载制度预览",
            x_label="加载步",
            y_label="位移 / 应变",
            zero_axes=True,
        )
        self.loading_preview_note.setText(
            f"共 {history.size} 个加载点 · 最大值 {np.max(history):g} · 最小值 {np.min(history):g}"
        )

    def _refresh_experiment_preview(self, *args) -> None:
        if not hasattr(self, "experiment_preview_plot"):
            return
        path_text = self.experiment_path_edit.text().strip()
        if not path_text or not self.displacement_combo.currentText() or not self.force_combo.currentText():
            self.experiment_preview_plot.clear("导入试验数据并选择位移列、力列")
            return
        try:
            experiment = load_experiment_data(
                Path(path_text),
                displacement_column=self.displacement_combo.currentText(),
                force_column=self.force_combo.currentText(),
            )
        except Exception as exc:
            self.experiment_preview_plot.clear(f"试验数据暂不可用\n{exc}")
            return
        self.experiment_preview_plot.update_series(
            [(experiment.displacement, experiment.force, "试验曲线", "#c2413b")],
            title="试验滞回曲线",
            x_label="位移",
            y_label="力",
            zero_axes=True,
        )

    def _sync_comparison_materials(self, selected_tag: int | None = None) -> None:
        if not hasattr(self, "comparison_material_combo"):
            return
        tags = [command.tag for command in self._material_commands]
        current_tag = self.comparison_material_combo.currentData()
        if selected_tag is None and current_tag in tags:
            selected_tag = int(current_tag)
        if selected_tag is None and 0 <= self._selected_material_row < len(self._material_commands):
            selected_tag = self._material_commands[self._selected_material_row].tag

        self.comparison_material_combo.blockSignals(True)
        self.comparison_material_combo.clear()
        for command in self._material_commands:
            self.comparison_material_combo.addItem(
                f"Tag {command.tag} · {command.material_type}", command.tag
            )
        if selected_tag is not None:
            index = self.comparison_material_combo.findData(selected_tag)
            if index >= 0:
                self.comparison_material_combo.setCurrentIndex(index)
        self.comparison_material_combo.blockSignals(False)
        self._populate_comparison_parameters()

    def _populate_comparison_parameters(self, *args) -> None:
        if not hasattr(self, "comparison_parameter_table"):
            return
        self.comparison_parameter_table.setRowCount(0)
        tag = self.comparison_material_combo.currentData()
        command = next(
            (item for item in self._material_commands if item.tag == tag),
            None,
        )
        if command is None:
            return
        for name in command.parameter_names:
            parameter = self._material_parameters.get(name)
            if parameter is None:
                continue
            row = self.comparison_parameter_table.rowCount()
            self.comparison_parameter_table.insertRow(row)
            meaning = parameter.description
            if parameter.unit:
                meaning = f"{meaning} [{parameter.unit}]" if meaning else parameter.unit
            for column, value in (
                (0, parameter.name),
                (1, meaning),
            ):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(parameter.effect or value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, parameter.name)
                self.comparison_parameter_table.setItem(row, column, item)
            editor = CompactDoubleSpinBox()
            editor.setDecimals(12)
            editor.setRange(
                parameter.min if parameter.min is not None else -1.0e15,
                parameter.max if parameter.max is not None else 1.0e15,
            )
            editor.setValue(parameter.value)
            editor.setMinimumWidth(118)
            editor.valueChanged.connect(
                lambda value, parameter_name=name: self._set_material_parameter_value(
                    parameter_name, value
                )
            )
            self.comparison_parameter_table.setCellWidget(row, 2, editor)
            self.comparison_parameter_table.setRowHeight(row, 38)

    def _refresh_comparison_view(self) -> None:
        if not hasattr(self, "plot_widget"):
            return
        current = self.current_result
        if not isinstance(current, (WorkflowResult, FitResult)):
            self.plot_widget.clear()
            self.comparison_metrics_table.setRowCount(0)
            self.set_baseline_button.setEnabled(False)
            self.clear_baseline_button.setEnabled(self.baseline_result is not None)
            self.export_button.setEnabled(False)
            self.run_state_label.setText("等待计算当前模型")
            return

        baseline = self.baseline_result
        baseline_simulation = (
            baseline.simulation if isinstance(baseline, (WorkflowResult, FitResult)) else None
        )
        experiment = self._comparison_experiment()
        self.plot_widget.update_comparison(
            current.simulation,
            experiment=experiment,
            baseline=baseline_simulation,
        )
        self._update_comparison_metrics(experiment)
        self.set_baseline_button.setEnabled(True)
        self.clear_baseline_button.setEnabled(baseline_simulation is not None)
        self.export_button.setEnabled(True)
        if self._comparison_dirty:
            self.run_state_label.setText("参数或模型已修改 · 当前曲线待重新计算")
        elif baseline_simulation is not None:
            self.run_state_label.setText("当前、基准与试验曲线已更新")
        else:
            self.run_state_label.setText("当前曲线已更新 · 尚未设置基准")

    def _comparison_experiment(self) -> ExperimentData | None:
        for result in (self.current_result, self.baseline_result):
            if isinstance(result, (WorkflowResult, FitResult)) and result.experiment is not None:
                return result.experiment
        path_text = self.experiment_path_edit.text().strip()
        if not path_text or not self.displacement_combo.currentText() or not self.force_combo.currentText():
            return None
        try:
            return load_experiment_data(
                Path(path_text),
                displacement_column=self.displacement_combo.currentText(),
                force_column=self.force_combo.currentText(),
            )
        except Exception:
            return None

    def _update_comparison_metrics(self, experiment: ExperimentData | None) -> None:
        entries: list[tuple[str, np.ndarray, np.ndarray]] = []
        if experiment is not None:
            entries.append(("试验", experiment.displacement, experiment.force))
        if isinstance(self.baseline_result, (WorkflowResult, FitResult)):
            simulation = self.baseline_result.simulation
            entries.append(("基准", simulation.displacement, simulation.force))
        if isinstance(self.current_result, (WorkflowResult, FitResult)):
            simulation = self.current_result.simulation
            entries.append(("当前", simulation.displacement, simulation.force))

        self.comparison_metrics_table.setRowCount(0)
        for label, displacement, force in entries:
            metrics = calculate_curve_metrics(displacement, force)
            errors = None
            if (
                label != "试验"
                and experiment is not None
                and displacement.shape == experiment.displacement.shape
                and np.allclose(displacement, experiment.displacement, rtol=1.0e-8, atol=1.0e-10)
            ):
                errors = calculate_error_metrics(force, experiment.force)
            values = (
                label,
                self._format_metric(metrics.peak_absolute_force),
                self._format_metric(metrics.initial_stiffness),
                self._format_metric(metrics.hysteretic_integral),
                self._format_metric(metrics.residual_force),
                self._format_metric(errors.rmse if errors else None),
                self._format_metric(errors.normalized_rmse if errors else None),
            )
            row = self.comparison_metrics_table.rowCount()
            self.comparison_metrics_table.insertRow(row)
            for column, value in enumerate(values):
                self.comparison_metrics_table.setItem(row, column, QTableWidgetItem(value))
            self.comparison_metrics_table.setRowHeight(row, 32)

    @staticmethod
    def _format_metric(value: float | None) -> str:
        return "—" if value is None or not np.isfinite(value) else f"{value:.6g}"

    def set_current_as_baseline(self) -> None:
        if not isinstance(self.current_result, (WorkflowResult, FitResult)):
            return
        self.baseline_result = self.current_result
        self._append_log(f"已设置基准曲线：{self.current_result.run_dir}")
        self._refresh_comparison_view()

    def clear_baseline(self) -> None:
        if self.baseline_result is None:
            return
        self.baseline_result = None
        self._append_log("已清除基准曲线")
        self._refresh_comparison_view()

    def _mark_comparison_dirty(self, reason: str) -> None:
        if not isinstance(self.current_result, (WorkflowResult, FitResult)):
            return
        self._comparison_dirty = True
        if hasattr(self, "run_state_label"):
            self.run_state_label.setText(f"{reason} · 当前曲线待重新计算")

    def _reset_comparison_results(self) -> None:
        self.current_result = None
        self.baseline_result = None
        self._comparison_dirty = False
        if hasattr(self, "plot_widget"):
            self._refresh_comparison_view()

    def _refresh_fitting_preview(self) -> None:
        if not hasattr(self, "fitting_preview_plot"):
            return
        result = self.current_result
        if isinstance(result, (WorkflowResult, FitResult)):
            series = [
                (result.simulation.displacement, result.simulation.force, "计算曲线", "#1f5fae")
            ]
            if result.experiment is not None:
                series.append(
                    (result.experiment.displacement, result.experiment.force, "试验曲线", "#c2413b")
                )
            self.fitting_preview_plot.update_series(
                series,
                title="模型与试验对比",
                x_label="位移",
                y_label="力",
                zero_axes=True,
            )
            if isinstance(result, FitResult):
                self.fitting_preview_status.setText(
                    f"拟合完成 · NRMSE {result.metrics.normalized_rmse:.6g} · "
                    f"共 {result.total_evaluations} 次评估"
                )
            else:
                self.fitting_preview_status.setText("当前分析结果，可作为拟合前对比")
            return

        path_text = self.experiment_path_edit.text().strip()
        if path_text and self.displacement_combo.currentText() and self.force_combo.currentText():
            try:
                experiment = load_experiment_data(
                    Path(path_text),
                    displacement_column=self.displacement_combo.currentText(),
                    force_column=self.force_combo.currentText(),
                )
            except Exception:
                experiment = None
            if experiment is not None:
                self.fitting_preview_plot.update_series(
                    [(experiment.displacement, experiment.force, "试验曲线", "#c2413b")],
                    title="拟合目标曲线",
                    x_label="位移",
                    y_label="力",
                    zero_axes=True,
                )
                self.fitting_preview_status.setText("等待计算初始模型或开始拟合")
                return
        self.fitting_preview_plot.clear("导入试验数据后可查看拟合目标")
        self.fitting_preview_status.setText("尚未运行拟合")

    def _apply_style(self) -> None:
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f6f8fb; color: #172033; }
            QMenuBar { background: #ffffff; border-bottom: 1px solid #dde3ec; padding: 2px 8px; }
            QMenuBar::item:selected, QMenu::item:selected { background: #eaf2ff; color: #174ea6; }
            #appHeader { background: #ffffff; border-bottom: 1px solid #dfe5ee; }
            #brandMark { min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px; background: #1f5fae; color: white; border-radius: 5px; font-weight: 700; }
            #brandTitle { font-size: 17px; font-weight: 700; color: #152238; }
            #projectLabel { color: #64748b; }
            #navigation { background: #17243a; border: none; }
            #navCaption { color: #9fb0c9; font-size: 11px; font-weight: 600; padding: 0 8px 6px 8px; }
            #navList { background: transparent; color: #c9d4e4; outline: none; }
            #navList::item { padding: 11px 10px; margin: 2px 0; border-radius: 4px; }
            #navList::item:selected { background: #27486f; color: #ffffff; }
            #navList::item:hover:!selected { background: #20344f; }
            #statusPill { background: #243954; color: #b9c8da; padding: 8px; border-radius: 4px; }
            #pageTitle { font-size: 24px; font-weight: 700; color: #162238; }
            #pageSubtitle { color: #6b778c; font-size: 10px; }
            #sectionTitle { font-size: 13px; font-weight: 700; color: #263750; }
            #pageCounter { color: #778397; }
            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QTreeWidget { background: #ffffff; border: 1px solid #cfd7e3; border-radius: 4px; selection-background-color: #dceaff; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 30px; padding: 0 8px; }
            QPlainTextEdit { padding: 7px; }
            QTableWidget, QTreeWidget { gridline-color: #e5eaf1; alternate-background-color: #f8fafc; }
            QHeaderView::section { background: #eef2f7; color: #46556b; border: none; border-bottom: 1px solid #d7dee9; padding: 8px; font-weight: 600; }
            QTabWidget::pane { border: 1px solid #cfd7e3; background: #ffffff; }
            QTabBar::tab { background: #eef2f7; color: #526174; padding: 8px 14px; border: 1px solid #d7dee9; border-bottom: none; }
            QTabBar::tab:selected { background: #ffffff; color: #174ea6; font-weight: 600; }
            QPushButton { background: #ffffff; border: 1px solid #c8d1de; border-radius: 4px; min-height: 32px; padding: 0 13px; }
            QPushButton:hover { background: #f0f5fb; border-color: #9badc3; }
            QPushButton:disabled { color: #9aa6b6; background: #eef1f5; }
            #primaryButton { background: #1f5fae; color: #ffffff; border-color: #1f5fae; font-weight: 600; }
            #primaryButton:hover { background: #174f96; }
            #dangerButton { color: #b42318; border-color: #e0aaa5; }
            #dangerButton:hover { background: #fff1f0; border-color: #c85d55; }
            QToolButton { background: #ffffff; border: 1px solid #c8d1de; border-radius: 4px; min-width: 30px; min-height: 30px; }
            #dangerTool { color: #b42318; border-color: #e0aaa5; }
            #dangerTool:hover { background: #fff1f0; border-color: #c85d55; }
            QSplitter::handle { background: #dfe5ee; }
            #runState { color: #26734d; font-weight: 600; }
            #previewNote { color: #64748b; padding: 4px 2px; }
        """)

    def _load_initial_state(self) -> None:
        self.solver_path_edit.setText(str(ROOT / "OS_terminal" / "OpenSees.exe"))
        self.tcl_library_edit.setText(str(ROOT / "OS_terminal" / "lib" / "tcl8.6"))
        self.amplitudes_edit.setPlainText("0, 8, -8, 12, -12, 16, -16, 20, -20, 0")
        self._populate_material(self._empty_material_template())
        self.current_template_path = None
        self.template_path_edit.setText("空材料集合（请从材料库添加）")
        self.topology_editor.set_root(TopologyNode(
            kind="material",
            material_tag=None,
            name="E01",
        ))
        self._sync_fit_table()
        self._update_model_status()
        self._refresh_comparison_view()
        self._append_log("已创建空白材料测试项目")

    def browse_solver(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 OpenSees", str(ROOT), "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self.solver_path_edit.setText(path)

    def browse_tcl_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Tcl 库文件夹", str(ROOT))
        if path:
            self.tcl_library_edit.setText(path)

    def browse_material_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择材料模板", str(ROOT), "YAML (*.yaml *.yml);;所有文件 (*)")
        if path:
            self._load_template(Path(path))

    def open_material_library(self) -> None:
        try:
            existing_tags = self._defined_material_tags_from_ui()
            library = load_material_library()
        except Exception as exc:
            QMessageBox.critical(self, "材料库", str(exc))
            return

        dialog = MaterialLibraryDialog(
            library,
            default_tag=max(existing_tags, default=0) + 1,
            existing_tags=existing_tags,
            solver=self._solver_from_ui(),
            area=float(self.area_spin.value()),
            length=float(self.length_spin.value()),
            parent=self,
        )
        if dialog.exec() != MaterialLibraryDialog.DialogCode.Accepted:
            return
        entry = dialog.selected_entry
        if entry is None or dialog.selected_action is None:
            return

        try:
            if dialog.selected_action == "replace":
                template = entry.as_template(tag=dialog.selected_tag)
                self._populate_material(template, selected_tag=dialog.selected_tag)
                self.topology_editor.clear_undefined_assignments()
                action_text = "替换为"
            else:
                added = entry.instantiate(tag=dialog.selected_tag)
                if self._material_commands:
                    current = self._material_from_ui()
                    template = MaterialTemplate(
                        name=f"{current.name} + {entry.name}",
                        description="由内置材料库组合生成",
                        command=f"{current.command.rstrip()}\n{added.command.lstrip()}",
                        tag=current.tag,
                        parameters=[*current.parameters, *added.parameters],
                    )
                else:
                    template = added
                template.validate()
                self._populate_material(template, selected_tag=dialog.selected_tag)
                action_text = "添加"
        except Exception as exc:
            QMessageBox.critical(self, "材料库", str(exc))
            return

        self.current_template_path = None
        self.template_path_edit.setText("内置材料库组合（随项目保存）")
        self._sync_fit_table()
        self._update_model_status()
        self._mark_comparison_dirty("材料资源已修改")
        self._append_log(f"已从材料库{action_text}：{entry.display_name}（Tag {dialog.selected_tag}）")

    def delete_selected_material(
        self,
        *,
        confirm: bool = True,
        replacement_tag: int | None = None,
    ) -> bool:
        """Delete a material resource while preserving assembly elements."""
        row = self._selected_material_row
        if row < 0 or row >= len(self._material_commands):
            return False
        command = self._material_commands[row]
        reference_count = self.topology_editor.count_material_references(command.tag)
        if confirm:
            if reference_count:
                choices = ["解除赋值（保留单元）"] + [
                    f"替换为 Tag {item.tag} · {item.material_type}"
                    for item in self._material_commands
                    if item.tag != command.tag
                ]
                choice, accepted = QInputDialog.getItem(
                    self,
                    "删除已使用材料",
                    f"Tag {command.tag} · {command.material_type} 已用于 {reference_count} 个单元。\n"
                    "请选择这些单元的处理方式：",
                    choices,
                    0,
                    False,
                )
                if not accepted:
                    return False
                if choice.startswith("替换为 Tag "):
                    replacement_tag = int(choice.split()[2])
            else:
                answer = QMessageBox.question(
                    self,
                    "删除材料",
                    f"确定删除 Tag {command.tag} · {command.material_type} 吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False

        valid_replacements = {
            item.tag for item in self._material_commands if item.tag != command.tag
        }
        if replacement_tag is not None and replacement_tag not in valid_replacements:
            raise ValueError(f"替换材料 Tag {replacement_tag} 不存在或正被删除")

        self._commit_selected_parameter_values()
        self._material_lines.pop(command.line_index)
        remaining_command_text = "\n".join(self._material_lines).strip()
        remaining_placeholders = MaterialTemplate(
            name="remaining-materials",
            command=remaining_command_text,
        ).placeholders()
        self._material_parameter_order = [
            name for name in self._material_parameter_order if name in remaining_placeholders
        ]
        self._material_parameters = {
            name: parameter
            for name, parameter in self._material_parameters.items()
            if name in remaining_placeholders
        }

        remaining_parameters = [
            ParameterSpec.from_mapping(self._material_parameters[name].to_mapping())
            for name in self._material_parameter_order
        ]
        provisional = MaterialTemplate(
            name=self.material_name_edit.text().strip() or "Material",
            description=self._material_template_description,
            command=remaining_command_text,
            tag=self._material_template_tag,
            parameters=remaining_parameters,
        )
        remaining_commands = parse_material_commands(provisional)
        fallback_tag = None
        if remaining_commands:
            fallback_row = min(row, len(remaining_commands) - 1)
            fallback_tag = remaining_commands[fallback_row].tag
        if fallback_tag is not None and self._material_template_tag == command.tag:
            provisional.tag = fallback_tag

        duplicate_tag_remains = any(item.tag == command.tag for item in remaining_commands)
        changed_assignments = 0
        if not duplicate_tag_remains:
            changed_assignments = self.topology_editor.reassign_material_tag(
                command.tag,
                replacement_tag,
            )

        self._populate_material(provisional, selected_tag=fallback_tag)
        self.current_template_path = None
        self.template_path_edit.setText(
            "项目内材料（已编辑，随项目保存）"
            if remaining_commands
            else "空材料集合（随项目保存）"
        )
        self._sync_fit_table()
        self._update_model_status()
        if changed_assignments and replacement_tag is not None:
            suffix = f"，{changed_assignments} 个单元已替换为 Tag {replacement_tag}"
        elif changed_assignments:
            suffix = f"，{changed_assignments} 个单元已保留并解除材料赋值"
        else:
            suffix = ""
        self._mark_comparison_dirty("材料资源已修改")
        self._append_log(f"已删除材料：Tag {command.tag} · {command.material_type}{suffix}")
        return True

    def calculate_selected_material_preview(self) -> None:
        if self._selected_material_row < 0 or self._selected_material_row >= len(self._material_commands):
            self.material_preview_plot.clear("请先选择一个材料命令")
            return
        command = self._material_commands[self._selected_material_row]
        try:
            base_material = self._material_from_ui()
            rendered_material = MaterialTemplate(
                name=base_material.name,
                command=base_material.render_command(),
                tag=command.tag,
            )
            protocol_mode = str(self.preview_protocol_combo.currentData())
            history = (
                history_from_protocol(self._loading_protocol_from_ui())
                if protocol_mode == "current"
                else standard_preview_history(command.material_type)
            )
            modeling = ModelingConfig()
            preview_type = command.material_type
            title = f"Tag {command.tag} · {command.material_type}"
            curve = calculate_material_preview(
                solver=self._solver_from_ui(),
                material=rendered_material,
                material_type=preview_type,
                area=float(self.area_spin.value()),
                length=float(self.length_spin.value()),
                displacement_history=history,
                modeling=modeling,
            )
        except Exception as exc:
            self.material_preview_plot.clear("曲线计算失败")
            self.material_preview_note.setText(preview_error_summary(exc))
            self._append_log(f"单材曲线计算失败（Tag {command.tag}）：\n{exc}")
            return

        if curve.available:
            self.material_preview_plot.update_curve(curve.displacement, curve.force, title=title)
            geometry_note = f"面积 {self.area_spin.value():g}，长度 {self.length_spin.value():g}"
            self.material_preview_note.setText("；".join(item for item in (geometry_note, curve.note) if item))
        else:
            self.material_preview_plot.clear("需要瞬态 / 速率相关协议")
            self.material_preview_note.setText(curve.note)

    def calculate_model_preview(self) -> None:
        try:
            base_material = self._material_from_ui()
            rendered_material = MaterialTemplate(
                name=base_material.name,
                command=base_material.render_command(),
                tag=base_material.tag,
            )
            modeling = self.topology_editor.modeling()
            protocol_mode = str(self.model_preview_protocol_combo.currentData())
            if protocol_mode == "standard" and self._material_commands:
                row = max(0, min(self._selected_material_row, len(self._material_commands) - 1))
                history = standard_preview_history(self._material_commands[row].material_type)
                if history is None:
                    history = history_from_protocol(self._loading_protocol_from_ui())
            else:
                history = history_from_protocol(self._loading_protocol_from_ui())
            curve = calculate_material_preview(
                solver=self._solver_from_ui(),
                material=rendered_material,
                material_type="ExplicitModel",
                area=float(self.area_spin.value()),
                length=float(self.length_spin.value()),
                displacement_history=history,
                modeling=modeling,
            )
        except Exception as exc:
            self.model_preview_plot.clear("整体曲线计算失败")
            self.model_preview_note.setText(preview_error_summary(exc))
            self._append_log(f"支撑装配曲线计算失败：\n{exc}")
            return

        if curve.available:
            self.model_preview_plot.update_curve(
                curve.displacement,
                curve.force,
                title="支撑装配整体滞回响应",
            )
            self.model_preview_note.setText(
                f"OpenSees 计算结果 · 面积 {self.area_spin.value():g} · 总长度 {self.length_spin.value():g}"
            )
        else:
            self.model_preview_plot.clear("当前协议无法计算整体响应")
            self.model_preview_note.setText(curve.note)

    def _solver_from_ui(self) -> SolverConfig:
        solver_text = self.solver_path_edit.text().strip()
        if not solver_text:
            raise ValueError("必须选择 OpenSees 可执行文件")
        tcl_text = self.tcl_library_edit.text().strip()
        return SolverConfig(
            executable_path=Path(solver_text),
            tcl_library_path=Path(tcl_text) if tcl_text else None,
            timeout=float(self.timeout_spin.value()),
        )

    def _loading_protocol_from_ui(self) -> LoadingProtocol:
        history_text = self.history_path_edit.text().strip()
        if history_text:
            return LoadingProtocol(
                history_path=Path(history_text),
                history_column=self.history_column_combo.currentText() or "0",
                append_zero=self.append_zero_check.isChecked(),
            )
        return LoadingProtocol(amplitudes=self._parse_amplitudes(), du=float(self.du_spin.value()))

    def browse_experiment_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择试验数据", str(ROOT), "数据文件 (*.csv *.txt *.xlsx *.xls);;所有文件 (*)")
        if path:
            self.experiment_path_edit.setText(path)
            self._load_experiment_columns(Path(path))

    def browse_loading_history(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择加载历史", str(ROOT), "数据文件 (*.csv *.txt *.xlsx *.xls);;所有文件 (*)")
        if path:
            self.history_path_edit.setText(path)
            self._load_history_columns(Path(path))

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", str(ROOT), "YAML (*.yaml *.yml);;所有文件 (*)")
        if not path:
            return
        try:
            self._populate_from_config(load_project_config(path))
        except Exception as exc:
            QMessageBox.critical(self, "打开项目", str(exc))
            return
        self.current_project_path = Path(path).resolve()
        self.project_label.setText(self.current_project_path.name)
        self._append_log(f"已打开项目：{path}")

    def save_project(self) -> None:
        start = str(self.current_project_path or ROOT / "examples" / "projects")
        path, _ = QFileDialog.getSaveFileName(self, "保存项目", start, "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            save_project_config(self._config_from_ui(require_ready=False), path)
        except Exception as exc:
            QMessageBox.critical(self, "保存项目", str(exc))
            return
        self.current_project_path = Path(path).resolve()
        self.project_label.setText(self.current_project_path.name)
        self._append_log(f"已保存项目：{path}")

    def start_run(self) -> None:
        try:
            config = self._comparison_config_from_ui()
        except Exception as exc:
            QMessageBox.critical(self, "运行分析", str(exc))
            return
        self._set_running(True, "OpenSees 计算中…")
        self._append_log("开始材料分析…")
        self._thread = QThread(self)
        self._worker = RunWorker(config)
        self._start_worker(self._worker, self._run_finished)

    def _comparison_config_from_ui(self) -> ProjectConfig:
        config = self._config_from_ui(require_ready=True)
        if (
            hasattr(self, "comparison_protocol_combo")
            and self.comparison_protocol_combo.currentData() == "experiment"
        ):
            if (
                config.experiment_path is None
                or config.displacement_column is None
                or config.force_column is None
            ):
                raise ValueError("选择试验位移历程前，请先导入试验数据并指定位移列和力列")
            config.loading = LoadingProtocol(
                history_path=config.experiment_path,
                history_column=config.displacement_column,
                history_sheet=config.experiment_sheet,
                append_zero=False,
            )
        return config

    def start_fit(self) -> None:
        try:
            config = self._config_from_ui(require_fitting=True, require_ready=True)
        except Exception as exc:
            QMessageBox.critical(self, "参数拟合", str(exc))
            return
        self._set_running(True, "参数拟合中…")
        self._append_log("开始参数拟合…")
        self._thread = QThread(self)
        self._worker = FitWorker(config)
        self._start_worker(self._worker, self._fit_finished)

    def _start_worker(self, worker: RunWorker | FitWorker, finished_callback) -> None:
        assert self._thread is not None
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.finished.connect(finished_callback)
        worker.failed.connect(self._run_failed)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, '结果保存目录', self.output_dir_edit.text())
        if path:
            self.output_dir_edit.setText(path)

    def publish_brace(self) -> None:
        if isinstance(self.current_result, FitResult):
            QMessageBox.information(self, '发布支撑模型', '拟合参数已回填。请先在“计算与对比”运行完整加载历程，再发布带完整输入与结果校验的支撑包。')
            return
        if self.current_result is None:
            QMessageBox.information(self, '发布支撑模型', '请先用实际实验数据计算当前模型，再发布可执行支撑包。')
            return
        try:
            from material_test.brace_export import build_brace_package
            import json
            config = self._config_from_ui()
            package = build_brace_package(config, result_dir=self.current_result.run_dir)
            dialog = QDialog(self)
            dialog.setWindowTitle('支撑模型单位与接口')
            layout = QVBoxLayout(dialog)
            note = QLabel('接口：两端轴向连接。请选择当前输入数值采用的单位。\n面积需与长度单位的平方一致；本构按 A/L 转为支撑力—变形关系。')
            note.setWordWrap(True)
            layout.addWidget(note)
            form = QFormLayout()
            force = QComboBox(); force.addItems(['N', 'kN', 'MN'])
            length = QComboBox(); length.addItems(['mm', 'cm', 'm'])
            form.addRow('力', force); form.addRow('长度 / 位移', length)
            layout.addLayout(form)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            package = build_brace_package(config, result_dir=self.current_result.run_dir,
                                          force_unit=force.currentText(), displacement_unit=length.currentText())
            path, _ = QFileDialog.getSaveFileName(self, '发布支撑模型', str(self.current_result.run_dir / 'support.oasbrace'), '支撑模型包 (*.oasbrace)')
            if not path:
                return
            Path(path).write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
            self._append_log(f'已发布支撑包：{path}。在 Structural analysis → 组件装配 → 导入支撑模型中打开。')
            QMessageBox.information(self, '支撑模型已发布', f'已保存两个轴向端口、可执行本构、试验与计算来源。\n{path}\n在 Structural analysis 的组件装配页导入后执行匹配检查。')
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, '无法发布支撑模型', str(exc))

    def export_figure(self) -> None:
        if self.current_result is None:
            QMessageBox.information(self, "导出当前对比图", "当前没有可导出的计算结果。")
            return
        start = self.current_result.run_dir / "current_comparison.png"
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出当前对比图",
            str(start),
            "PNG 图片 (*.png);;PDF 矢量图 (*.pdf);;SVG 矢量图 (*.svg)",
        )
        if not path:
            return
        output_path = Path(path)
        if not output_path.suffix:
            suffix = ".pdf" if "PDF" in selected_filter else ".svg" if "SVG" in selected_filter else ".png"
            output_path = output_path.with_suffix(suffix)
        baseline = (
            self.baseline_result.simulation
            if isinstance(self.baseline_result, (WorkflowResult, FitResult))
            else None
        )
        save_comparison_plot(
            output_path,
            self.current_result.simulation,
            experiment=self._comparison_experiment(),
            baseline=baseline,
        )
        self._append_log(f"已导出当前对比图：{output_path}")

    def clear_log(self) -> None:
        self.log_edit.clear()

    def remove_selected_parameter(self) -> None:
        rows = sorted({index.row() for index in self.param_table.selectedIndexes()}, reverse=True)
        if not rows and self.param_table.rowCount():
            rows = [self.param_table.rowCount() - 1]
        for row in rows:
            self.param_table.removeRow(row)

    def _run_finished(self, result: WorkflowResult) -> None:
        self.current_result = result
        self._comparison_dirty = False
        self._append_log(f"分析完成：{result.run_dir}")
        self._append_log(f"叠加曲线：{result.figure_file}")
        if result.simulation.stdout.strip():
            self._append_log(result.simulation.stdout.strip())
        if result.simulation.stderr.strip():
            self._append_log(result.simulation.stderr.strip())
        self.run_state_label.setText("分析完成")
        self._refresh_comparison_view()
        self._refresh_fitting_preview()

    def _fit_finished(self, result: FitResult) -> None:
        self.current_result = result
        for parameter in result.fitted_material.parameters:
            if parameter.name in self._material_parameters:
                self._set_material_parameter_value(parameter.name, parameter.value)
        self._comparison_dirty = False
        self._append_log(f"拟合完成：{result.run_dir}")
        self._append_log(f"算法：{result.global_algorithm}；全局 {result.global_evaluations} 次；局部 {result.local_evaluations} 次")
        self._append_log(f"归一化 RMSE：{result.metrics.normalized_rmse:.6g}")
        self._append_log(f"拟合材料：{result.material_file}")
        self.run_state_label.setText("拟合完成")
        self._refresh_comparison_view()
        self._refresh_fitting_preview()

    def _run_failed(self, message: str) -> None:
        self._append_log(message)
        QMessageBox.critical(self, "运行失败", preview_error_summary(RuntimeError(message)))
        self.run_state_label.setText("运行失败")

    def _thread_finished(self) -> None:
        self._set_running(False, self.run_state_label.text())
        self._thread = None
        self._worker = None

    def _set_running(self, running: bool, text: str) -> None:
        self.publish_button.setEnabled(not running and self.current_result is not None)
        self.publish_brace_action.setEnabled(not running)
        self.run_button.setEnabled(not running)
        self.fit_page_button.setEnabled(not running)
        self.set_baseline_button.setEnabled(
            not running and isinstance(self.current_result, (WorkflowResult, FitResult))
        )
        self.clear_baseline_button.setEnabled(not running and self.baseline_result is not None)
        self.export_button.setEnabled(
            not running and isinstance(self.current_result, (WorkflowResult, FitResult))
        )
        self.run_state_label.setText(text)
        self.statusBar().showMessage(text)

    def _load_template(self, path: Path) -> None:
        template = load_material_template(path)
        self.current_template_path = path.resolve()
        self.template_path_edit.setText(str(path))
        self._populate_material(template)
        self._mark_comparison_dirty("材料模板已修改")
        self._append_log(f"已加载材料模板：{path}")

    def _populate_from_config(self, config: ProjectConfig) -> None:
        self._reset_comparison_results()
        self.solver_path_edit.setText(str(config.solver.executable_path))
        self.tcl_library_edit.setText(str(config.solver.tcl_library_path or ""))
        self.timeout_spin.setValue(float(config.solver.timeout))
        self.output_dir_edit.setText(str(config.output_dir))
        self.area_spin.setValue(float(config.area))
        self.length_spin.setValue(float(config.length))
        self.current_template_path = config.material_template_path
        self.template_path_edit.setText(str(config.material_template_path or ""))
        self._populate_material(config.material)
        self.topology_editor.set_modeling(config.modeling, config.material.tag)
        self.amplitudes_edit.setPlainText(", ".join(f"{value:g}" for value in config.loading.amplitudes))
        self.du_spin.setValue(float(config.loading.du))
        self.history_path_edit.setText(str(config.loading.history_path or ""))
        self.append_zero_check.setChecked(config.loading.append_zero)
        if config.loading.history_path and config.loading.history_path.exists():
            self._load_history_columns(config.loading.history_path)
            self._select_combo_text(self.history_column_combo, config.loading.history_column)
        self.experiment_path_edit.setText(str(config.experiment_path or ""))
        if config.experiment_path and config.experiment_path.exists():
            self._load_experiment_columns(config.experiment_path)
            self._select_combo_text(self.displacement_combo, config.displacement_column)
            self._select_combo_text(self.force_combo, config.force_column)
        self._sync_fit_table()
        self._populate_fitting(config.fitting)
        self._update_model_status()
        self._update_material_usage()
        self._refresh_project_overview()
        self._refresh_loading_preview()
        self._refresh_experiment_preview()
        self._refresh_fitting_preview()

    def _populate_material(self, template: MaterialTemplate, *, selected_tag: int | None = None) -> None:
        self.material_name_edit.setText(template.name)
        self._material_template_tag = int(template.tag)
        self._material_template_description = template.description
        self._material_lines = template.command.splitlines()
        self._material_parameter_order = [parameter.name for parameter in template.parameters]
        self._material_parameters = {
            parameter.name: ParameterSpec.from_mapping(parameter.to_mapping())
            for parameter in template.parameters
        }
        self._material_commands = parse_material_commands(template)
        self.topology_editor.set_material_options([
            (command.tag, command.material_type) for command in self._material_commands
        ])
        self._selected_material_row = -1
        self._rebuild_material_command_table(selected_tag if selected_tag is not None else template.tag)
        self._sync_comparison_materials(selected_tag if selected_tag is not None else template.tag)

    def _rebuild_material_command_table(self, selected_tag: int) -> None:
        self._updating_material_editor = True
        self.material_command_table.setRowCount(0)
        selected_row = 0
        for command in self._material_commands:
            row = self.material_command_table.rowCount()
            self.material_command_table.insertRow(row)
            self.material_command_table.setItem(row, 0, QTableWidgetItem(str(command.tag)))
            self.material_command_table.setItem(row, 1, QTableWidgetItem(command.material_type))
            self.material_command_table.setItem(row, 2, QTableWidgetItem(command.source))
            references = self.topology_editor.count_material_references(command.tag)
            self.material_command_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{references} 个单元" if references else "未使用"),
            )
            delete_button = QToolButton()
            delete_button.setObjectName("dangerTool")
            delete_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            delete_button.setToolTip(
                "删除该材料；若已用于装配，单元将保留并解除赋值"
            )
            delete_button.setEnabled(True)
            delete_button.clicked.connect(
                lambda checked=False, target_row=row: self._delete_material_row(target_row)
            )
            self.material_command_table.setCellWidget(row, 4, delete_button)
            self.material_command_table.setRowHeight(row, 38)
            if command.tag == selected_tag:
                selected_row = row
        tags = [command.tag for command in self._material_commands]
        self.material_tag_spin.set_material_tags(tags, selected_tag)
        self._updating_material_editor = False
        if self._material_commands:
            self.material_command_table.setCurrentCell(selected_row, 0)
            self.material_command_table.selectRow(selected_row)
            self._select_material_command(selected_row)
        else:
            self.material_command_edit.clear()
            self.param_table.setRowCount(0)
            self.selected_material_label.setText("尚未添加材料")
            self.selected_material_usage_label.setText("请点击“打开材料库”添加 OpenSees 材料")
            self.material_preview_plot.clear("材料资源为空")
            self.material_preview_note.setText("添加材料后可查看参数和滞回曲线")

    def _delete_material_row(self, row: int) -> None:
        if row < 0 or row >= len(self._material_commands):
            return
        self.material_command_table.setCurrentCell(row, 0)
        self.material_command_table.selectRow(row)
        self._select_material_command(row)
        self.delete_selected_material()

    def _select_material_command(self, row: int) -> None:
        if self._updating_material_editor or row < 0 or row >= len(self._material_commands):
            return
        self._commit_selected_parameter_values()
        self._selected_material_row = row
        command = self._material_commands[row]
        self._updating_material_editor = True
        self.material_command_table.selectRow(row)
        self.material_tag_spin.setValue(command.tag)
        self.material_command_edit.setPlainText(command.source)
        self.selected_material_label.setText(f"Tag {command.tag} · {command.material_type} 参数说明")
        references = self.topology_editor.count_material_references(command.tag)
        self.selected_material_usage_label.setText(
            f"已分配给 {references} 个装配单元 · 参数请在第 5 步“计算与对比”中修改"
            if references
            else "尚未分配到装配单元 · 参数请在第 5 步“计算与对比”中修改"
        )
        self.param_table.setRowCount(0)
        for parameter_name in command.parameter_names:
            parameter = self._material_parameters.get(parameter_name)
            if parameter is not None:
                self._append_parameter_row(parameter)
        self._updating_material_editor = False
        self.material_preview_plot.clear("点击“计算曲线”查看当前参数响应")
        self.material_preview_note.clear()
        if hasattr(self, "comparison_material_combo"):
            comparison_index = self.comparison_material_combo.findData(command.tag)
            if comparison_index >= 0 and comparison_index != self.comparison_material_combo.currentIndex():
                self.comparison_material_combo.setCurrentIndex(comparison_index)

    def _select_material_tag(self, tag: int) -> None:
        if self._updating_material_editor:
            return
        for row, command in enumerate(self._material_commands):
            if command.tag == int(tag):
                self.material_command_table.setCurrentCell(row, 0)
                self.material_command_table.selectRow(row)
                return

    def _append_parameter_row(self, parameter: ParameterSpec) -> None:
        row = self.param_table.rowCount()
        self.param_table.insertRow(row)
        values = (parameter.name, parameter.description, parameter.unit, parameter.effect)
        for column, value in ((0, values[0]), (1, values[1]), (3, values[2]), (4, values[3])):
            item = QTableWidgetItem(value)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setToolTip(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, parameter.name)
            self.param_table.setItem(row, column, item)
        editor = CompactDoubleSpinBox()
        editor.setDecimals(12)
        editor.setRange(
            parameter.min if parameter.min is not None else -1.0e15,
            parameter.max if parameter.max is not None else 1.0e15,
        )
        editor.setValue(parameter.value)
        editor.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        editor.setReadOnly(True)
        editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        editor.setToolTip("参数修改已集中到第 5 步“计算与对比”")
        editor.setMinimumWidth(120)
        editor.valueChanged.connect(
            lambda value, name=parameter.name: self._set_material_parameter_value(name, value)
        )
        self.param_table.setCellWidget(row, 2, editor)
        self.param_table.setRowHeight(row, 42)

    def _set_material_parameter_value(self, name: str, value: float) -> None:
        parameter = self._material_parameters.get(name)
        new_value = float(value)
        if parameter is not None and not np.isclose(parameter.value, new_value, rtol=0.0, atol=1.0e-12):
            parameter.value = new_value
            self._sync_parameter_value_widgets(name, new_value)
            if hasattr(self, "model_preview_plot"):
                self.model_preview_plot.clear("材料参数已修改，点击重新计算整体响应")
                self.model_preview_note.setText("待更新 · 当前整体曲线已失效")
            self._mark_comparison_dirty("材料参数已修改")

    def _sync_parameter_value_widgets(self, name: str, value: float) -> None:
        for table, name_column, value_column in (
            (getattr(self, "param_table", None), 0, 2),
            (getattr(self, "comparison_parameter_table", None), 0, 2),
        ):
            if not isinstance(table, QTableWidget):
                continue
            for row in range(table.rowCount()):
                item = table.item(row, name_column)
                editor = table.cellWidget(row, value_column)
                if item is None or not isinstance(editor, QDoubleSpinBox):
                    continue
                parameter_name = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
                if parameter_name != name or np.isclose(editor.value(), value):
                    continue
                editor.blockSignals(True)
                editor.setValue(value)
                editor.blockSignals(False)

    def _commit_selected_parameter_values(self) -> None:
        for row in range(self.param_table.rowCount()):
            item = self.param_table.item(row, 0)
            editor = self.param_table.cellWidget(row, 2)
            if item is None or not isinstance(editor, QDoubleSpinBox):
                continue
            name = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
            self._set_material_parameter_value(name, editor.value())

    def _sync_fit_table(self) -> None:
        self._commit_selected_parameter_values()
        existing: dict[str, tuple[bool, str, str, str]] = {}
        for row in range(self.fit_table.rowCount()):
            name = self._table_text(row, 1, self.fit_table)
            check = self.fit_table.item(row, 0)
            scale = self.fit_table.cellWidget(row, 4)
            existing[name] = (
                check is not None and check.checkState() == Qt.CheckState.Checked,
                self._table_text(row, 2, self.fit_table), self._table_text(row, 3, self.fit_table),
                str(scale.currentData()) if isinstance(scale, QComboBox) else "linear",
            )
        self.fit_table.setRowCount(0)
        for name in self._material_parameter_order:
            parameter = self._material_parameters.get(name)
            if parameter is None:
                continue
            checked, min_text, max_text, scale_value = existing.get(name, (
                False,
                "" if parameter.min is None else f"{parameter.min:g}",
                "" if parameter.max is None else f"{parameter.max:g}",
                "linear",
            ))
            row = self.fit_table.rowCount()
            self.fit_table.insertRow(row)
            select_item = QTableWidgetItem()
            select_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            select_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.fit_table.setItem(row, 0, select_item)
            name_item = QTableWidgetItem(name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.fit_table.setItem(row, 1, name_item)
            self.fit_table.setItem(row, 2, QTableWidgetItem(min_text))
            self.fit_table.setItem(row, 3, QTableWidgetItem(max_text))
            scale_combo = QComboBox()
            scale_combo.addItem("线性", "linear")
            scale_combo.addItem("对数", "log")
            self._select_combo_data(scale_combo, scale_value)
            self.fit_table.setCellWidget(row, 4, scale_combo)

    def _populate_fitting(self, fitting: FitConfig | None) -> None:
        for row in range(self.fit_table.rowCount()):
            self.fit_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        if fitting is None:
            return
        self._select_combo_data(self.fit_algorithm_combo, fitting.algorithm)
        self.fit_max_evaluations_spin.setValue(fitting.max_evaluations)
        self.fit_population_spin.setValue(fitting.population_size)
        self.fit_local_refine_check.setChecked(fitting.local_refine)
        self.fit_local_evaluations_spin.setValue(fitting.local_max_evaluations)
        selected = {parameter.name: parameter for parameter in fitting.parameters}
        for row in range(self.fit_table.rowCount()):
            spec = selected.get(self._table_text(row, 1, self.fit_table))
            if spec is None:
                continue
            self.fit_table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            self.fit_table.item(row, 2).setText(f"{spec.min:g}")
            self.fit_table.item(row, 3).setText(f"{spec.max:g}")
            scale = self.fit_table.cellWidget(row, 4)
            if isinstance(scale, QComboBox):
                self._select_combo_data(scale, spec.scale)

    def _load_experiment_columns(self, path: Path) -> None:
        self._load_columns(path, self.displacement_combo, self.force_combo)
        self._select_best_column(self.displacement_combo, ["disp", "deform", "u", "x"])
        self._select_best_column(self.force_combo, ["force", "load", "f", "y"])

    def _load_history_columns(self, path: Path) -> None:
        self._load_columns(path, self.history_column_combo)
        self._select_best_column(self.history_column_combo, ["disp", "deform", "u", "x", "0"])

    def _load_columns(self, path: Path, *combos: QComboBox) -> None:
        try:
            columns = list_data_columns(path)
        except Exception as exc:
            QMessageBox.critical(self, "数据列", str(exc))
            return
        for combo in combos:
            combo.clear()
            combo.addItems(columns)

    def _config_from_ui(
        self,
        *,
        require_fitting: bool = False,
        require_ready: bool = True,
    ) -> ProjectConfig:
        solver_text = self.solver_path_edit.text().strip()
        if not solver_text:
            raise ValueError("必须选择 OpenSees 可执行文件")
        experiment_text = self.experiment_path_edit.text().strip()
        experiment_path = Path(experiment_text) if experiment_text else None
        history_text = self.history_path_edit.text().strip()
        if history_text:
            loading = LoadingProtocol(
                history_path=Path(history_text), history_column=self.history_column_combo.currentText() or "0",
                append_zero=self.append_zero_check.isChecked(),
            )
        else:
            loading = LoadingProtocol(amplitudes=self._parse_amplitudes(), du=float(self.du_spin.value()))
        material = self._material_from_ui()
        if require_ready and not self._material_commands:
            raise ValueError("材料资源为空，请先从材料库添加至少一个材料")
        modeling = self.topology_editor.modeling(require_assigned=require_ready)
        self._sync_fit_table()
        fitting = self._fitting_from_ui(material)
        if require_fitting and fitting is None:
            raise ValueError("请至少选择一个待拟合参数")
        tcl_text = self.tcl_library_edit.text().strip()
        return ProjectConfig(
            solver=SolverConfig(
                executable_path=Path(solver_text), tcl_library_path=Path(tcl_text) if tcl_text else None,
                timeout=float(self.timeout_spin.value()),
            ),
            material=material, loading=loading, experiment_path=experiment_path,
            displacement_column=self.displacement_combo.currentText() if experiment_path else None,
            force_column=self.force_combo.currentText() if experiment_path else None,
            output_dir=Path(self.output_dir_edit.text()).expanduser().resolve(), material_template_path=self.current_template_path,
            area=float(self.area_spin.value()), length=float(self.length_spin.value()),
            fitting=fitting, modeling=modeling,
        )

    def _material_from_ui(self) -> MaterialTemplate:
        self._commit_selected_parameter_values()
        parameters = [
            ParameterSpec.from_mapping(self._material_parameters[name].to_mapping())
            for name in self._material_parameter_order
            if name in self._material_parameters
        ]
        material = MaterialTemplate(
            name=self.material_name_edit.text().strip() or "Material",
            description=self._material_template_description,
            command="\n".join(self._material_lines).strip(),
            tag=self._material_template_tag,
            parameters=parameters,
        )
        material.validate()
        return material

    @staticmethod
    def _empty_material_template() -> MaterialTemplate:
        return MaterialTemplate(
            name="空材料集合",
            description="尚未添加 OpenSees 单轴材料",
            command="",
            tag=1,
            parameters=[],
        )

    def _defined_material_tags_from_ui(self) -> set[int]:
        return defined_material_tags(self._material_from_ui().render_command())

    def _fitting_from_ui(self, material: MaterialTemplate) -> FitConfig | None:
        selected: list[FitParameterSpec] = []
        known = {parameter.name for parameter in material.parameters}
        for row in range(self.fit_table.rowCount()):
            item = self.fit_table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            name = self._table_text(row, 1, self.fit_table)
            if name not in known:
                continue
            min_text = self._table_text(row, 2, self.fit_table)
            max_text = self._table_text(row, 3, self.fit_table)
            if not min_text or not max_text:
                raise ValueError(f"拟合参数 {name} 必须设置有限的下限和上限")
            scale_combo = self.fit_table.cellWidget(row, 4)
            scale = scale_combo.currentData() if isinstance(scale_combo, QComboBox) else "linear"
            selected.append(FitParameterSpec(name, float(min_text), float(max_text), str(scale)))
        if not selected:
            return None
        return FitConfig(
            parameters=selected, algorithm=str(self.fit_algorithm_combo.currentData()),
            max_evaluations=int(self.fit_max_evaluations_spin.value()),
            population_size=int(self.fit_population_spin.value()),
            local_refine=self.fit_local_refine_check.isChecked(),
            local_max_evaluations=int(self.fit_local_evaluations_spin.value()),
        )

    def _parse_amplitudes(self) -> list[float]:
        values = [item for item in re.split(r"[\s,;]+", self.amplitudes_edit.toPlainText().strip()) if item]
        if len(values) < 2:
            raise ValueError("循环加载至少需要两个位移幅值")
        return [float(value) for value in values]

    def _update_model_status(self) -> None:
        if not self._material_commands:
            unassigned, _ = self.topology_editor.assignment_issues()
            self.model_status.setText("材料资源为空")
            if hasattr(self, "model_assignment_label"):
                self.model_assignment_label.setText(
                    "材料资源为空；装配可以继续编辑，计算前请添加并分配材料"
                )
            return
        unassigned, undefined = self.topology_editor.assignment_issues()
        if unassigned or undefined:
            messages = []
            if unassigned:
                messages.append(f"未赋值 {len(unassigned)} 个")
            if undefined:
                messages.append(f"材料失效 {len(undefined)} 个")
            self.model_status.setText("装配待完善 · " + "，".join(messages))
            if hasattr(self, "model_assignment_label"):
                detail = []
                if unassigned:
                    detail.append("未赋值：" + "、".join(unassigned))
                if undefined:
                    detail.append("未定义：" + "、".join(undefined))
                self.model_assignment_label.setText("；".join(detail))
            return
        try:
            modeling = self.topology_editor.modeling(require_assigned=True)
            count = len(modeling.root.material_tags()) if modeling.root else 1
        except ValueError:
            self.model_status.setText("模型待校验")
            return
        self.model_status.setText(f"装配有效 · {count} 个单元")
        if hasattr(self, "model_assignment_label"):
            self.model_assignment_label.setText(f"材料赋值完整 · 共 {count} 个单元")

    def _on_topology_changed(self) -> None:
        self._update_model_status()
        self._update_material_usage()
        self._mark_comparison_dirty("支撑装配已修改")
        if hasattr(self, "model_preview_plot"):
            self.model_preview_plot.clear("拓扑或材料赋值已修改，点击重新计算")
            self.model_preview_note.setText("待更新 · 当前整体曲线已失效")

    def _update_material_usage(self) -> None:
        if not hasattr(self, "material_command_table"):
            return
        for row, command in enumerate(self._material_commands):
            references = self.topology_editor.count_material_references(command.tag)
            item = self.material_command_table.item(row, 3)
            if item is not None:
                item.setText(f"{references} 个单元" if references else "未使用")
        if 0 <= self._selected_material_row < len(self._material_commands):
            command = self._material_commands[self._selected_material_row]
            references = self.topology_editor.count_material_references(command.tag)
            self.selected_material_usage_label.setText(
                f"已分配给 {references} 个装配单元"
                if references
                else "尚未分配到装配单元"
            )

    def _table_text(self, row: int, column: int, table: QTableWidget | None = None) -> str:
        item = (table or self.param_table).item(row, column)
        return item.text().strip() if item else ""

    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(message)

    @staticmethod
    def _optional_float(value: str) -> float | None:
        return float(value) if value else None

    @staticmethod
    def _select_combo_text(combo: QComboBox, value: str | int | None) -> None:
        if value is not None:
            index = combo.findText(str(value))
            if index >= 0:
                combo.setCurrentIndex(index)

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _select_best_column(combo: QComboBox, candidates: list[str]) -> None:
        lowered = [(index, combo.itemText(index).lower()) for index in range(combo.count())]
        for candidate in candidates:
            for index, text in lowered:
                if candidate in text:
                    combo.setCurrentIndex(index)
                    return
