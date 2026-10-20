from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Event
import traceback

import numpy as np
import pyqtgraph as pg
import yaml
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSlider, QSpinBox, QSplitter, QStyle, QTableWidget,
    QTableWidgetItem, QTabBar, QTabWidget, QToolBar, QToolButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget)

from material_test.cancellation import AnalysisCancelled
from material_test.components2d.model import ComponentProject, load_component, save_component
from material_test.components2d.solver import ComponentResult, run_component, write_component_tcl, load_component_result
from material_test.components2d.templates import make_plate_joint, make_translation, make_rotation
from material_test.components2d.fitting import fit_component, load_observations, evaluate_observations
from material_test.ui.component_canvas import ComponentCanvas
from material_test.ui.theme import STYLESHEET


COMPONENT_PAGES = [
    ("项目与单位", "二维组件"),
    ("试验与加载", "加载节点与历程"),
    ("材料", "本构资源与参数"),
    ("组件装配", "节点、单元与连接约束"),
    ("对比与标定", "响应通道与拟合"),
]


class ComponentWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(int, int, float)

    def __init__(self, project, fitting=False):
        super().__init__()
        self.project, self.fitting = project, fitting
        self.cancel_event = Event()

    @Slot()
    def run(self):
        try:
            result = fit_component(self.project, progress=self.progress.emit, cancel_check=self.cancel_event.is_set) if self.fitting else run_component(self.project, cancel_check=self.cancel_event.is_set)
        except AnalysisCancelled:
            self.cancelled.emit(); return
        except Exception:
            self.failed.emit(traceback.format_exc()); return
        self.finished.emit(result)


