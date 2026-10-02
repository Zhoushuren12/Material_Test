from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool
from threading import Event
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from material_test.domain import SolverConfig
from material_test.io.material_library import MaterialLibrary, MaterialLibraryEntry
from material_test.material_preview import (
    MaterialPreviewCurve, calculate_material_preview, preview_error_summary,
)
from material_test.ui.plot_canvas import HysteresisPreviewWidget, SparklineWidget
from material_test.ui.preview_jobs import PREVIEW_CACHE, PreviewJob


COMPATIBILITY_LABELS = {
    "official": "OpenSees 官方",
    "current_solver_verified": "当前求解器已验证",
    "requires_transient": "需瞬态 / 速率相关协议",
}


class MaterialLibraryDialog(QDialog):
    def __init__(
        self,
        library: MaterialLibrary,
        *,
        default_tag: int,
        existing_tags: set[int],
        material_names: dict[int, str] | None = None,
        solver: SolverConfig | None = None,
        area: float = 1.0,
        length: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.library = library
        self.existing_tags = set(existing_tags)
        self.material_names = material_names or {}
        self.selected_entry: MaterialLibraryEntry | None = None
        self.selected_action: str | None = None
        self.preview_curves: dict[str, MaterialPreviewCurve] = {}
        self._preview_jobs = {}
        self._preview_cancel = Event()
        self.finished.connect(lambda *_: self._preview_cancel.set())
        self.solver = solver
        self.area = float(area)
        self.length = float(length)
        self.setWindowTitle("材料库 · SMA 支撑与钢结构抗震")
        self.resize(1240, 780)
        self.setMinimumSize(1000, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索 SMA、钢材、Series、Parallel、MinMax、串联、并联…")
        self.category_combo = QComboBox()
        self.category_combo.addItem("全部类别", "")
        for category in library.categories:
            self.category_combo.addItem(category, category)
        self.search_edit.textChanged.connect(self._refilter)
        self.category_combo.currentIndexChanged.connect(self._refilter)
        filters.addWidget(self.search_edit, 2)
        filters.addWidget(self.category_combo, 1)
        layout.addLayout(filters)

        self.material_table = QTableWidget(0, 4)
        self.material_table.setHorizontalHeaderLabels(["材料", "类别", "来源与兼容性", "滞回预览"])
        self.material_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.material_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.material_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.material_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.material_table.setColumnWidth(3, 140)
        self.material_table.verticalHeader().setVisible(False)
        self.material_table.setAlternatingRowColors(True)
        self.material_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.material_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.material_table.itemSelectionChanged.connect(self._selection_changed)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 0, 0, 0)
        self.name_label = QLabel("选择一个材料")
        self.name_label.setObjectName("libraryMaterialTitle")
        self.compatibility_label = QLabel()
        self.compatibility_label.setObjectName("libraryCompatibility")
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.preview_plot = HysteresisPreviewWidget()
        self.preview_plot.setMinimumHeight(210)
        self.command_label = QLabel()
        self.command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.command_label.setWordWrap(True)
        self.source_label = QLabel()
        self.source_label.setOpenExternalLinks(True)
        self.source_label.setWordWrap(True)
        self.notes_label = QLabel()
        self.notes_label.setWordWrap(True)
        self.notes_label.setObjectName("libraryNotes")
        self.parameter_table = QTableWidget(0, 5)
        self.parameter_table.setHorizontalHeaderLabels(["参数", "含义", "示例值", "单位", "对曲线的作用"])
        self.parameter_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.parameter_table.verticalHeader().setVisible(False)
        self.parameter_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reference_widget = QWidget()
        reference_layout = QVBoxLayout(self.reference_widget)
        reference_layout.setContentsMargins(0, 0, 0, 0)
        self.reference_label = QLabel()
        self.reference_label.setWordWrap(True)
        self.reference_list = QListWidget()
        self.reference_list.setMaximumHeight(110)
        self.reference_list.itemSelectionChanged.connect(self._update_action_state)
        reference_layout.addWidget(self.reference_label)
        reference_layout.addWidget(self.reference_list)
        self.reference_widget.hide()
        detail_layout.addWidget(self.name_label)
        detail_layout.addWidget(self.compatibility_label)
        detail_layout.addWidget(self.description_label)
        detail_layout.addWidget(self.preview_plot)
        detail_layout.addSpacing(4)
        detail_layout.addWidget(QLabel("命令模板"))
        detail_layout.addWidget(self.command_label)
        detail_layout.addWidget(self.source_label)
        detail_layout.addWidget(self.notes_label)
        detail_layout.addWidget(self.reference_widget)
        detail_layout.addWidget(QLabel("参数"))
        detail_layout.addWidget(self.parameter_table, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.material_table)
        splitter.addWidget(detail)
        splitter.setSizes([520, 700])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("材料 Tag"))
        self.tag_spin = QSpinBox()
        self.tag_spin.setRange(1, 999999)
        self.tag_spin.setValue(max(1, int(default_tag)))
        self.tag_spin.valueChanged.connect(self._update_action_state)
        actions.addWidget(self.tag_spin)
        self.tag_hint = QLabel()
        self.tag_hint.setObjectName("libraryTagHint")
        actions.addWidget(self.tag_hint)
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.replace_button = QPushButton("替换材料集合")
        self.replace_button.setStyleSheet("color:#92400e; background:transparent;")
        self.replace_button.clicked.connect(lambda: self._accept_action("replace"))
        self.add_button = QPushButton("添加到材料资源")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(lambda: self._accept_action("add"))
        actions.addWidget(cancel)
        actions.addWidget(self.replace_button)
        actions.addWidget(self.add_button)
        layout.addLayout(actions)

        self.setStyleSheet(
            """
            #libraryMaterialTitle { font-size: 18px; font-weight: 700; color: #17243a; }
            #libraryCompatibility { color: #1f5fae; font-weight: 600; }
            #libraryNotes { color: #7c4a03; background: #fff8e8; border: 1px solid #f1d89c; padding: 7px; border-radius: 4px; }
            #libraryTagHint { color: #b45309; }
            """
        )
        self._calculate_previews()
        self._refilter()

    @property
    def selected_tag(self) -> int:
        return int(self.tag_spin.value())

    @property
    def selected_references(self) -> tuple[int, ...]:
        return tuple(
            int(item.data(Qt.ItemDataRole.UserRole))
            for item in self.reference_list.selectedItems()
        )

    def _refilter(self, *args) -> None:
        query = self.search_edit.text()
        category = str(self.category_combo.currentData() or "")
        entries = [
            entry for entry in self.library.entries
            if entry.matches(query) and (not category or entry.category == category)
        ]
        self.material_table.setRowCount(0)
        for entry in entries:
            row = self.material_table.rowCount()
            self.material_table.insertRow(row)
            name_item = QTableWidgetItem(entry.display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self.material_table.setItem(row, 0, name_item)
            self.material_table.setItem(row, 1, QTableWidgetItem(entry.category))
            self.material_table.setItem(
                row,
                2,
                QTableWidgetItem(COMPATIBILITY_LABELS.get(entry.compatibility, entry.source_type)),
            )
            sparkline = SparklineWidget()
            curve = self.preview_curves.get(entry.id)
            if curve is None:
                sparkline.set_curve(None, None, "选择后预览" if self.solver else "未配置求解器")
            elif curve.available:
                sparkline.set_curve(curve.displacement, curve.force)
            else:
                sparkline.set_curve(None, None, self._preview_status(entry, curve))
            self.material_table.setCellWidget(row, 3, sparkline)
            self.material_table.setRowHeight(row, 60)
        if entries:
            self.material_table.selectRow(0)
        else:
            self.selected_entry = None
            self._clear_detail("没有匹配的材料")
        self._update_action_state()

    def _selection_changed(self) -> None:
        row = self.material_table.currentRow()
        if row < 0:
            self.selected_entry = None
            self._clear_detail("选择一个材料")
            self._update_action_state()
            return
        item = self.material_table.item(row, 0)
        self.selected_entry = self.library.get(str(item.data(Qt.ItemDataRole.UserRole)))
        entry = self.selected_entry
        self.name_label.setText(entry.display_name)
        self.compatibility_label.setText(COMPATIBILITY_LABELS.get(entry.compatibility, entry.source_type))
        self.description_label.setText(entry.description)
        curve = self.preview_curves.get(entry.id)
        if curve is None:
            self.preview_plot.clear("后台计算示例曲线中…" if self.solver else "未配置可用的 OpenSees 求解器")
            self._request_preview(entry)
        elif curve.available:
            self.preview_plot.update_curve(
                curve.displacement,
                curve.force,
                title=f"{entry.name} 标准循环预览",
            )
        else:
            self.preview_plot.clear(self._preview_status(entry, curve, detailed=True))
        self.command_label.setText(entry.command)
        if entry.source_url:
            self.source_label.setText(f'<a href="{entry.source_url}">查看来源：{entry.source_type}</a>')
        else:
            self.source_label.setText(entry.source_type)
        preview_note = curve.note if curve is not None else ""
        notes = "\n".join(item for item in (entry.notes, preview_note) if item)
        self.notes_label.setText(notes)
        self.notes_label.setVisible(bool(notes))
        self.reference_list.blockSignals(True)
        self.reference_list.clear()
        self.reference_widget.setVisible(entry.references is not None)
        if entry.references is not None:
            reference = entry.references
            self.reference_label.setText(
                f"{reference.label}：选择{reference.count_text}已有材料（点击选择/取消）。\n"
                "添加时引用所选材料；使用示例替换时会载入示例子材料。"
            )
            self.reference_list.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection
                if reference.max_count == 1
                else QAbstractItemView.SelectionMode.MultiSelection
            )
            for tag in sorted(self.existing_tags):
                name = self.material_names.get(tag, "")
                reference_item = QListWidgetItem(f"Tag {tag} · {name}" if name else f"Tag {tag}")
                reference_item.setData(Qt.ItemDataRole.UserRole, tag)
                self.reference_list.addItem(reference_item)
        self.reference_list.blockSignals(False)
        self.parameter_table.setRowCount(0)
        for parameter in entry.parameters:
            row = self.parameter_table.rowCount()
            self.parameter_table.insertRow(row)
            for column, value in enumerate((
                parameter.name,
                parameter.label,
                f"{parameter.value:g}",
                parameter.unit,
                parameter.description,
            )):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.parameter_table.setItem(row, column, item)
            self.parameter_table.setRowHeight(row, 36)
        self._update_action_state()

    def _clear_detail(self, title: str) -> None:
        self.name_label.setText(title)
        self.compatibility_label.clear()
        self.description_label.clear()
        self.preview_plot.clear(title)
        self.command_label.clear()
        self.source_label.clear()
        self.notes_label.clear()
        self.parameter_table.setRowCount(0)
        self.reference_widget.hide()

    def _calculate_previews(self) -> None:
        if self.solver is None or not self.solver.executable_path.exists():
            return
        for entry in self.library.entries:
            curve = PREVIEW_CACHE.get(self._preview_key(entry))
            if curve is not None:
                self.preview_curves[entry.id] = curve

    def _preview_key(self, entry):
        path = self.solver.executable_path
        return (str(path.resolve()), path.stat().st_mtime_ns, str(self.solver.tcl_library_path),
                self.solver.timeout, entry.as_template().render_command(), self.area, self.length)

    def _request_preview(self, entry):
        if self.solver is None or not self.solver.executable_path.exists() or entry.id in self._preview_jobs:
            return
        key = self._preview_key(entry)
        if key in PREVIEW_CACHE:
            self._preview_ready(entry.id, PREVIEW_CACHE[key])
            return
        job = PreviewJob(entry, key, self.solver, self.area, self.length, self._preview_cancel)
        job.signals.finished.connect(self._preview_ready)
        self._preview_jobs[entry.id] = job
        QThreadPool.globalInstance().start(job)

    def _preview_ready(self, entry_id, curve):
        self.preview_curves[entry_id] = curve
        self._preview_jobs.pop(entry_id, None)
        for row in range(self.material_table.rowCount()):
            if self.material_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == entry_id:
                self.material_table.cellWidget(row, 3).set_curve(curve.displacement, curve.force, curve.note)
        if self.selected_entry is not None and self.selected_entry.id == entry_id:
            if curve.available:
                self.preview_plot.update_curve(curve.displacement, curve.force, title=f'{self.selected_entry.name} 示例标准循环')
            else:
                self.preview_plot.clear(curve.note)

    @staticmethod
    def _preview_status(
        entry: MaterialLibraryEntry,
        curve: MaterialPreviewCurve,
        *,
        detailed: bool = False,
    ) -> str:
        if entry.compatibility == "requires_transient":
            return "需要瞬态 / 速率相关协议" if detailed else "需瞬态协议"
        if detailed and curve.note:
            return curve.note
        return "预览计算失败" if detailed else "预览失败"

    def _update_action_state(self, *args) -> None:
        entry = self.selected_entry
        selected = entry is not None
        duplicate = self.selected_tag in self.existing_tags
        reference_error = ""
        preview_conflict = False
        if entry is not None and entry.references is not None:
            try:
                entry.references.validate_tags(self.selected_references, owner_tag=self.selected_tag)
            except ValueError as exc:
                reference_error = str(exc)
            preview_conflict = self.selected_tag in entry.references.preview_tags
        self.replace_button.setText(
            "使用示例替换材料集合" if entry and entry.references else "替换材料集合"
        )
        self.replace_button.setEnabled(selected and not preview_conflict)
        self.add_button.setEnabled(selected and not duplicate and not reference_error)
        hints = []
        if duplicate:
            hints.append("该 Tag 已存在，添加时请选择其他 Tag")
        if reference_error:
            hints.append(reference_error)
        if preview_conflict:
            hints.append("该 Tag 与示例子材料冲突，替换时请选择其他 Tag")
        self.tag_hint.setText("；".join(hints))
        self.tag_hint.setWordWrap(True)

    def _accept_action(self, action: str) -> None:
        if self.selected_entry is None:
            return
        if action == "add" and self.selected_tag in self.existing_tags:
            QMessageBox.warning(self, "材料 Tag", "该材料 Tag 已存在，请选择未使用的 Tag。")
            return
        try:
            if action == "add":
                self.selected_entry.instantiate(
                    tag=self.selected_tag, references=self.selected_references,
                )
            else:
                self.selected_entry.as_template(tag=self.selected_tag)
        except ValueError as exc:
            QMessageBox.warning(self, "材料库", str(exc))
            return
        self.selected_action = action
        self.accept()
