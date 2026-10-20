from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRegularExpression, Qt, QThread, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QDialog, QDialogButtonBox,
    QInputDialog, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
    QDockWidget, QMenu, QProgressBar, QStyle, QTabBar, QTabWidget, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
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
from material_test.ui.theme import STYLESHEET
from material_test.ui.workers import FitWorker, RunWorker
from material_test.workflow import WorkflowResult
from material_test.ui.project_session import ProjectSessionMixin
from material_test.ui.assembly_workflow import AssemblyWorkflowMixin
from material_test.ui.fitting_workflow import FittingWorkflowMixin
from material_test.ui.editors import MaterialTagSpinBox, CompactDoubleSpinBox
from material_test.ui.pages.project import ProjectPage
from material_test.ui.pages.loading import LoadingPage
from material_test.ui.pages.material import MaterialPage
from material_test.ui.pages.assembly import AssemblyPage
from material_test.ui.pages.calibration import CalibrationPage


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / "examples" / "materials" / "twostage.yaml"
DEFAULT_PROJECT = ROOT / "examples" / "projects" / "twostage_demo.yaml"
PAGES = [
    ("项目与单位", "求解器、单位与本构输入模式"),
    ("试验与加载", "试验可选 · 统一加载来源"),
    ("材料", "添加材料、编辑参数与单材预览"),
    ("支撑装配", "节点、单元与材料赋予"),
    ("对比与标定", "手动调参、自动拟合与发布"),
]