class ComponentWindow(QMainWindow):
    session_changed = Signal()
    status_changed = Signal(str, str)
    running_changed = Signal(bool)
    step_requested = Signal(int)

    def __init__(self, base_dir, solver, project_path=None, parent=None, *, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle("Material Test | 二维阻尼器组件")
        self.resize(1460, 920)
        if not embedded:
            self.setMinimumSize(960, 640)
        self.base_dir, self.solver_path = Path(base_dir), Path(solver)
        self.path = Path(project_path) if project_path else None
        self.project = load_component(self.path) if self.path else make_plate_joint(self.base_dir, self.solver_path, specimen=True)
        self.result = self.proposal = None
        self._thread = self._worker = None
        self._dirty = self._loading = self._closing = False
        self._material_selection = None
        self.state_text, self.state_kind = "等待计算当前组件", "waiting"
        self._build()
        self._populate()
        self._restore_result()
        self.setFont(QFont("Microsoft YaHei UI", 9))
        if not embedded:
            self.setStyleSheet(STYLESHEET)

    def _restore_result(self):
        saved = self.project.data.get('last_result')
        if not saved: return
        try:
            path = Path(saved)
            if not path.is_absolute(): path = self.project.base_dir/path
            dirty = self._dirty
            self._finished(load_component_result(self.project,path))
            self._dirty = dirty
        except Exception as exc:
            self.log.appendPlainText(f'已存结果不可用，请重算：{exc}')

    def _button(self, text, icon, callback):
        button = QPushButton(text)
        button.setIcon(self.style().standardIcon(icon)); button.clicked.connect(callback)
        return button

    def _tool(self, text, icon, callback):
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(text)
        button.setAccessibleName(text)
        button.setObjectName("canvasTool")
        button.clicked.connect(callback)
        return button

    @staticmethod
    def _section(text):
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _scroll(self, widget):
        scroll = QScrollArea()
        scroll.setObjectName('componentScroll')
        widget.setObjectName('componentForm')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _path_field(self, edit, *, solver=False):
        edit.textChanged.connect(edit.setToolTip)
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        def browse():
            if solver:
                path, _ = QFileDialog.getOpenFileName(self, "选择 OpenSees", edit.text(), "可执行文件 (*.exe);;所有文件 (*)")
            else:
                path = QFileDialog.getExistingDirectory(self, "结果目录", edit.text())
            if path:
                edit.setText(path)
        row.addWidget(self._tool("选择求解器" if solver else "选择结果目录", QStyle.StandardPixmap.SP_DialogOpenButton, browse))
        return field

    def _build(self):
        toolbar = QToolBar(); toolbar.setMovable(False); self.addToolBar(toolbar)
        self.toolbar = toolbar
        for text, icon, callback in (("打开", QStyle.StandardPixmap.SP_DialogOpenButton, self.open_project),
                                     ("保存", QStyle.StandardPixmap.SP_DialogSaveButton, self.save_project),
                                     ("导出 Tcl", QStyle.StandardPixmap.SP_ArrowDown, self.export_tcl)):
            action = QAction(self.style().standardIcon(icon), text, self); action.triggered.connect(callback); toolbar.addAction(action)
        toolbar.setVisible(not self.embedded)
        self.run_button = self._button("计算", QStyle.StandardPixmap.SP_MediaPlay, self.start_run)
        self.run_button.setObjectName("primaryButton")
        self.fit_button = self._button("拟合", QStyle.StandardPixmap.SP_BrowserReload, lambda: self.start_run(fitting=True))
        self.cancel_button = self._button("取消", QStyle.StandardPixmap.SP_MediaStop, self.cancel)
        self.cancel_button.setEnabled(False)
        root = QWidget(); root.setObjectName("componentWorkspace")
        layout = QVBoxLayout(root); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(10)
        self.workflow_nav = QTabBar()
        for title, _ in COMPONENT_PAGES: self.workflow_nav.addTab(title)
        self.workflow_nav.currentChanged.connect(self.set_step)
        self.workflow_nav.setVisible(not self.embedded); layout.addWidget(self.workflow_nav)
        self.split = QSplitter(); self.split.setChildrenCollapsible(False); self.split.setHandleWidth(10)
        self.workflow_pages = QStackedWidget(); self.workflow_pages.setMinimumWidth(300)
        self.split.addWidget(self.workflow_pages)
        project_page = QWidget(); project_layout = QVBoxLayout(project_page)
        project_layout.setContentsMargins(0, 0, 8, 0); project_layout.setSpacing(14)
        project_layout.addWidget(self._section("组件项目"))
        form = QFormLayout(); form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name_edit = QLineEdit(); self.output_edit = QLineEdit()
        self.solver_edit = QLineEdit()
        for edit in (self.name_edit, self.output_edit, self.solver_edit): edit.textChanged.connect(self._changed)
        self.force_unit = QComboBox(); self.force_unit.addItems(["N", "kN", "MN"])
        self.length_unit = QComboBox(); self.length_unit.addItems(["mm", "cm", "m"])
        for combo in (self.force_unit, self.length_unit): combo.currentTextChanged.connect(self._changed)
        form.addRow("组件名称", self.name_edit)
        form.addRow("OpenSees", self._path_field(self.solver_edit, solver=True))
        form.addRow("力单位", self.force_unit); form.addRow("长度单位", self.length_unit)
        form.addRow("结果目录", self._path_field(self.output_edit)); project_layout.addLayout(form)
        project_layout.addWidget(self._section("新建模型"))
        self.template = QComboBox(); self.template.addItems(["完整双板节点试件", "局部双板节点", "轴向阻尼器", "剪切阻尼器", "转动阻尼器", "黏滞阻尼器"])
        template_row = QHBoxLayout(); template_row.addWidget(self.template, 1)
        self.new_button = self._button("新建", QStyle.StandardPixmap.SP_FileDialogNewFolder, self.new_template)
        template_row.addWidget(self.new_button); project_layout.addLayout(template_row)
        self.summary_label = QLabel(); self.summary_label.setObjectName("componentSummary")
        self.summary_label.setWordWrap(True); project_layout.addWidget(self.summary_label); project_layout.addStretch(1)
        self.workflow_pages.addWidget(self._scroll(project_page))
        self._build_loading()
        self.nodes = self._table(["Tag", "名称", "X", "Y", "固定 UX", "固定 UY", "固定 RZ", "质量 X", "质量 Y", "转动惯量"])
        self.elements = self._table(["Tag", "名称", "类型", "节点 i", "节点 j", "材料 Tags", "方向", "A", "E", "Iz", "局部 X", "局部 Y", "坐标变换"])
        self.constraints = self._table(["类型", "保留节点", "从属节点", "自由度"])
        self.materials = self._table(["Tag", "名称", "响应维度", "命令模板"])
        self.parameters = self._table(["参数", "当前值", "下限", "上限"])
        self.materials.setColumnWidth(2, 160); self.materials.setColumnWidth(3, 260)
        self.materials.setMinimumHeight(168)
        material_page = QWidget(); ml = QVBoxLayout(material_page); ml.setContentsMargins(0, 0, 8, 0)
        ml.addWidget(self._section("材料资源")); ml.addWidget(self.materials, 1)
        row = QHBoxLayout()
        row.addWidget(self._button("材料库", QStyle.StandardPixmap.SP_DirOpenIcon, self.import_library_material))
        row.addWidget(self._tool("添加材料", QStyle.StandardPixmap.SP_FileDialogNewFolder, self.add_material))
        row.addWidget(self._tool("删除材料", QStyle.StandardPixmap.SP_TrashIcon, lambda: self._delete_row(self.materials)))
        row.addStretch(1); ml.addLayout(row)
        parameter_header = QHBoxLayout(); parameter_header.addWidget(self._section("当前材料参数"), 1)
        parameter_header.addWidget(self._tool("添加参数", QStyle.StandardPixmap.SP_FileDialogNewFolder, self.add_parameter))
        parameter_header.addWidget(self._tool("删除参数", QStyle.StandardPixmap.SP_TrashIcon, lambda: self._delete_row(self.parameters)))
        ml.addLayout(parameter_header); ml.addWidget(self.parameters, 2); self.workflow_pages.addWidget(material_page)
        self.materials.currentCellChanged.connect(self._select_material)
        assembly_page = QWidget(); assembly_layout = QVBoxLayout(assembly_page); assembly_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(); self.tabs.setDocumentMode(True)
        self._table_tab("节点", self.nodes, self.add_node)
        self._table_tab("单元", self.elements, self.add_element)
        self._table_tab("连接约束", self.constraints, self.add_constraint)
        self.ports_editor = QPlainTextEdit(); self.ports_editor.textChanged.connect(self._changed)
        self.tabs.addTab(self.ports_editor, '端口与分组')
        assembly_layout.addWidget(self.tabs); self.workflow_pages.addWidget(assembly_page)
        self.calibration_tabs = QTabWidget(); self.calibration_tabs.setDocumentMode(True)
        self.channels_editor = QPlainTextEdit(); self.channels_editor.textChanged.connect(self._changed)
        self.calibration_tabs.addTab(self.channels_editor, "响应通道")
        self.fitting_editor = QPlainTextEdit(); self.fitting_editor.textChanged.connect(self._changed)
        self.calibration_tabs.addTab(self.fitting_editor, "拟合设置")
        for editor in (self.channels_editor, self.fitting_editor, self.ports_editor): editor.setFont(QFont("Consolas", 10))
        self.workflow_pages.addWidget(self.calibration_tabs)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        view_controls = QHBoxLayout()
        view_controls.addWidget(self._section("二维模型"), 1)
        self.refresh_button = self._tool("校验并更新模型", QStyle.StandardPixmap.SP_BrowserReload, self.refresh_model)
        fit_view = self._tool("适配视图", QStyle.StandardPixmap.SP_TitleBarMaxButton, lambda: self.canvas.fit_all())
        view_controls.addWidget(self.refresh_button); view_controls.addWidget(fit_view)
        self.amplification = QDoubleSpinBox(); self.amplification.setRange(0, 1000); self.amplification.setValue(1)
        self.amplification.valueChanged.connect(self._show_frame)
        self.amplification.setFixedWidth(92)
        view_controls.addWidget(QLabel("变形倍率")); view_controls.addWidget(self.amplification)
        rl.addLayout(view_controls)
        self.preview_split = QSplitter(Qt.Orientation.Vertical); self.preview_split.setChildrenCollapsible(False); self.preview_split.setHandleWidth(8)
        model_panel = QWidget(); model_layout = QVBoxLayout(model_panel); model_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = ComponentCanvas(); self.canvas.selected.connect(self._canvas_selected); model_layout.addWidget(self.canvas, 1)
        play = QHBoxLayout()
        self.play_button = self._tool("播放 / 暂停变形", QStyle.StandardPixmap.SP_MediaPlay, self.toggle_play)
        self.frame = QSlider(Qt.Orientation.Horizontal); self.frame.valueChanged.connect(self._show_frame)
        self.frame_label = QLabel("未计算"); self.frame_label.setMinimumWidth(82)
        play.addWidget(self.play_button); play.addWidget(self.frame, 1); play.addWidget(self.frame_label)
        model_layout.addLayout(play); self.preview_split.addWidget(model_panel)
        self.chart_stack = QStackedWidget()
        self.history_plot = pg.PlotWidget(background="w"); self.history_plot.showGrid(x=True, y=True, alpha=.12)
        self.chart_stack.addWidget(self.history_plot)
        result_panel = QWidget(); result_layout = QVBoxLayout(result_panel); result_layout.setContentsMargins(0, 0, 0, 0)
        selectors = QHBoxLayout(); self.x_channel = QComboBox(); self.y_channel = QComboBox()
        self.x_channel.currentTextChanged.connect(self._plot); self.y_channel.currentTextChanged.connect(self._plot)
        selectors.addWidget(QLabel("X")); selectors.addWidget(self.x_channel); selectors.addWidget(QLabel("Y")); selectors.addWidget(self.y_channel)
        result_layout.addWidget(self._section("响应对比")); result_layout.addLayout(selectors)
        self.apply_button = self._button("应用拟合参数", QStyle.StandardPixmap.SP_DialogApplyButton, self.apply_proposal); self.apply_button.setEnabled(False)
        self.plot = pg.PlotWidget(background="w"); self.plot.setMinimumHeight(160)
        self.plot.showGrid(x=True, y=True, alpha=.12); result_layout.addWidget(self.plot, 1)
        self.chart_stack.addWidget(result_panel); self.preview_split.addWidget(self.chart_stack)
        self.preview_split.setSizes([420, 300]); rl.addWidget(self.preview_split, 1)
        self.split.addWidget(right); self.split.setSizes([560, 700]); layout.addWidget(self.split, 1)
        commands = QHBoxLayout()
        self.state_label = QLabel(self.state_text); self.state_label.setObjectName("runState"); self.state_label.setWordWrap(True)
        commands.addWidget(self.state_label)
        commands.addStretch(1)
        self.export_button = self._tool("导出 Tcl", QStyle.StandardPixmap.SP_ArrowDown, self.export_tcl)
        commands.addWidget(self.export_button)
        for button in (self.apply_button, self.fit_button, self.cancel_button, self.run_button): commands.addWidget(button)
        layout.addLayout(commands)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMinimumHeight(50); self.log.setMaximumHeight(80); layout.addWidget(self.log)
        self.log.setVisible(not self.embedded)
        self.setCentralWidget(root)
        self.timer = QTimer(self); self.timer.setInterval(50); self.timer.timeout.connect(self._advance_frame)
        self.statusBar().hide()
        self.set_step(0)

    def set_step(self, index):
        self.workflow_pages.setCurrentIndex(index)
        if self.workflow_nav.currentIndex() != index: self.workflow_nav.setCurrentIndex(index)
        self.chart_stack.setVisible(index in (1, 4))
        self.chart_stack.setCurrentIndex(0 if index == 1 else 1)
        self.fit_button.setVisible(index == 4); self.apply_button.setVisible(index == 4)
        self.export_button.setVisible(index in (3, 4))
        if index == 1: self._refresh_history()
        if index == 4: self._plot()

    def _set_status(self, text, state="waiting"):
        self.state_text, self.state_kind = text, state
        self.state_label.setText(text); self.state_label.setProperty("state", state)
        self.state_label.style().unpolish(self.state_label); self.state_label.style().polish(self.state_label)
        self.status_changed.emit(text, state)

    def _refresh_history(self):
        from material_test.components2d.model import ordered_history
        self.history_plot.clear()
        try:
            loading = yaml.safe_load(self.history_editor.toPlainText()) or {}
            loading["step"] = float(self.load_step.text())
            history = ordered_history(loading); dt = float(self.load_dt.text())
            dynamic = self.analysis.currentText() == "transient"
            x = np.arange(len(history)) * (dt if dynamic else 1.)
            self.history_plot.plot(x, history, pen=pg.mkPen("#167d70", width=2))
            self.history_plot.setLabel("bottom", "时间" if dynamic else "加载步", units="s" if dynamic else None)
            self.history_plot.setLabel("left", "转角" if self.load_dof.currentIndex() == 2 else "位移", units="rad" if self.load_dof.currentIndex() == 2 else self.length_unit.currentText())
            self.loading_summary.setText(f"{len(history)} 个加载点  /  最大值 {max(history):g}  /  最小值 {min(history):g}")
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            self.loading_summary.setText(f"加载待完善：{exc}")

    def _table(self, headers):
        table = QTableWidget(0, len(headers)); table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True); table.setWordWrap(False); table.setShowGrid(False)
        table.verticalHeader().setVisible(False); table.verticalHeader().setDefaultSectionSize(36)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setDefaultSectionSize(92)
        table.horizontalHeader().setStretchLastSection(True); table.setColumnWidth(0, 60)
        if len(headers) > 4: table.setColumnWidth(1, 130)
        table.itemChanged.connect(self._changed)
        return table

    def _table_tab(self, name, table, add):
        page = QWidget(); layout = QVBoxLayout(page); layout.addWidget(table)
        row = QHBoxLayout()
        row.addWidget(self._tool("添加" + name, QStyle.StandardPixmap.SP_FileDialogNewFolder, add))
        row.addWidget(self._tool("删除所选" + name, QStyle.StandardPixmap.SP_TrashIcon, lambda: self._delete_row(table)))
        row.addStretch(1); layout.addLayout(row); self.tabs.addTab(page, name)

    def _build_loading(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 8, 0)
        layout.addWidget(self._section("加载控制"))
        form = QFormLayout(); form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.analysis = QComboBox(); self.analysis.addItems(["static", "transient"])
        self.load_node = QSpinBox(); self.load_node.setRange(1, 9999999)
        self.load_dof = QComboBox(); self.load_dof.addItems(["UX", "UY", "RZ"])
        self.load_step = QLineEdit(); self.load_dt = QLineEdit()
        self.history_editor = QPlainTextEdit()
        self.history_editor.setMinimumHeight(150)
        for label, widget in (("分析类型", self.analysis), ("加载节点", self.load_node), ("加载自由度", self.load_dof),
                              ("转折点细分步长", self.load_step), ("采样间隔 [s]", self.load_dt), ("加载历程 / 转折点", self.history_editor)):
            form.addRow(label, widget)
            signal = getattr(widget, "textChanged", None) or getattr(widget, "currentTextChanged", None) or getattr(widget, "valueChanged", None)
            signal.connect(self._changed)
        layout.addLayout(form)
        self.loading_summary = QLabel(); self.loading_summary.setObjectName("previewNote"); self.loading_summary.setWordWrap(True)
        layout.addWidget(self.loading_summary); layout.addStretch(1)
        self.workflow_pages.addWidget(self._scroll(page))

    def _fill(self, table, rows, choices=None, checks=()):
        table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                if choices and c in choices:
                    combo = QComboBox(); combo.addItems(choices[c]); combo.setCurrentText(str(value))
                    combo.currentTextChanged.connect(self._changed); table.setCellWidget(r, c, combo)
                else:
                    item = QTableWidgetItem(str(value) if value is not None else "")
                    item.setToolTip(item.text())
                    if c in checks:
                        item.setText(""); item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
                    table.setItem(r, c, item)

    def _cell(self, table, row, col):
        widget = table.cellWidget(row, col)
        if isinstance(widget, QComboBox): return widget.currentText()
        item = table.item(row, col)
        if table is self.nodes and col in (4, 5, 6):
            return "1" if item.checkState() == Qt.CheckState.Checked else "0"
        return item.text() if item else ""

    def _populate(self):
        self._loading = True
        d = self.project.data
        self.name_edit.setText(d["name"]); self.output_edit.setText(str(self.project.output_root))
        self._fill(self.nodes, [[n["tag"], n.get("name", ""), n["x"], n["y"], *n.get("fix", [0,0,0]), *n.get("mass", [0,0,0])] for n in d["nodes"]], checks=(4,5,6))
        self._fill(self.elements, [[e["tag"], e.get("name", ""), e["type"], e["i"], e["j"],
                                   ",".join(str(v) for v in e.get("materials", [e["material"]] if "material" in e else [])),
                                   ",".join(str(v) for v in e.get("directions", [])), e.get("A", ""), e.get("E", ""), e.get("Iz", ""),
                                   *e.get("axis", [1,0]), e.get("transformation", "Linear")] for e in d["elements"]],
                   choices={2: ["zeroLength", "twoNodeLink", "truss", "corotTruss", "elasticBeamColumn"], 12: ["Linear", "PDelta", "Corotational"]})
        self._fill(self.constraints, [[c["type"], c["retained"], c["constrained"], ",".join(str(v) for v in c.get("dofs", []))] for c in d.get("constraints", [])], choices={0: ["rigidLink", "equalDOF"]})
        self._material_records = deepcopy(d["materials"]); self._material_selection = None
        self._fill(self.materials, [[m["tag"], m["name"], m["response"], m["command"]] for m in d["materials"]],
                   choices={2: ["force_deformation", "stress_strain", "moment_rotation"]})
        self.parameters.setRowCount(0)
        if self.materials.rowCount():
            self.materials.setCurrentCell(0, 0); self._select_material(0, 0, -1, -1)
        self.solver_edit.setText(str(self.project.solver.executable_path))
        self.force_unit.setCurrentText(d["units"]["force"]); self.length_unit.setCurrentText(d["units"]["length"])
        l = d["loading"]
        self.analysis.setCurrentText(l.get("analysis", "static")); self.load_node.setValue(l["node"])
        self.load_dof.setCurrentIndex(l["dof"]-1); self.load_step.setText(str(l.get("step", .1))); self.load_dt.setText(str(l.get("dt", 1.)))
        self.history_editor.setPlainText(yaml.safe_dump({k:l[k] for k in ("history", "path") if k in l}, sort_keys=False))
        self.channels_editor.setPlainText(yaml.safe_dump(d["channels"], sort_keys=False))
        self.fitting_editor.setPlainText(yaml.safe_dump(d.get("fitting", {"parameters": [], "observations": [], "max_evaluations": 300}), sort_keys=False))
        self.ports_editor.setPlainText(yaml.safe_dump({'ports':d.get('ports',{}),'groups':d.get('groups',[])},sort_keys=False))
        self._sync_channels()
        self._loading = False
        self.canvas.set_project(self.project)
        self.setWindowTitle(f"Material Test | {d['name']}")
        self.summary_label.setText(f"{len(d['nodes'])} 个节点   /   {len(d['elements'])} 个单元   /   {len(d['materials'])} 个材料\n{len(d.get('constraints', []))} 个连接约束")
        self._refresh_history()
        self._set_playback_enabled(self.result is not None)
        self._update_fit_availability()

    def _sync_channels(self):
        channels = [c['name'] for c in self.project.data['channels']]
        plot = self.project.data.get('plot', {})
        for selector, axis, default in ((self.x_channel, 'x', channels[0]), (self.y_channel, 'y', channels[-1])):
            name = plot.get(axis, default)
            selector.blockSignals(True)
            selector.clear(); selector.addItems(channels)
            selector.setCurrentText(name if name in channels else default)
            selector.blockSignals(False)

    def _store_parameters(self):
        row = self._material_selection
        if row is None or row < 0 or row >= len(self._material_records): return
        values = []
        for r in range(self.parameters.rowCount()):
            item = {"name": self._cell(self.parameters, r, 0), "value": float(self._cell(self.parameters, r, 1))}
            for c, key in ((2, "min"), (3, "max")):
                if self._cell(self.parameters, r, c): item[key] = float(self._cell(self.parameters, r, c))
            values.append(item)
        self._material_records[row]["parameters"] = values

    def _select_material(self, row, col, previous_row, previous_col):
        if row < 0 or row >= len(self._material_records): return
        try:
            if not self._loading: self._store_parameters()
        except ValueError as exc:
            self.log.appendPlainText(str(exc)); return
        loading = self._loading; self._loading = True
        self._material_selection = row
        self._fill(self.parameters, [[p["name"], p["value"], p.get("min", ""), p.get("max", "")] for p in self._material_records[row].get("parameters", [])])
        self._loading = loading

    def _collect(self):
        self._store_parameters()
        d = deepcopy(self.project.data)
        d["name"] = self.name_edit.text().strip(); d["output_dir"] = self.output_edit.text().strip()
        d["solver"]["executable_path"] = self.solver_edit.text().strip()
        d["units"] = {"force": self.force_unit.currentText(), "length": self.length_unit.currentText(), "time": "s"}
        old_nodes = {n['tag']: n for n in d['nodes']}
        d["nodes"] = []
        for r in range(self.nodes.rowCount()):
            v = [self._cell(self.nodes, r, c) for c in range(self.nodes.columnCount())]
            n = deepcopy(old_nodes.get(int(v[0]), {}))
            n.update(tag=int(v[0]), name=v[1], x=float(v[2]), y=float(v[3]), fix=list(map(int, v[4:7])))
            mass = list(map(float, v[7:10]))
            if any(mass) or 'mass' in n:
                n['mass'] = mass
            d['nodes'].append(n)
        old_elements = {e["tag"]:e for e in d["elements"]}; d["elements"] = []
        for r in range(self.elements.rowCount()):
            v = [self._cell(self.elements, r, c) for c in range(self.elements.columnCount())]
            e = deepcopy(old_elements.get(int(v[0]), {}))
            e.update(tag=int(v[0]), name=v[1], type=v[2], i=int(v[3]), j=int(v[4]))
            numbers = lambda s: [int(x.strip()) for x in s.split(",") if x.strip()]
            if e["type"] in {"zeroLength", "twoNodeLink"}:
                e.update(materials=numbers(v[5]), directions=numbers(v[6]), axis=[float(v[10]), float(v[11])])
            elif e["type"] in {"truss", "corotTruss"}:
                e.update(material=int(v[5]), A=float(v[7]))
            else:
                e.update(A=float(v[7]), E=float(v[8]), Iz=float(v[9]), transformation=v[12])
            d["elements"].append(e)
        d["constraints"] = []
        for r in range(self.constraints.rowCount()):
            v = [self._cell(self.constraints, r, c) for c in range(4)]
            c = {"type": v[0], "retained": int(v[1]), "constrained": int(v[2])}
            if v[0] == "equalDOF": c["dofs"] = [int(x.strip()) for x in v[3].split(",") if x.strip()]
            d["constraints"].append(c)
        d["materials"] = []
        for r in range(self.materials.rowCount()):
            m = deepcopy(self._material_records[r])
            m.update(tag=int(self._cell(self.materials, r, 0)), name=self._cell(self.materials, r, 1),
                     response=self._cell(self.materials, r, 2), command=self._cell(self.materials, r, 3))
            d["materials"].append(m)
        history = yaml.safe_load(self.history_editor.toPlainText())
        d["loading"] = {"node": self.load_node.value(), "dof": self.load_dof.currentIndex()+1, "analysis": self.analysis.currentText(),
                        "step": float(self.load_step.text()), "dt": float(self.load_dt.text()), **history}
        d["channels"] = yaml.safe_load(self.channels_editor.toPlainText())
        d["fitting"] = yaml.safe_load(self.fitting_editor.toPlainText())
        grouping = yaml.safe_load(self.ports_editor.toPlainText()) or {}
        d['ports'],d['groups'] = grouping.get('ports',{}),grouping.get('groups',[])
        d["plot"] = {"x":self.x_channel.currentText(), "y":self.y_channel.currentText()}
        return ComponentProject(d, self.project.base_dir).validate()

    def _changed(self, *args):
        if self._loading: return
        self._dirty = True; self.proposal = None; self.apply_button.setEnabled(False)
        self.timer.stop(); self._set_status("输入已修改；计算结果待更新", "stale")
        if self.result is not None:
            self.plot.clear(); self.plot.plot(self.result.channel(self.x_channel.currentText()), self.result.channel(self.y_channel.currentText()), pen=pg.mkPen("#999999", style=Qt.PenStyle.DashLine))
            self.result = None
            self.frame_label.setText("待计算")
        self._set_playback_enabled(False)
        if self.workflow_pages.currentIndex() == 1 or self.sender() in (
                self.history_editor, self.load_step, self.load_dt, self.load_dof, self.analysis):
            self._refresh_history()
        self._update_fit_availability()
        self.session_changed.emit()

    def _set_playback_enabled(self, enabled):
        self.play_button.setEnabled(enabled); self.frame.setEnabled(enabled)

    def _update_fit_availability(self):
        try:
            fitting = yaml.safe_load(self.fitting_editor.toPlainText()) or {}
            ready = bool(fitting.get('parameters') and fitting.get('observations'))
        except (AttributeError, yaml.YAMLError):
            ready = False
        self.fit_button.setEnabled(ready and self._thread is None)
        self.fit_button.setToolTip('拟合已配置的观测响应' if ready else '尚未配置拟合参数与观测数据')

    def _delete_row(self, table):
        row = table.currentRow()
        if row < 0: return
        if table is self.materials:
            self._store_parameters(); self._material_records.pop(row); self._material_selection = None
        if table is self.materials:
            loading = self._loading; self._loading = True; table.removeRow(row); self._loading = loading
            if table.rowCount():
                self._select_material(max(0,min(row,table.rowCount()-1)),0,-1,-1)
            else: self.parameters.setRowCount(0)
        else: table.removeRow(row)
        self._changed()

    def _add_record(self, table, values, choices=None, checks=()):
        rows = [[self._cell(table, r, c) for c in range(table.columnCount())] for r in range(table.rowCount())]
        if checks:
            for row in rows:
                for c in checks: row[c] = int(row[c])
        rows.append(values); self._fill(table, rows, choices, checks); self._changed()

    def add_node(self):
        tags = [int(self._cell(self.nodes, r, 0)) for r in range(self.nodes.rowCount())]
        self._add_record(self.nodes, [max(tags, default=0)+1, "", 0, 0, 0, 0, 0, 0, 0, 0], checks=(4,5,6))

    def add_element(self):
        tags = [int(self._cell(self.elements, r, 0)) for r in range(self.elements.rowCount())]
        self._add_record(self.elements, [max(tags, default=0)+1, "", "zeroLength", 101, 102, "11", "1", "", "", "", 1, 0, "Linear"],
                         choices={2:["zeroLength", "twoNodeLink", "truss", "corotTruss", "elasticBeamColumn"], 12:["Linear", "PDelta", "Corotational"]})

    def add_constraint(self):
        self._add_record(self.constraints, ["rigidLink", 101, 111, ""], choices={0:["rigidLink", "equalDOF"]})

    def add_material(self):
        self._store_parameters()
        key = max([int(self._cell(self.materials, r, 0)) for r in range(self.materials.rowCount())], default=0)+1
        self._material_records.append({"tag":key, "name":"Elastic", "response":"force_deformation", "command":"uniaxialMaterial Elastic {tag} {k}", "parameters":[{"name":"k", "value":100.}]})
        self._add_record(self.materials, [key,"Elastic","force_deformation","uniaxialMaterial Elastic {tag} {k}"], choices={2:["force_deformation","stress_strain","moment_rotation"]})
        self.materials.setCurrentCell(self.materials.rowCount()-1,0)

    def add_parameter(self):
        self._add_record(self.parameters, [f"p{self.parameters.rowCount()+1}", 1., "", ""])

    def import_library_material(self):
        from material_test.domain import SolverConfig
        from material_test.io.material_library import load_material_library
        from material_test.material_commands import parse_material_commands
        from material_test.ui.material_library_dialog import MaterialLibraryDialog
        try:
            project = self._collect()
            tags = {command.tag for material in project.materials.values() for command in parse_material_commands(material)}
            dialog = MaterialLibraryDialog(load_material_library(),default_tag=max(tags,default=0)+1,existing_tags=tags,
                                           solver=project.solver,parent=self)
            dialog.replace_button.hide()
            if dialog.exec() != dialog.DialogCode.Accepted or dialog.selected_entry is None: return
            response,accepted = QInputDialog.getItem(self,'本构响应维度','响应维度',['stress_strain','force_deformation','moment_rotation'],0,False)
            if not accepted: return
            template = dialog.selected_entry.instantiate(tag=dialog.selected_tag,references=dialog.selected_references)
            item = {**template.to_mapping(),'response':response,'rate_dependent':dialog.selected_entry.compatibility == 'requires_transient'}
            project.data['materials'].append(item)
            project.validate(); self.project = project; self._populate(); self._changed()
            self.materials.setCurrentCell(self.materials.rowCount()-1,0)
        except Exception as exc:
            QMessageBox.warning(self,'材料库',str(exc))

    def refresh_model(self):
        try:
            self.project = self._collect(); self._sync_channels(); self.canvas.set_project(self.project)
            self._set_status("模型校验通过")
        except Exception as exc:
            self.log.appendPlainText(str(exc)); QMessageBox.warning(self, "模型校验", str(exc))

    def _canvas_selected(self, kind, key):
        table = self.nodes if kind == "node" else self.elements
        self.set_step(3); self.step_requested.emit(3)
        self.tabs.setCurrentIndex(0 if kind == "node" else 1)
        for r in range(table.rowCount()):
            if self._cell(table,r,0) == str(key): table.selectRow(r); break

    def _confirm_discard(self):
        if not self._dirty: return True
        answer = QMessageBox.question(self,"未保存修改","保存当前组件项目？",QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Cancel: return False
        return self.save_project() if answer == QMessageBox.StandardButton.Save else True

    def open_project(self):
        if self._thread is not None: return
        if not self._confirm_discard(): return
        filename, _ = QFileDialog.getOpenFileName(self,"打开二维组件",str(self.base_dir),"YAML (*.yaml *.yml)")
        if not filename: return
        try:
            self.load_path(filename)
        except Exception as exc: QMessageBox.warning(self,"打开失败",str(exc))

    def load_path(self, filename):
        project = load_component(filename)
        self.timer.stop()
        self.project = project; self.path = Path(filename).resolve(); self.base_dir = project.base_dir
        self.solver_path = project.solver.executable_path
        self.result = self.proposal = None; self._populate(); self._dirty = False; self.plot.clear()
        self.apply_button.setEnabled(False); self.frame_label.setText("未计算")
        self._set_status("等待计算当前组件"); self._restore_result(); self.session_changed.emit()

    def save_project(self, *, save_as=False):
        if self._thread is not None: return False
        try:
            project = self._collect()
            path = self.path
            if save_as or path is None:
                default = self.base_dir / f"{project.data['name']}_model.yaml"
                filename, _ = QFileDialog.getSaveFileName(self,"另存为二维组件" if save_as else "保存二维组件",str(path or default),"YAML (*.yaml)")
                if not filename: return False
                path = Path(filename).resolve()
            save_component(project, path)
            self.project = load_component(path); self.path = path; self.base_dir = path.parent
            self._sync_channels()
            self._dirty = False; self._set_status(f"已保存：{path.name}")
            self.log.appendPlainText(f"已保存模型：{path}"); self.session_changed.emit(); return True
        except Exception as exc:
            QMessageBox.warning(self,"保存失败",str(exc)); return False

    def new_template(self):
        if self._thread is not None: return
        if not self._confirm_discard(): return
        index = self.template.currentIndex()
        factories = [lambda: make_plate_joint(self.base_dir,self.solver_path,specimen=True),
                     lambda: make_plate_joint(self.base_dir,self.solver_path),
                     lambda: make_translation(self.base_dir,self.solver_path),
                     lambda: make_translation(self.base_dir,self.solver_path,direction=2),
                     lambda: make_rotation(self.base_dir,self.solver_path),
                     lambda: make_translation(self.base_dir,self.solver_path,viscous=True)]
        self.solver_path = Path(self.solver_edit.text()).resolve()
        self.timer.stop(); self.project = factories[index](); self.path = None; self.result = self.proposal = None
        self._populate(); self._dirty = True; self.plot.clear(); self.apply_button.setEnabled(False)
        self.frame_label.setText("未计算"); self._set_status("新建组件，等待计算"); self.session_changed.emit()

    def export_tcl(self):
        from datetime import datetime
        try:
            project = self._collect()
            directory = project.output_root / datetime.now().strftime("export-%Y%m%d-%H%M%S-%f")
            filename, _, _ = write_component_tcl(project,directory)
            save_component(project,directory/"project.yaml"); self.log.appendPlainText(str(filename))
        except Exception as exc: QMessageBox.warning(self,"导出失败",str(exc))

    def start_run(self, checked=False, *, fitting=False):
        if self._thread is not None: return
        try: self.project = self._collect()
        except Exception as exc:
            QMessageBox.warning(self,"输入检查",str(exc)); return
        self._sync_channels(); self.canvas.set_project(self.project); self.timer.stop()
        self._worker = ComponentWorker(self.project.clone(),fitting)
        self._thread = QThread(self); self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._worker.cancelled.connect(self._cancelled, Qt.ConnectionType.QueuedConnection)
        for signal in (self._worker.finished,self._worker.failed,self._worker.cancelled):
            signal.connect(self._thread.quit); signal.connect(self._worker.deleteLater)
        self._worker.progress.connect(self._fit_progress, Qt.ConnectionType.QueuedConnection)
        self._thread.finished.connect(self._thread_finished, Qt.ConnectionType.QueuedConnection)
        self.workflow_pages.setEnabled(False); self.run_button.setEnabled(False); self.fit_button.setEnabled(False); self.cancel_button.setEnabled(True)
        self.workflow_nav.setEnabled(False); self.refresh_button.setEnabled(False); self.export_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.name_edit.setEnabled(False); self.output_edit.setEnabled(False); self.template.setEnabled(False)
        for action in self.toolbar.actions():
            if self.toolbar.widgetForAction(action) is None: action.setEnabled(False)
        self._set_status("参数拟合中" if fitting else "OpenSees 计算中", "running")
        self.running_changed.emit(True); self._thread.start()

    @Slot(int, int, float)
    def _fit_progress(self, count, total, best):
        self._set_status(f"拟合 {count}/{total} | 最佳目标 {best:.5g}", "running")

    @Slot(object)
    def _finished(self, result):
        self.proposal = None if isinstance(result,ComponentResult) else result
        self.result = result if isinstance(result,ComponentResult) else result.simulation
        if self.proposal is None:
            self.project.data['last_result'] = str(self.result.run_dir)
            self._dirty = True
        self.apply_button.setEnabled(self.proposal is not None and self._thread is None)
        self.frame.setRange(0,len(self.result.values)-1); self.frame.setValue(0)
        self._plot(); self._show_frame()
        self._set_playback_enabled(True)
        self.log.appendPlainText(str(self.result.run_dir)); self._set_status("完整加载计算完成" if self.proposal is None else "拟合完成，参数待审核应用", "complete")
        if self.proposal is not None:
            self.log.appendPlainText(f'图像/时程目标 = {self.proposal.objective:.6g}，评价次数 = {self.proposal.evaluations}')
        self.session_changed.emit()

    @Slot(str)
    def _failed(self, message):
        self.log.appendPlainText(message); self._set_status("计算失败；详见日志", "failed")

    @Slot()
    def _cancelled(self):
        self.log.appendPlainText("任务已取消，参数未应用"); self._set_status("任务已取消")

    @Slot()
    def _thread_finished(self):
        thread = self._thread
        thread.wait()
        self._thread = self._worker = None
        thread.deleteLater()
        self.workflow_pages.setEnabled(True); self.run_button.setEnabled(True); self.fit_button.setEnabled(True); self.cancel_button.setEnabled(False)
        self.workflow_nav.setEnabled(True); self.refresh_button.setEnabled(True); self.export_button.setEnabled(True)
        self.apply_button.setEnabled(self.proposal is not None)
        self._update_fit_availability()
        self.name_edit.setEnabled(True); self.output_edit.setEnabled(True); self.template.setEnabled(True)
        for action in self.toolbar.actions(): action.setEnabled(True)
        self.running_changed.emit(False)
        if self._closing: self.close()

    def cancel(self):
        if self._worker:
            self._worker.cancel_event.set(); self._set_status("正在取消，请稍候", "running")

    def apply_proposal(self):
        if self.proposal is None: return
        self.project = self.proposal.project.clone(); self.project.data['last_result'] = str(self.result.run_dir); self._populate(); self._dirty = True
        self.proposal = None; self.apply_button.setEnabled(False); self._plot(); self._show_frame()
        self._set_status("已应用拟合参数", "complete"); self.session_changed.emit()

    def _plot(self, *args):
        if self._loading or self.result is None: return
        x,y = self.x_channel.currentText(),self.y_channel.currentText()
        if x not in self.result.columns or y not in self.result.columns: return
        self.plot.clear()
        try:
            for source, observed in load_observations(self.project):
                if source["x"] != x or source["y"] != y: continue
                if source["kind"] == "image":
                    self.plot.plot(observed.points[:,0]*source.get('x_scale',1.),observed.points[:,1]*source.get('y_scale',1.),pen=None,symbol="o",symbolSize=2,symbolPen=None,symbolBrush="#333333")
                else: self.plot.plot(observed[:,0]*source.get('x_scale',1.),observed[:,1]*source.get('y_scale',1.),pen=pg.mkPen("#333333",width=1.))
        except (ValueError,OSError,KeyError): pass
        self.plot.plot(self.result.channel(x),self.result.channel(y),pen=pg.mkPen("#b34857",width=1.8))
        channels = {c['name']:c for c in self.project.data['channels']}
        self.plot.setLabel("bottom",x,units=channels.get(x,{}).get('unit'))
        self.plot.setLabel("left",y,units=channels.get(y,{}).get('unit')); self.plot.enableAutoRange()

    def _show_frame(self, *args):
        if self.result is None: return
        frame = self.frame.value()
        self.frame_label.setText(f"{frame+1} / {len(self.result.values)}")
        display_project = self.proposal.project if self.proposal is not None else self.project
        self.canvas.set_project(display_project,result=self.result,frame=frame,amplification=self.amplification.value(),refit=False)

    def toggle_play(self):
        if self.result is None: return
        if self.timer.isActive(): self.timer.stop()
        else: self.timer.start()

    def _advance_frame(self):
        self.frame.setValue((self.frame.value()+1)%max(1,self.frame.maximum()+1))

    def closeEvent(self,event):
        if self._thread is not None:
            self._closing = True; self.cancel(); event.ignore(); return
        if not self._confirm_discard(): event.ignore(); return
        self.timer.stop(); event.accept()
