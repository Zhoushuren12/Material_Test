from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from material_test.domain import ModelingConfig, TopologyNode
from material_test.ui.editors import CompactDoubleSpinBox
from material_test.ui.topology_preview import TopologyPreview


KIND_LABELS = {
    "material": "材料单元",
    "series": "串联组",
    "parallel": "并联组",
}


class TopologyTree(QTreeWidget):
    reordered = Signal()

    def dropEvent(self, event):
        selected = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        if (selected is None or target is None or selected.parent() is None
                or selected.parent() is not target.parent()
                or self.dropIndicatorPosition() not in (self.DropIndicatorPosition.AboveItem, self.DropIndicatorPosition.BelowItem)):
            event.ignore()
            return
        super().dropEvent(event)
        if event.isAccepted():
            self.reordered.emit()


class TopologyEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating_properties = False
        self._material_options: dict[int, str] = {}
        self.force_deformation_mode = False
        self.tree = TopologyTree()
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.reordered.connect(self._model_changed)
        self.tree.setToolTip("可拖拽调整同组组件的先后顺序；连接方式通过包装按钮修改")
        self.tree.setHeaderLabels(["组件", "材料赋值", "长度权重"])
        self.tree.setObjectName("topologyTree")
        self.tree.setMinimumHeight(64)
        self.tree.setIndentation(18)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(self._selection_changed)

        add_material = QPushButton("添加单元")
        add_material.setObjectName("primaryButton")
        wrap_series = QPushButton("串联")
        wrap_parallel = QPushButton("并联")
        wrap_series.setToolTip("将所选组件包成串联组")
        wrap_parallel.setToolTip("将所选组件包成并联组")
        duplicate = QPushButton("复制")
        remove = QPushButton("删除")
        move_up = QPushButton("上移")
        move_down = QPushButton("下移")
        add_material.clicked.connect(self.add_material)
        wrap_series.clicked.connect(lambda: self.wrap_selected("series"))
        wrap_parallel.clicked.connect(lambda: self.wrap_selected("parallel"))
        duplicate.clicked.connect(self.duplicate_selected)
        remove.clicked.connect(self.remove_selected)
        move_up.clicked.connect(lambda: self.move_selected(-1))
        move_down.clicked.connect(lambda: self.move_selected(1))

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        for button in (add_material, wrap_series, wrap_parallel):
            toolbar.addWidget(button)
        toolbar.addStretch(1)

        self.name_edit = QLineEdit()
        self.material_combo = QComboBox()
        self.material_combo.addItem("未赋值", None)
        self.area_spin = CompactDoubleSpinBox()
        self.area_spin.setDecimals(6)
        self.area_spin.setRange(0.0, 1.0e12)
        self.area_spin.setSpecialValueText("默认面积")
        self.area_spin.setToolTip("设为 0 时使用项目默认面积")
        self.weight_spin = CompactDoubleSpinBox()
        self.weight_spin.setDecimals(4)
        self.weight_spin.setRange(0.0001, 1.0e6)
        self.weight_spin.setValue(1.0)
        self.kind_label = QLabel("-")
        self.kind_label.setObjectName("typeBadge")
        self.name_edit.editingFinished.connect(self._properties_changed)
        self.material_combo.currentIndexChanged.connect(self._properties_changed)
        self.area_spin.valueChanged.connect(self._properties_changed)
        self.weight_spin.valueChanged.connect(self._properties_changed)

        properties = QFrame()
        properties.setObjectName("surfaceCard")
        properties.setMinimumWidth(200)
        properties_layout = QVBoxLayout(properties)
        properties_layout.setContentsMargins(12, 12, 12, 12)
        properties_layout.setSpacing(8)
        property_title = QLabel("组件属性")
        property_title.setObjectName("sectionTitle")
        properties_layout.addWidget(property_title)
        form = QFormLayout()
        form.setVerticalSpacing(6)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setHorizontalSpacing(8)
        for field in (self.name_edit, self.material_combo, self.area_spin, self.weight_spin):
            field.setObjectName("compactField")
            field.setMinimumWidth(60)
            field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        form.addRow("类型", self.kind_label)
        form.addRow("名称", self.name_edit)
        form.addRow("材料赋值", self.material_combo)
        form.addRow("单元面积", self.area_spin)
        form.addRow("长度权重", self.weight_spin)
        properties_layout.addLayout(form)
        properties_layout.addStretch(1)

        left = QFrame()
        left.setObjectName("surfaceCard")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        tree_title = QLabel("装配组件")
        tree_title.setObjectName("sectionTitle")
        left_layout.addWidget(tree_title)
        left_layout.addLayout(toolbar)
        left_layout.addWidget(self.tree, 1)
        edit_tools = QHBoxLayout()
        edit_tools.setSpacing(6)
        for button in (duplicate, move_up, move_down, remove):
            button.setObjectName("compactButton" if button is not remove else "dangerButton")
            edit_tools.addWidget(button)
        edit_tools.addStretch(1)
        left_layout.addLayout(edit_tools)

        self.preview = TopologyPreview()
        preview_panel = QFrame()
        preview_panel.setObjectName("surfaceCard")
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(14, 14, 14, 10)
        preview_layout.setSpacing(10)
        preview_header = QHBoxLayout()
        title = QLabel("节点与单元预览")
        title.setObjectName("sectionTitle")
        title.setToolTip("拓扑示意：仅表示连接关系，不按单元长度权重比例绘制")
        preview_header.addWidget(title)
        preview_header.addStretch(1)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("mutedLabel")
        preview_header.addWidget(self.zoom_label)
        for text, tip, action in (
            ("−", "缩小", lambda: self.preview.zoom_by(1 / 1.2)),
            ("+", "放大", lambda: self.preview.zoom_by(1.2)),
            ("适配全图", "显示完整拓扑（也可双击画布）", self.preview.fit_to_view),
        ):
            button = QToolButton()
            button.setText(text)
            button.setObjectName("canvasTool")
            button.setToolTip(tip)
            button.clicked.connect(action)
            preview_header.addWidget(button)
        self.preview.view_changed.connect(lambda value: self.zoom_label.setText(f"{value}%"))
        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(self.preview, 1)
        preview_footer = QHBoxLayout()
        self.preview_summary = QLabel()
        self.preview_summary.setObjectName("mutedLabel")
        self.preview.summary_changed.connect(self.preview_summary.setText)
        preview_footer.addWidget(self.preview_summary, 1)
        hint = QLabel("滚轮缩放 · 左键拖动")
        hint.setObjectName("mutedLabel")
        preview_footer.addWidget(hint)
        preview_layout.addLayout(preview_footer)

        editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        editor_splitter.setChildrenCollapsible(False)
        editor_splitter.setHandleWidth(8)
        editor_splitter.addWidget(left)
        editor_splitter.addWidget(properties)
        editor_splitter.setSizes([360, 210])
        editor_splitter.setStretchFactor(0, 1)
        editor_splitter.setStretchFactor(1, 0)
        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        vertical_splitter.setChildrenCollapsible(False)
        vertical_splitter.setHandleWidth(10)
        vertical_splitter.addWidget(preview_panel)
        vertical_splitter.addWidget(editor_splitter)
        vertical_splitter.setSizes([410, 270])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(vertical_splitter, 1)
        self.set_root(TopologyNode.material(1, name="E01"))

    def set_modeling(self, modeling: ModelingConfig, test_material_tag: int) -> None:
        self.set_root(deepcopy(modeling.resolved_root(test_material_tag)))

    def modeling(self, *, require_assigned: bool = True) -> ModelingConfig:
        if require_assigned:
            unassigned, undefined = self.assignment_issues()
            if unassigned:
                raise ValueError(f"以下单元尚未分配材料：{', '.join(unassigned)}")
            if undefined:
                raise ValueError(f"以下单元引用了未定义材料：{', '.join(undefined)}")
        root_item = self.tree.topLevelItem(0)
        if root_item is None:
            raise ValueError("请先创建支撑装配模型")
        root = self._node_from_item(root_item, validate=False)
        config = ModelingConfig(topology="nested", root=root)
        config.validate(require_assigned=require_assigned)
        return config

    def set_root(self, root: TopologyNode) -> None:
        root = deepcopy(root)
        leaves = []
        def collect(node):
            if node.kind == "material":
                leaves.append(node)
            for child in node.children:
                collect(child)
        collect(root)
        names = {node.name for node in leaves if node.name}
        for node in leaves:
            if not node.name:
                index = 1
                while f"E{index:02d}" in names:
                    index += 1
                node.name = f"E{index:02d}"
                names.add(node.name)
        self.tree.clear()
        item = self._item_from_node(root)
        self.tree.addTopLevelItem(item)
        self.tree.expandAll()
        self.tree.setCurrentItem(item)
        self._refresh_preview()

    def add_material(self) -> None:
        tag = self.material_combo.currentData()
        self.insert_material(
            int(tag) if tag is not None else None,
            self._next_element_name(),
        )

    def insert_material(self, material_tag: int | None, name: str = "") -> None:
        selected = self.tree.currentItem()
        node = TopologyNode(
            kind="material",
            material_tag=int(material_tag) if material_tag is not None else None,
            name=name or self._next_element_name(),
        )
        if selected is None:
            self.set_root(node)
            return
        new_item = self._item_from_node(node)
        kind = selected.data(0, Qt.ItemDataRole.UserRole)["kind"]
        if kind in {"series", "parallel"}:
            selected.addChild(new_item)
        else:
            parent = selected.parent()
            if parent is None:
                wrapper = self._new_group_item("series")
                self.tree.takeTopLevelItem(0)
                wrapper.addChild(selected)
                wrapper.addChild(new_item)
                self.tree.addTopLevelItem(wrapper)
            else:
                parent.insertChild(parent.indexOfChild(selected) + 1, new_item)
        self.tree.expandAll()
        self.tree.setCurrentItem(new_item)
        self._model_changed()

    def count_material_references(self, material_tag: int) -> int:
        """Return how many material components use a material tag."""
        target = int(material_tag)
        return sum(
            1
            for item in QTreeWidgetItemIteratorCompat(self.tree)
            if item.data(0, Qt.ItemDataRole.UserRole).get("kind") == "material"
            and int(item.data(0, Qt.ItemDataRole.UserRole).get("material_tag") or 0) == target
        )

    def set_material_options(self, options: list[tuple[int, str]]) -> None:
        """Update materials available for assigning to assembly elements."""
        selected = self.material_combo.currentData()
        self._material_options = {int(tag): str(label) for tag, label in options}
        self._updating_properties = True
        self.material_combo.clear()
        self.material_combo.addItem("未赋值", None)
        for tag, label in self._material_options.items():
            self.material_combo.addItem(f"Tag {tag} · {label}", tag)
        index = self.material_combo.findData(selected)
        self.material_combo.setCurrentIndex(max(index, 0))
        self._updating_properties = False
        for item in QTreeWidgetItemIteratorCompat(self.tree):
            self._update_item_text(item)
        self._refresh_preview()

    def assignment_issues(self) -> tuple[list[str], list[str]]:
        unassigned: list[str] = []
        undefined: list[str] = []
        for item in QTreeWidgetItemIteratorCompat(self.tree):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data.get("kind") != "material":
                continue
            name = str(data.get("name") or item.text(0))
            tag = data.get("material_tag")
            if tag is None:
                unassigned.append(name)
            elif int(tag) not in self._material_options:
                undefined.append(f"{name} (Tag {tag})")
        return unassigned, undefined

    def reassign_material_tag(self, old_tag: int, new_tag: int | None) -> int:
        """Replace or clear assignments while preserving every topology element."""
        changed = 0
        for item in QTreeWidgetItemIteratorCompat(self.tree):
            data = dict(item.data(0, Qt.ItemDataRole.UserRole))
            if data.get("kind") != "material" or data.get("material_tag") != int(old_tag):
                continue
            data["material_tag"] = int(new_tag) if new_tag is not None else None
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            self._update_item_text(item)
            changed += 1
        if changed:
            self._selection_changed(self.tree.currentItem(), None)
            self._model_changed()
        return changed

    def clear_undefined_assignments(self) -> int:
        changed = 0
        for item in QTreeWidgetItemIteratorCompat(self.tree):
            data = dict(item.data(0, Qt.ItemDataRole.UserRole))
            tag = data.get("material_tag")
            if data.get("kind") != "material" or tag is None or int(tag) in self._material_options:
                continue
            data["material_tag"] = None
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            self._update_item_text(item)
            changed += 1
        if changed:
            self._selection_changed(self.tree.currentItem(), None)
            self._model_changed()
        return changed

    def wrap_selected(self, kind: str) -> None:
        selected = self.tree.currentItem()
        if selected is None:
            return
        selected_tag = self.material_combo.currentData()
        parent = selected.parent()
        index = parent.indexOfChild(selected) if parent else self.tree.indexOfTopLevelItem(selected)
        if parent:
            parent.takeChild(index)
        else:
            self.tree.takeTopLevelItem(index)
        wrapper = self._new_group_item(kind)
        wrapper.addChild(selected)
        wrapper.addChild(self._item_from_node(TopologyNode(
            kind="material",
            material_tag=int(selected_tag) if selected_tag is not None else None,
            name=self._next_element_name(),
        )))
        if parent:
            parent.insertChild(index, wrapper)
        else:
            self.tree.insertTopLevelItem(index, wrapper)
        wrapper.setExpanded(True)
        self.tree.setCurrentItem(wrapper)
        self._model_changed()

    def duplicate_selected(self) -> None:
        selected = self.tree.currentItem()
        if selected is None:
            return
        copy_item = self._item_from_node(deepcopy(self._node_from_item(selected, validate=False)))
        parent = selected.parent()
        if parent is None:
            QMessageBox.information(self, "复制组件", "根组件不能直接复制，请先用串联或并联包装它。")
            return
        parent.insertChild(parent.indexOfChild(selected) + 1, copy_item)
        self.tree.setCurrentItem(copy_item)
        self._model_changed()

    def remove_selected(self) -> None:
        selected = self.tree.currentItem()
        if selected is None:
            return
        parent = selected.parent()
        if parent is None:
            QMessageBox.information(self, "删除组件", "模型至少需要一个根组件。")
            return
        parent.removeChild(selected)
        if parent.childCount() == 1:
            remaining = parent.takeChild(0)
            grandparent = parent.parent()
            if grandparent is None:
                root_index = self.tree.indexOfTopLevelItem(parent)
                self.tree.takeTopLevelItem(root_index)
                self.tree.insertTopLevelItem(root_index, remaining)
            else:
                index = grandparent.indexOfChild(parent)
                grandparent.takeChild(index)
                grandparent.insertChild(index, remaining)
            self.tree.setCurrentItem(remaining)
        else:
            self.tree.setCurrentItem(parent)
        self._model_changed()

    def move_selected(self, offset: int) -> None:
        selected = self.tree.currentItem()
        if selected is None or selected.parent() is None:
            return
        parent = selected.parent()
        old_index = parent.indexOfChild(selected)
        new_index = old_index + offset
        if new_index < 0 or new_index >= parent.childCount():
            return
        parent.takeChild(old_index)
        parent.insertChild(new_index, selected)
        self.tree.setCurrentItem(selected)
        self._model_changed()

    def _selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        self._updating_properties = True
        enabled = current is not None
        data = current.data(0, Qt.ItemDataRole.UserRole) if current else {}
        kind = data.get("kind", "")
        self.kind_label.setText(KIND_LABELS.get(kind, "-"))
        self.name_edit.setText(data.get("name", ""))
        tag = data.get("material_tag")
        index = self.material_combo.findData(tag)
        if index < 0 and tag is not None:
            self.material_combo.addItem(f"Tag {tag} · 未定义", int(tag))
            index = self.material_combo.count() - 1
        self.material_combo.setCurrentIndex(max(index, 0))
        self.area_spin.setValue(float(data.get("area") or 0.0))
        self.weight_spin.setValue(float(data.get("length_weight", 1.0)))
        self.name_edit.setEnabled(enabled)
        self.material_combo.setEnabled(kind == "material")
        self.area_spin.setEnabled(kind == "material" and not self.force_deformation_mode)
        self.weight_spin.setEnabled(enabled)
        self._updating_properties = False

    def _properties_changed(self, *args) -> None:
        if self._updating_properties:
            return
        item = self.tree.currentItem()
        if item is None:
            return
        data = dict(item.data(0, Qt.ItemDataRole.UserRole))
        data["name"] = self.name_edit.text().strip()
        data["length_weight"] = float(self.weight_spin.value())
        if data["kind"] == "material":
            tag = self.material_combo.currentData()
            data["material_tag"] = int(tag) if tag is not None else None
            data["area"] = float(self.area_spin.value()) or None
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        self._update_item_text(item)
        self._model_changed()

    def _model_changed(self) -> None:
        self._refresh_preview()
        self.changed.emit()

    def _refresh_preview(self) -> None:
        root_item = self.tree.topLevelItem(0)
        if root_item is None:
            self.preview.set_topology(None)
            return
        try:
            root = self._node_from_item(root_item, validate=False)
        except (TypeError, ValueError):
            root = None
        self.preview.set_topology(root)

    def _next_element_name(self) -> str:
        names = {item.data(0, Qt.ItemDataRole.UserRole).get("name") for item in QTreeWidgetItemIteratorCompat(self.tree)}
        index = 1
        while f"E{index:02d}" in names:
            index += 1
        return f"E{index:02d}"

    def _new_group_item(self, kind: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {"kind": kind, "name": "", "length_weight": 1.0, "area": None, "material_tag": None},
        )
        self._update_item_text(item)
        return item

    def _item_from_node(self, node: TopologyNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(
            0,
            Qt.ItemDataRole.UserRole,
            {
                "kind": node.kind,
                "name": node.name,
                "material_tag": node.material_tag,
                "area": node.area,
                "length_weight": node.length_weight,
            },
        )
        self._update_item_text(item)
        for child in node.children:
            item.addChild(self._item_from_node(child))
        return item

    def _node_from_item(self, item: QTreeWidgetItem, *, validate: bool = True) -> TopologyNode:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        node = TopologyNode(
            kind=data["kind"],
            name=data.get("name", ""),
            material_tag=data.get("material_tag"),
            area=data.get("area"),
            length_weight=float(data.get("length_weight", 1.0)),
            children=[self._node_from_item(item.child(index), validate=False) for index in range(item.childCount())],
        )
        if validate:
            node.validate(require_assigned=False)
        return node

    def _update_item_text(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        kind = data["kind"]
        item.setText(0, data.get("name") or KIND_LABELS[kind])
        tag = data.get("material_tag")
        item.setText(1, "未赋值" if kind == "material" and tag is None else str(tag or ""))
        item.setText(2, f"{float(data.get('length_weight', 1.0)):g}")


class QTreeWidgetItemIteratorCompat:
    def __init__(self, tree: QTreeWidget) -> None:
        self._items: list[QTreeWidgetItem] = []
        for index in range(tree.topLevelItemCount()):
            self._collect(tree.topLevelItem(index))

    def _collect(self, item: QTreeWidgetItem) -> None:
        self._items.append(item)
        for index in range(item.childCount()):
            self._collect(item.child(index))

    def __iter__(self):
        return iter(self._items)