class MainWindow(AssemblyWorkflowMixin, ProjectSessionMixin, FittingWorkflowMixin, QMainWindow):
    def __init__(self, *, restore_last_project: bool = True) -> None:
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
        self._loading_ui = True
        self._project_dirty = False
        self._generation = 0
        self._auto_run_pending = False
        self._source_badges = []
        self._syncing_fit = False
        self._build_actions()
        self._build_ui()
        self._apply_style()
        self._load_initial_state()
        self._initialize_session()
        if restore_last_project:
            self._restore_last_project()

    def _build_actions(self) -> None:
        self.load_project_action = QAction("打开项目", self)
        self.load_project_action.setShortcut("Ctrl+O")
        self.load_project_action.triggered.connect(self.load_project)
        self.save_project_action = QAction("保存项目", self)
        self.save_project_action.setShortcut("Ctrl+S")
        self.save_project_action.triggered.connect(self.save_project)
        self.save_as_action = QAction("另存为…", self)
        self.save_as_action.setShortcut("Ctrl+Shift+S")
        self.save_as_action.triggered.connect(self.save_project_as)
        self.addActions([self.load_project_action, self.save_project_action, self.save_as_action])
        self.publish_brace_action = QAction('发布支撑模型到 Structural analysis…', self)
        self.publish_brace_action.setShortcut('Ctrl+Shift+P')
        self.publish_brace_action.triggered.connect(self.publish_brace)
        self.addAction(self.publish_brace_action)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
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
        content_layout.setContentsMargins(22, 18, 22, 14)
        content_layout.setSpacing(10)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("pageSubtitle")
        content_layout.addWidget(self.page_title)
        content_layout.addWidget(self.page_subtitle)
        self.pages = QStackedWidget()
        for builder in (
            ProjectPage, LoadingPage, MaterialPage, AssemblyPage, CalibrationPage,
        ):
            page = builder(self)
            # Give split panels a consistent surface and resize affordance.
            for splitter in page.findChildren(QSplitter):
                splitter.setChildrenCollapsible(False)
                splitter.setHandleWidth(10)
                for index in range(splitter.count()):
                    panel = splitter.widget(index)
                    if panel.objectName() or isinstance(panel, (QSplitter, TopologyEditor)):
                        continue
                    panel.setObjectName("surfaceCard")
                    if isinstance(panel, QScrollArea) and panel.widget() is not None:
                        panel.widget().setObjectName("scrollContent")
                        if panel.widget().layout() is not None:
                            panel.widget().layout().setContentsMargins(16, 16, 16, 16)
                    if panel.layout() is not None:
                        panel.layout().setContentsMargins(16, 16, 16, 16)
            self.pages.addWidget(self._wrap_workflow_page(page))
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
        self.project_label.setWordWrap(True)
        self.project_label.setMaximumWidth(320)
        layout.addWidget(mark)
        layout.addWidget(title)
        layout.addSpacing(12)
        layout.addWidget(self.project_label)
        layout.addStretch(1)
        open_button = QPushButton("打开")
        open_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        open_button.clicked.connect(self.load_project)
        self.open_button = open_button
        save_button = QPushButton("保存")
        save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        save_button.clicked.connect(self.save_project)
        self.save_button = save_button
        layout.addWidget(open_button)
        layout.addWidget(save_button)
        return header

    def _build_navigation(self) -> QWidget:
        nav = QFrame()
        nav.setObjectName("navigation")
        nav.setFixedWidth(228)
        layout = QVBoxLayout(nav)
        layout.setContentsMargins(14, 22, 14, 18)
        label = QLabel("分析流程")
        label.setObjectName("navCaption")
        layout.addWidget(label)
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setFrameShape(QFrame.Shape.NoFrame)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setWordWrap(True)
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
        layout.addWidget(self.run_state_label, 1)
        self.run_state_label.show()
        self.log_toggle_button = QPushButton("运行日志")
        self.log_toggle_button.setCheckable(True)
        self.log_toggle_button.toggled.connect(self.log_dock.setVisible)
        self.log_dock.visibilityChanged.connect(self.log_toggle_button.setChecked)
        layout.addWidget(self.log_toggle_button)
        layout.addStretch(1)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.next_button)
        return footer


    def _fit_table_edited(self, *args):
        if not self._syncing_fit:
            self._mark_project_dirty()
            self._validate_fit_inputs()

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

    @staticmethod
    def _wrap_workflow_page(page: QWidget) -> QScrollArea:
        """Keep every workflow page accessible when its content exceeds the window."""
        scroll = QScrollArea()
        scroll.setObjectName("workflowPageScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        scroll.viewport().setAutoFillBackground(False)
        page.setAutoFillBackground(False)
        return scroll

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
        if index == 4:
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
        elif index == 1:
            self._refresh_loading_preview()
            self._refresh_experiment_preview()
        elif index == 4:
            self._sync_comparison_materials()
            self._refresh_comparison_view()
            self._refresh_fitting_preview()

    def _navigate_relative(self, offset: int) -> None:
        self._navigate_to(max(0, min(len(PAGES) - 1, self.nav_list.currentRow() + offset)))

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
            y_label=f"{'转角' if self._response_units()[1]=='rad' else '位移'} [{self._response_units()[1]}]",
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
        experiment = self._comparison_experiment()
        if experiment is None:
            self.experiment_preview_plot.clear("试验数据暂不可用，请检查文件、数据列与换算系数")
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
            meaning = self._parameter_description(parameter)
            for column, value in (
                (0, parameter.name),
                (1, meaning),
                (3, self._parameter_unit(parameter)),
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
            self.stale_notice.hide()
            self.publish_button.setEnabled(False)
            self.comparison_metrics_table.setRowCount(0)
            self.set_baseline_button.setEnabled(False)
            self.clear_baseline_button.setEnabled(self.baseline_result is not None)
            self.export_button.setEnabled(False)
            self._set_status("等待计算当前模型")
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
        self.plot_widget.set_stale(self._comparison_dirty)
        self.stale_notice.setVisible(self._comparison_dirty)
        self.set_baseline_button.setEnabled(not self._comparison_dirty)
        self.export_button.setEnabled(not self._comparison_dirty)
        self.publish_button.setEnabled(not self._comparison_dirty and self._thread is None)
        if self._comparison_dirty:
            self._set_status("参数或模型已修改 · 当前曲线待重新计算", "stale")
        elif baseline_simulation is not None:
            self._set_status("当前与基准曲线已更新", "complete")
        else:
            self._set_status("当前曲线已更新 · 尚未设置基准", "complete")


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
            elif (label != "试验" and experiment is not None and len(displacement) == len(experiment.displacement) + 1
                  and np.isclose(displacement[0], 0.0)
                  and np.allclose(displacement[1:], experiment.displacement, rtol=1e-8, atol=1e-10)):
                errors = calculate_error_metrics(force[1:], experiment.force)
            values = (
                label,
                self._format_metric(metrics.peak_absolute_force),
                self._format_metric(metrics.initial_stiffness),
                self._format_metric(metrics.hysteretic_integral),
                self._format_metric(metrics.residual_force),
                self._format_metric(errors.rmse) if errors else ("不适用（无试验）" if experiment is None else ("—" if label == "试验" else "需用试验位移")),
                self._format_metric(errors.normalized_rmse) if errors else ("不适用（无试验）" if experiment is None else ("—" if label == "试验" else "需用试验位移")),
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
        if not isinstance(self.current_result, (WorkflowResult, FitResult)) or self._comparison_dirty:
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


    def _reset_comparison_results(self) -> None:
        self.current_result = None
        self.baseline_result = None
        self._comparison_dirty = False
        self._clear_assembly_deformation('项目已切换，等待计算', reset=True)
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
        self.setStyleSheet(STYLESHEET)

    def _load_initial_state(self) -> None:
        self.solver_path_edit.setText(str(ROOT / "OS_terminal" / "OpenSees.exe"))
        self.tcl_library_edit.setText(str(ROOT / "OS_terminal" / "lib" / "tcl8.6"))
        self.amplitudes_edit.setPlainText("0, 8, -8, 12, -12, 16, -16, 20, -20, 0")
        self._populate_material(self._empty_material_template())
        self.current_template_path = None
        self.template_path_edit.setText("空材料集合（请从材料库添加）")
        self.topology_editor.new_empty()
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
            try:
                template = load_material_template(path)
                if self._confirm_material_replacement(template):
                    self._load_template(Path(path))
                    self.topology_editor.clear_undefined_assignments()
            except Exception as exc:
                QMessageBox.critical(self, "导入材料模板", str(exc))

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
            material_names={item.tag: item.material_type for item in self._material_commands},
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
                if not self._confirm_material_replacement(template):
                    return
                self._populate_material(template, selected_tag=dialog.selected_tag)
                self.topology_editor.clear_undefined_assignments()
                action_text = "替换为"
            else:
                added = entry.instantiate(
                    tag=dialog.selected_tag, references=dialog.selected_references,
                )
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
        dependents = [
            item for item in self._material_commands
            if item.tag != command.tag and command.tag in item.referenced_tags
        ]
        if dependents:
            owners = "、".join(f"Tag {item.tag} · {item.material_type}" for item in dependents)
            message = f"Tag {command.tag} 被组合材料 {owners} 引用，请先删除引用它的组合材料。"
            self._append_log(message)
            if confirm:
                QMessageBox.warning(self, "材料仍被引用", message)
            return False
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
                material_mode=self.material_mode_combo.currentData(),
            )
        except Exception as exc:
            self.material_preview_plot.clear("曲线计算失败")
            self.material_preview_note.setText(preview_error_summary(exc))
            self._append_log(f"单材曲线计算失败（Tag {command.tag}）：\n{exc}")
            return

        if curve.available:
            self.material_preview_plot.update_curve(curve.displacement, curve.force, title=title, experiment=self._comparison_experiment())
            geometry_note = f"面积 {self.area_spin.value():g}，长度 {self.length_spin.value():g}"
            self.material_preview_note.setText("；".join(item for item in (geometry_note, curve.note) if item))
        else:
            self.material_preview_plot.clear("需要瞬态 / 速率相关协议")
            self.material_preview_note.setText(curve.note)

    def calculate_model_preview(self) -> None:
        self.open_assembly_response()

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


    def browse_experiment_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择试验数据", str(ROOT), "数据文件 (*.csv *.txt *.xlsx *.xls);;所有文件 (*)")
        if path:
            self._experiment_sheet = 0
            self.experiment_path_edit.setText(path)
            self._load_experiment_columns(Path(path))

    def browse_loading_history(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择加载历史", str(ROOT), "数据文件 (*.csv *.txt *.xlsx *.xls);;所有文件 (*)")
        if path:
            self._history_sheet = 0
            self.history_path_edit.setText(path)
            self._load_history_columns(Path(path))


    def browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, '结果保存目录', self.output_dir_edit.text())
        if path:
            self.output_dir_edit.setText(path)


    def export_figure(self) -> None:
        if self.current_result is None or self._comparison_dirty:
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
            force_unit=self._response_units()[0], length_unit=self._response_units()[1],
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


    def _load_template(self, path: Path) -> None:
        template = load_material_template(path)
        self.current_template_path = path.resolve()
        self.template_path_edit.setText(str(path))
        self._populate_material(template)
        self._mark_comparison_dirty("材料模板已修改")
        self._append_log(f"已加载材料模板：{path}")

    def _populate_from_config(self, config: ProjectConfig) -> None:
        previous = self._loading_ui
        self._loading_ui = True
        self._auto_timer.stop()
        self._auto_run_pending = False
        self._last_fit_original = {}
        self.undo_fit_button.setEnabled(False)
        self._experiment_sheet = config.experiment_sheet
        self._history_sheet = config.loading.history_sheet
        self.fit_table.blockSignals(True)
        self.fit_table.setRowCount(0)
        self.fit_table.blockSignals(False)
        try:
            self._populate_project_fields(config)
        finally:
            self._loading_ui = previous
        self._update_navigation_states()

    def _populate_project_fields(self, config: ProjectConfig) -> None:
        self._reset_comparison_results()
        if getattr(self,'assembly_response_dialog',None) is not None:
            self.assembly_response_plot.clear('暂无当前项目的计算结果')
            self.assembly_response_export.setEnabled(False)
            self.assembly_response_cancel.setEnabled(False)
            self.assembly_response_status.setText('项目已切换，等待计算')
        self._select_combo_text(self.force_unit_combo, config.force_unit)
        self._select_combo_text(self.length_unit_combo, config.length_unit)
        self._select_combo_data(self.material_mode_combo, config.material_mode)
        self.experiment_displacement_scale_spin.setValue(config.experiment_displacement_scale)
        self.experiment_force_scale_spin.setValue(config.experiment_force_scale)
        self.history_scale_spin.setValue(config.loading.history_scale)
        self.solver_path_edit.setText(str(config.solver.executable_path))
        self.tcl_library_edit.setText(str(config.solver.tcl_library_path or ""))
        self.timeout_spin.setValue(float(config.solver.timeout))
        self.output_dir_edit.setText(str(config.output_dir))
        self.area_spin.setValue(float(config.area))
        self.length_spin.setValue(float(config.length))
        self.current_template_path = config.material_template_path
        self.template_path_edit.setText(str(config.material_template_path or ""))
        self._populate_material(config.material)
        self.topology_editor.default_area = config.area
        self.topology_editor.default_length = config.length
        self.topology_editor.force_deformation_mode = config.material_mode == 'force_deformation'
        self.topology_editor.set_modeling(config.modeling, config.material.tag)
        values = config.loading.samples if config.loading.source=='samples' else config.loading.amplitudes
        self.amplitudes_edit.setPlainText(", ".join(f"{value:.17g}" for value in values))
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
        source = config.loading.source
        if source == "auto": source = "external" if config.loading.history_path else "amplitudes"
        self._select_combo_data(self.loading_source_combo, source)
        self._units_changed(mark=False)
        self.area_spin.setEnabled(config.material_mode != "force_deformation")
        self.length_spin.setEnabled(config.material_mode != "force_deformation")
        self.topology_editor.force_deformation_mode = config.material_mode == "force_deformation"
        self._update_model_status()
        self._update_material_usage()
        self._refresh_project_overview()
        self._refresh_loading_preview()
        self._refresh_experiment_preview()
        self._refresh_fitting_preview()

    def _populate_material(self, template: MaterialTemplate, *, selected_tag: int | None = None) -> None:
        # Parameter widgets belong to the previous resource set. Clear them
        # before selecting commands from the new set, which commits widgets.
        self.param_table.setRowCount(0)
        self._selected_material_row = -1
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
            self.material_command_table.setItem(row, 2, QTableWidgetItem(command.rendered))
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
        self.material_command_edit.setPlainText(command.rendered)
        self.selected_material_label.setText(f"Tag {command.tag} · {command.material_type} 参数说明")
        references = self.topology_editor.count_material_references(command.tag)
        self.selected_material_usage_label.setText(
            f"已分配给 {references} 个装配单元 · 可在此编辑，与对比页同步"
            if references
            else "尚未分配到装配单元 · 可在此编辑，与对比页同步"
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
        values = (parameter.name, self._parameter_description(parameter), self._parameter_unit(parameter), parameter.effect)
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
        editor.setReadOnly(False)
        editor.setToolTip("材料页与对比页同步修改；支持科学计数法")
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
                self.model_preview_plot.set_stale(True)
                self.model_preview_note.setText("待更新 · 当前整体曲线已失效")
            self._mark_comparison_dirty("材料参数已修改")
            rendered = parse_material_commands(MaterialTemplate(name="current", command="\n".join(self._material_lines),
                tag=self._material_template_tag, parameters=list(self._material_parameters.values())))
            self._material_commands = rendered
            for row, command in enumerate(rendered):
                self.material_command_table.item(row, 2).setText(command.rendered)
                if row == self._selected_material_row:
                    self.material_command_edit.setPlainText(command.rendered)
            for row in range(self.fit_table.rowCount()):
                if self._table_text(row, 1, self.fit_table) == name and self.fit_table.item(row, 5):
                    self.fit_table.blockSignals(True); self.fit_table.item(row, 5).setText(f"{new_value:g}"); self.fit_table.blockSignals(False)

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
        self._syncing_fit = True
        self.fit_table.blockSignals(True)
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
            low, high = sorted((parameter.value * 0.5, parameter.value * 2.0))
            if low == high: low, high = -1.0, 1.0
            if parameter.min is not None: low = max(low, parameter.min)
            if parameter.max is not None: high = min(high, parameter.max)
            checked, min_text, max_text, scale_value = existing.get(name, (False, f"{low:g}", f"{high:g}", "linear"))
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
            scale_combo.currentIndexChanged.connect(self._fit_table_edited)
            current_item = QTableWidgetItem(f"{parameter.value:g}")
            current_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.fit_table.setItem(row, 5, current_item)
        self.fit_table.blockSignals(False)
        self._syncing_fit = False
        self._validate_fit_inputs()

    def _populate_fitting(self, fitting: FitConfig | None) -> None:
        self._fit_options = {"loss": fitting.loss, "f_scale": fitting.f_scale, "seed": fitting.seed} if fitting else {}
        for row in range(self.fit_table.rowCount()):
            self.fit_table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        if fitting is None:
            return
        self._select_combo_data(self.fit_algorithm_combo, fitting.algorithm)
        self.fit_max_evaluations_spin.setValue(fitting.max_evaluations)
        self.fit_population_spin.setValue(fitting.population_size)
        self.fit_local_refine_check.setChecked(fitting.local_refine)
        self.fit_local_evaluations_spin.setValue(fitting.local_max_evaluations)
        self.fit_local_diff_step_spin.setValue(fitting.local_diff_step)
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
        if not self._loading_ui:
            self._select_combo_data(self.loading_source_combo, "experiment")
        self._update_source_labels()

    def _load_history_columns(self, path: Path) -> None:
        self._load_columns(path, self.history_column_combo)
        self._select_best_column(self.history_column_combo, ["disp", "deform", "u", "x", "0"])
        if not self._loading_ui:
            self._select_combo_data(self.loading_source_combo, "external")

    def _load_columns(self, path: Path, *combos: QComboBox) -> None:
        try:
            sheet = getattr(self, "_experiment_sheet", 0) if combos and combos[0] is self.displacement_combo else getattr(self, "_history_sheet", 0)
            columns = list_data_columns(path, sheet_name=sheet)
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
        loading = self._loading_protocol_from_ui()
        material = self._material_from_ui()
        if require_ready and not self._material_commands:
            raise ValueError("材料资源为空，请先从材料库添加至少一个材料")
        modeling = self.topology_editor.modeling(require_assigned=require_ready)
        self._sync_fit_table()
        fitting = self._fitting_from_ui(material)
        if fitting is not None and require_fitting:
            fitting.validate()
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
            experiment_sheet=getattr(self, "_experiment_sheet", 0),
            output_dir=Path(self.output_dir_edit.text()).expanduser().resolve(), material_template_path=self.current_template_path,
            area=float(self.area_spin.value()), length=float(self.length_spin.value()),
            fitting=fitting, modeling=modeling,
            force_unit=self.force_unit_combo.currentText(), length_unit=self.length_unit_combo.currentText(),
            material_mode=self.material_mode_combo.currentData(),
            experiment_displacement_scale=self.experiment_displacement_scale_spin.value(),
            experiment_force_scale=self.experiment_force_scale_spin.value(),
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
            local_diff_step=float(self.fit_local_diff_step_spin.value()),
            **getattr(self, "_fit_options", {}),
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
            count = len(modeling.assembly['elements']) if modeling.assembly is not None else len(modeling.root.material_tags()) if modeling.root else 1
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
        self._units_changed(mark=False)
        if hasattr(self, "model_preview_plot"):
            self.model_preview_plot.set_stale(True)
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
