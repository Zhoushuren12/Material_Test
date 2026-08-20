from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from material_test.domain import (
    FitConfig,
    FitParameterSpec,
    LoadingProtocol,
    MaterialTemplate,
    ModelingConfig,
    ParameterSpec,
    ProjectConfig,
    SolverConfig,
)
from material_test.fitting import FitResult
from material_test.io.experiment_data import list_data_columns
from material_test.io.material_templates import load_material_template
from material_test.io.project_config import load_project_config, save_project_config
from material_test.ui.plot_canvas import OverlayPlotWidget
from material_test.ui.workers import FitWorker, RunWorker
from material_test.workflow import WorkflowResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / "examples" / "materials" / "twostage.yaml"
DEFAULT_PROJECT = ROOT / "examples" / "projects" / "twostage_demo.yaml"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Material Test")
        self.resize(1280, 820)

        self.current_template_path: Path | None = None
        self.current_result: WorkflowResult | FitResult | None = None
        self._thread: QThread | None = None
        self._worker: RunWorker | FitWorker | None = None

        self._build_actions()
        self._build_ui()
        self._load_initial_state()

    def _build_actions(self) -> None:
        self.load_project_action = QAction("Load Project", self)
        self.load_project_action.triggered.connect(self.load_project)
        self.save_project_action = QAction("Save Project", self)
        self.save_project_action.triggered.connect(self.save_project)

        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.load_project_action)
        file_menu.addAction(self.save_project_action)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 8, 8, 8)
        self.plot_widget = OverlayPlotWidget()
        right_layout.addWidget(self.plot_widget, stretch=4)
        right_layout.addWidget(self._build_log_panel(), stretch=1)
        splitter.addWidget(right)
        splitter.setSizes([430, 850])

        self.setCentralWidget(splitter)

    def _build_left_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(390)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        layout.addWidget(self._build_project_section())
        layout.addWidget(self._build_solver_section())
        layout.addWidget(self._build_material_section())
        layout.addWidget(self._build_protocol_section())
        layout.addWidget(self._build_experiment_section())
        layout.addWidget(self._build_fitting_section())

        self.run_button = QPushButton("Run")
        self.run_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_button.clicked.connect(self.start_run)
        self.run_button.setMinimumHeight(34)

        self.fit_button = QPushButton("Fit")
        self.fit_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.fit_button.clicked.connect(self.start_fit)
        self.fit_button.setMinimumHeight(34)

        self.export_button = QPushButton("Export Figure")
        self.export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_button.clicked.connect(self.export_figure)
        self.export_button.setEnabled(False)

        action_row = QHBoxLayout()
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.fit_button)
        action_row.addWidget(self.export_button)
        layout.addLayout(action_row)
        layout.addStretch(1)

        scroll.setWidget(panel)
        return scroll

    def _build_log_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("Run Log"))
        header.addStretch(1)
        clear_button = QToolButton()
        clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        clear_button.setToolTip("Clear run log")
        clear_button.clicked.connect(self.clear_log)
        header.addWidget(clear_button)

        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(2000)
        self.log_edit.setPlaceholderText("Run log")
        self.log_edit.setMinimumHeight(150)
        layout.addLayout(header)
        layout.addWidget(self.log_edit)
        return panel

    def _build_project_section(self) -> QGroupBox:
        group = QGroupBox("Project")
        layout = QHBoxLayout(group)

        load_button = QPushButton("Load")
        load_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        load_button.clicked.connect(self.load_project)

        save_button = QPushButton("Save")
        save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        save_button.clicked.connect(self.save_project)

        layout.addWidget(load_button)
        layout.addWidget(save_button)
        return group

    def _build_solver_section(self) -> QGroupBox:
        group = QGroupBox("Solver")
        form = QFormLayout(group)
        self.solver_path_edit = QLineEdit()
        self.tcl_library_edit = QLineEdit()
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 3600.0)
        self.timeout_spin.setValue(120.0)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setDecimals(1)

        form.addRow("OpenSees", self._path_row(self.solver_path_edit, self.browse_solver, file_mode=True))
        form.addRow("Tcl library", self._path_row(self.tcl_library_edit, self.browse_tcl_library, file_mode=False))
        form.addRow("Timeout", self.timeout_spin)
        return group

    def _build_material_section(self) -> QGroupBox:
        group = QGroupBox("Material")
        layout = QVBoxLayout(group)

        self.template_path_edit = QLineEdit()
        layout.addWidget(QLabel("Template"))
        layout.addLayout(self._path_row(self.template_path_edit, self.browse_material_template, file_mode=True))

        form = QFormLayout()
        self.material_name_edit = QLineEdit()
        self.material_tag_spin = QSpinBox()
        self.material_tag_spin.setRange(1, 999999)
        self.material_command_edit = QPlainTextEdit()
        self.material_command_edit.setMaximumHeight(88)
        self.model_topology_combo = QComboBox()
        self.model_topology_combo.addItem("Single material", "single")
        self.model_topology_combo.addItem("Explicit series", "series")
        self.model_topology_combo.addItem("Explicit parallel", "parallel")
        self.model_topology_combo.setToolTip(
            "Explicit modes connect Truss elements directly and do not use "
            "OpenSees uniaxialMaterial Series or Parallel."
        )
        self.model_topology_combo.currentIndexChanged.connect(self._update_modeling_controls)
        self.model_tags_edit = QLineEdit()
        self.model_tags_edit.setPlaceholderText("e.g. 101, 102")
        self.model_tags_edit.setToolTip("Material tags defined by the commands above, in connection order.")
        form.addRow("Name", self.material_name_edit)
        form.addRow("Tag", self.material_tag_spin)
        form.addRow("Commands", self.material_command_edit)
        form.addRow("Test model", self.model_topology_combo)
        form.addRow("Material tags", self.model_tags_edit)
        layout.addLayout(form)
        self._update_modeling_controls()

        self.param_table = QTableWidget(0, 6)
        self.param_table.setHorizontalHeaderLabels(["Name", "Value", "Min", "Max", "Fit", "Scale"])
        self.param_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.setMinimumHeight(180)
        layout.addWidget(self.param_table)

        param_buttons = QHBoxLayout()
        add_param = QPushButton("Add")
        add_param.clicked.connect(lambda: self._append_parameter_row(ParameterSpec("param", 0.0)))
        remove_param = QPushButton("Remove")
        remove_param.clicked.connect(self.remove_selected_parameter)
        param_buttons.addWidget(add_param)
        param_buttons.addWidget(remove_param)
        layout.addLayout(param_buttons)
        return group

    def _build_protocol_section(self) -> QGroupBox:
        group = QGroupBox("Cyclic Loading")
        form = QFormLayout(group)
        self.amplitudes_edit = QPlainTextEdit()
        self.amplitudes_edit.setMaximumHeight(92)
        self.du_spin = QDoubleSpinBox()
        self.du_spin.setDecimals(6)
        self.du_spin.setRange(1.0e-9, 1.0e9)
        self.du_spin.setValue(0.25)
        self.history_path_edit = QLineEdit()
        self.history_column_combo = QComboBox()
        self.append_zero_check = QCheckBox("Append zero")
        form.addRow("Amplitudes", self.amplitudes_edit)
        form.addRow("du", self.du_spin)
        form.addRow("History file", self._path_row(self.history_path_edit, self.browse_loading_history, file_mode=True))
        form.addRow("History column", self.history_column_combo)
        form.addRow("", self.append_zero_check)
        return group

    def _build_experiment_section(self) -> QGroupBox:
        group = QGroupBox("Experiment Data")
        form = QFormLayout(group)
        self.experiment_path_edit = QLineEdit()
        self.displacement_combo = QComboBox()
        self.force_combo = QComboBox()
        form.addRow("Data file", self._path_row(self.experiment_path_edit, self.browse_experiment_data, file_mode=True))
        form.addRow("Displacement", self.displacement_combo)
        form.addRow("Force", self.force_combo)
        return group

    def _build_fitting_section(self) -> QGroupBox:
        group = QGroupBox("Parameter Fitting")
        form = QFormLayout(group)
        self.fit_algorithm_combo = QComboBox()
        self.fit_algorithm_combo.addItem("Differential evolution", "de")
        self.fit_algorithm_combo.addItem("Particle swarm", "pso")
        self.fit_max_evaluations_spin = QSpinBox()
        self.fit_max_evaluations_spin.setRange(8, 100000)
        self.fit_max_evaluations_spin.setValue(240)
        self.fit_population_spin = QSpinBox()
        self.fit_population_spin.setRange(2, 1000)
        self.fit_population_spin.setValue(16)
        self.fit_local_refine_check = QCheckBox("Local least-squares refinement")
        self.fit_local_refine_check.setChecked(True)
        self.fit_local_evaluations_spin = QSpinBox()
        self.fit_local_evaluations_spin.setRange(1, 100000)
        self.fit_local_evaluations_spin.setValue(120)

        form.addRow("Global algorithm", self.fit_algorithm_combo)
        form.addRow("Global evaluations", self.fit_max_evaluations_spin)
        form.addRow("Population", self.fit_population_spin)
        form.addRow("", self.fit_local_refine_check)
        form.addRow("Local evaluations", self.fit_local_evaluations_spin)
        return group

    def _path_row(self, edit: QLineEdit, callback, *, file_mode: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button = QToolButton()
        button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        button.setToolTip("Browse file" if file_mode else "Browse folder")
        button.clicked.connect(callback)
        row.addWidget(edit)
        row.addWidget(button)
        return row

    def _load_initial_state(self) -> None:
        if DEFAULT_PROJECT.exists():
            try:
                self._populate_from_config(load_project_config(DEFAULT_PROJECT))
                self._append_log(f"Loaded default project: {DEFAULT_PROJECT}")
                return
            except Exception as exc:
                self._append_log(f"Could not load default project: {exc}")
        if DEFAULT_TEMPLATE.exists():
            self._load_template(DEFAULT_TEMPLATE)
        self.solver_path_edit.setText(str(ROOT / "OpenSees.exe"))
        self.tcl_library_edit.setText(str(ROOT / "lib" / "tcl8.6"))

    def browse_solver(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select OpenSees executable", str(ROOT), "Executable (*.exe);;All files (*)")
        if path:
            self.solver_path_edit.setText(path)

    def browse_tcl_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Tcl library folder", str(ROOT))
        if path:
            self.tcl_library_edit.setText(path)

    def browse_material_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select material template", str(ROOT), "YAML (*.yaml *.yml);;All files (*)")
        if path:
            self._load_template(Path(path))

    def browse_experiment_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select experiment data",
            str(ROOT),
            "Data files (*.csv *.txt *.xlsx *.xls);;All files (*)",
        )
        if path:
            self.experiment_path_edit.setText(path)
            self._load_experiment_columns(Path(path))

    def browse_loading_history(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select loading history",
            str(ROOT),
            "Data files (*.csv *.txt *.xlsx *.xls);;All files (*)",
        )
        if path:
            self.history_path_edit.setText(path)
            self._load_history_columns(Path(path))

    def load_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load project", str(ROOT), "YAML (*.yaml *.yml);;All files (*)")
        if not path:
            return
        try:
            config = load_project_config(path)
            self._populate_from_config(config)
        except Exception as exc:
            QMessageBox.critical(self, "Load Project", str(exc))
            return
        self._append_log(f"Loaded project: {path}")

    def save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save project", str(ROOT / "examples" / "projects"), "YAML (*.yaml *.yml)")
        if not path:
            return
        try:
            config = self._config_from_ui()
            save_project_config(config, path)
        except Exception as exc:
            QMessageBox.critical(self, "Save Project", str(exc))
            return
        self._append_log(f"Saved project: {path}")

    def start_run(self) -> None:
        try:
            config = self._config_from_ui()
        except Exception as exc:
            QMessageBox.critical(self, "Run", str(exc))
            return

        self.run_button.setEnabled(False)
        self.fit_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.statusBar().showMessage("Running OpenSees...")
        self._append_log("Starting simulation...")

        self._thread = QThread(self)
        self._worker = RunWorker(config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._run_finished)
        self._worker.failed.connect(self._run_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def start_fit(self) -> None:
        try:
            config = self._config_from_ui(require_fitting=True)
        except Exception as exc:
            QMessageBox.critical(self, "Fit", str(exc))
            return

        self.run_button.setEnabled(False)
        self.fit_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.statusBar().showMessage("Fitting material parameters...")
        self._append_log("Starting parameter fit...")

        self._thread = QThread(self)
        self._worker = FitWorker(config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._fit_finished)
        self._worker.failed.connect(self._run_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def export_figure(self) -> None:
        if self.current_result is None:
            QMessageBox.information(self, "Export Figure", "No run result is available.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export figure", str(ROOT / "outputs" / "overlay.png"), "PNG (*.png)")
        if not path:
            return
        self.plot_widget.save_png(path)
        self._append_log(f"Exported figure: {path}")

    def clear_log(self) -> None:
        self.log_edit.clear()
        self.statusBar().showMessage("Run log cleared")

    def remove_selected_parameter(self) -> None:
        rows = sorted({index.row() for index in self.param_table.selectedIndexes()}, reverse=True)
        if not rows and self.param_table.rowCount():
            rows = [self.param_table.rowCount() - 1]
        for row in rows:
            self.param_table.removeRow(row)

    def _run_finished(self, result: WorkflowResult) -> None:
        self.current_result = result
        self.plot_widget.update_curves(result.simulation, result.experiment)
        self.export_button.setEnabled(True)
        self._append_log(f"Completed run: {result.run_dir}")
        self._append_log(f"Overlay figure: {result.figure_file}")
        if result.simulation.stdout.strip():
            self._append_log(result.simulation.stdout.strip())
        if result.simulation.stderr.strip():
            self._append_log(result.simulation.stderr.strip())
        self.statusBar().showMessage("Run completed")

    def _fit_finished(self, result: FitResult) -> None:
        self.current_result = result
        self.plot_widget.update_curves(result.simulation, result.experiment)
        self.export_button.setEnabled(True)
        self._append_log(f"Completed fit: {result.run_dir}")
        self._append_log(
            f"Algorithm: {result.global_algorithm}; evaluations: {result.global_evaluations} global, "
            f"{result.local_evaluations} local"
        )
        self._append_log(f"Normalized RMSE: {result.metrics.normalized_rmse:.6g}")
        self._append_log(f"Fitted material: {result.material_file}")
        self.statusBar().showMessage("Fit completed")

    def _run_failed(self, message: str) -> None:
        self._append_log(message)
        QMessageBox.critical(self, "Run Failed", message)
        self.statusBar().showMessage("Run failed")

    def _thread_finished(self) -> None:
        self.run_button.setEnabled(True)
        self.fit_button.setEnabled(True)
        self._thread = None
        self._worker = None

    def _load_template(self, path: Path) -> None:
        template = load_material_template(path)
        self.current_template_path = path.resolve()
        self.template_path_edit.setText(str(path))
        self._populate_material(template)
        self._append_log(f"Loaded material template: {path}")

    def _populate_from_config(self, config: ProjectConfig) -> None:
        self.solver_path_edit.setText(str(config.solver.executable_path))
        self.tcl_library_edit.setText(str(config.solver.tcl_library_path or ""))
        self.timeout_spin.setValue(float(config.solver.timeout))

        self.current_template_path = config.material_template_path
        self.template_path_edit.setText(str(config.material_template_path or ""))
        self._populate_material(config.material)
        self._populate_modeling(config.modeling)
        self._populate_fitting(config.fitting)

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

    def _populate_material(self, template: MaterialTemplate) -> None:
        self.material_name_edit.setText(template.name)
        self.material_tag_spin.setValue(template.tag)
        self.material_command_edit.setPlainText(template.command)
        self.param_table.setRowCount(0)
        for parameter in template.parameters:
            self._append_parameter_row(parameter)

    def _populate_modeling(self, modeling: ModelingConfig) -> None:
        self._select_combo_data(self.model_topology_combo, modeling.topology)
        self.model_tags_edit.setText(", ".join(str(tag) for tag in modeling.material_tags))
        self._update_modeling_controls()

    def _append_parameter_row(self, parameter: ParameterSpec, *, selected_for_fit: bool = False, scale: str = "linear") -> None:
        row = self.param_table.rowCount()
        self.param_table.insertRow(row)
        self.param_table.setItem(row, 0, QTableWidgetItem(parameter.name))
        self.param_table.setItem(row, 1, QTableWidgetItem(f"{parameter.value:g}"))
        self.param_table.setItem(row, 2, QTableWidgetItem("" if parameter.min is None else f"{parameter.min:g}"))
        self.param_table.setItem(row, 3, QTableWidgetItem("" if parameter.max is None else f"{parameter.max:g}"))
        selected = QTableWidgetItem()
        selected.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        selected.setCheckState(Qt.CheckState.Checked if selected_for_fit else Qt.CheckState.Unchecked)
        self.param_table.setItem(row, 4, selected)
        scale_combo = QComboBox()
        scale_combo.addItem("Linear", "linear")
        scale_combo.addItem("Log", "log")
        self._select_combo_data(scale_combo, scale)
        self.param_table.setCellWidget(row, 5, scale_combo)

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
            QMessageBox.critical(self, "Data Columns", str(exc))
            return
        for combo in combos:
            combo.clear()
            combo.addItems(columns)

    def _config_from_ui(self, *, require_fitting: bool = False) -> ProjectConfig:
        solver_text = self.solver_path_edit.text().strip()
        if not solver_text:
            raise ValueError("OpenSees executable path is required")
        solver_path = Path(solver_text)
        tcl_text = self.tcl_library_edit.text().strip()

        experiment_path_text = self.experiment_path_edit.text().strip()
        experiment_path = Path(experiment_path_text) if experiment_path_text else None
        displacement_column = self.displacement_combo.currentText() if experiment_path else None
        force_column = self.force_combo.currentText() if experiment_path else None

        history_text = self.history_path_edit.text().strip()
        if history_text:
            loading = LoadingProtocol(
                history_path=Path(history_text),
                history_column=self.history_column_combo.currentText() or "0",
                append_zero=self.append_zero_check.isChecked(),
            )
        else:
            loading = LoadingProtocol(amplitudes=self._parse_amplitudes(), du=float(self.du_spin.value()))

        material = self._material_from_ui()
        modeling = self._modeling_from_ui()
        fitting = self._fitting_from_ui(material)
        if require_fitting and fitting is None:
            raise ValueError("select at least one material parameter for fitting")

        return ProjectConfig(
            solver=SolverConfig(
                executable_path=solver_path,
                tcl_library_path=Path(tcl_text) if tcl_text else None,
                timeout=float(self.timeout_spin.value()),
            ),
            material=material,
            loading=loading,
            experiment_path=experiment_path,
            displacement_column=displacement_column,
            force_column=force_column,
            output_dir=ROOT / "outputs" / "runs",
            material_template_path=self.current_template_path,
            fitting=fitting,
            modeling=modeling,
        )

    def _material_from_ui(self) -> MaterialTemplate:
        parameters: list[ParameterSpec] = []
        seen: set[str] = set()
        for row in range(self.param_table.rowCount()):
            name = self._table_text(row, 0)
            if not name:
                continue
            if name in seen:
                raise ValueError(f"duplicate parameter name: {name}")
            seen.add(name)
            parameters.append(
                ParameterSpec(
                    name=name,
                    value=float(self._table_text(row, 1) or "0"),
                    min=self._optional_float(self._table_text(row, 2)),
                    max=self._optional_float(self._table_text(row, 3)),
                )
            )
        return MaterialTemplate(
            name=self.material_name_edit.text().strip() or "Material",
            command=self.material_command_edit.toPlainText().strip(),
            tag=int(self.material_tag_spin.value()),
            parameters=parameters,
        )

    def _modeling_from_ui(self) -> ModelingConfig:
        topology = str(self.model_topology_combo.currentData())
        tags: list[int] = []
        if topology != "single":
            values = [item for item in re.split(r"[\s,;]+", self.model_tags_edit.text().strip()) if item]
            try:
                tags = [int(value) for value in values]
            except ValueError as exc:
                raise ValueError("explicit model material tags must be integers") from exc
        modeling = ModelingConfig(topology=topology, material_tags=tags)
        modeling.validate()
        return modeling

    def _update_modeling_controls(self) -> None:
        explicit = self.model_topology_combo.currentData() != "single"
        self.model_tags_edit.setEnabled(explicit)

    def _fitting_from_ui(self, material: MaterialTemplate) -> FitConfig | None:
        selected: list[FitParameterSpec] = []
        for row, parameter in enumerate(material.parameters):
            item = self.param_table.item(row, 4)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            if parameter.min is None or parameter.max is None:
                raise ValueError(f"fitted parameter {parameter.name!r} requires finite Min and Max values")
            scale_combo = self.param_table.cellWidget(row, 5)
            scale = scale_combo.currentData() if isinstance(scale_combo, QComboBox) else "linear"
            selected.append(FitParameterSpec(parameter.name, parameter.min, parameter.max, str(scale)))
        if not selected:
            return None
        return FitConfig(
            parameters=selected,
            algorithm=str(self.fit_algorithm_combo.currentData()),
            max_evaluations=int(self.fit_max_evaluations_spin.value()),
            population_size=int(self.fit_population_spin.value()),
            local_refine=self.fit_local_refine_check.isChecked(),
            local_max_evaluations=int(self.fit_local_evaluations_spin.value()),
        )

    def _populate_fitting(self, fitting: FitConfig | None) -> None:
        for row in range(self.param_table.rowCount()):
            item = self.param_table.item(row, 4)
            if item is not None:
                item.setCheckState(Qt.CheckState.Unchecked)
        if fitting is None:
            return
        self._select_combo_data(self.fit_algorithm_combo, fitting.algorithm)
        self.fit_max_evaluations_spin.setValue(fitting.max_evaluations)
        self.fit_population_spin.setValue(fitting.population_size)
        self.fit_local_refine_check.setChecked(fitting.local_refine)
        self.fit_local_evaluations_spin.setValue(fitting.local_max_evaluations)
        selected = {parameter.name: parameter for parameter in fitting.parameters}
        for row in range(self.param_table.rowCount()):
            name = self._table_text(row, 0)
            spec = selected.get(name)
            if spec is None:
                continue
            item = self.param_table.item(row, 4)
            if item is not None:
                item.setCheckState(Qt.CheckState.Checked)
            min_item = self.param_table.item(row, 2)
            max_item = self.param_table.item(row, 3)
            if min_item is not None:
                min_item.setText(f"{spec.min:g}")
            if max_item is not None:
                max_item.setText(f"{spec.max:g}")
            scale_combo = self.param_table.cellWidget(row, 5)
            if isinstance(scale_combo, QComboBox):
                self._select_combo_data(scale_combo, spec.scale)

    def _parse_amplitudes(self) -> list[float]:
        text = self.amplitudes_edit.toPlainText().strip()
        values = [item for item in re.split(r"[\s,;]+", text) if item]
        if len(values) < 2:
            raise ValueError("at least two amplitude values are required")
        return [float(value) for value in values]

    def _table_text(self, row: int, column: int) -> str:
        item = self.param_table.item(row, column)
        return item.text().strip() if item else ""

    def _append_log(self, message: str) -> None:
        self.log_edit.appendPlainText(message)

    @staticmethod
    def _optional_float(value: str) -> float | None:
        return float(value) if value else None

    @staticmethod
    def _select_combo_text(combo: QComboBox, value: str | int | None) -> None:
        if value is None:
            return
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
