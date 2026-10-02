from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from material_test.domain import ModelingConfig, TopologyNode
from material_test.modeling import compile_topology


KIND_LABELS = {
    "material": "材料单元",
    "series": "串联组",
    "parallel": "并联组",
}


class TopologyPreview(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root: TopologyNode | None = None
        self._error = ""
        self._warning = ""
        self.setMinimumSize(420, 140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_topology(self, root: TopologyNode | None) -> None:
        self._root = root
        self._error = ""
        self._warning = ""
        if root is not None:
            try:
                root.validate(require_assigned=False)
            except ValueError as exc:
                self._error = str(exc)
            if not self._error and self._has_unassigned_material(root):
                self._warning = "存在未赋值材料的单元"
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9fc"))
        if self._root is None:
            self._draw_centered(painter, "尚未创建模型", QColor("#6b7280"))
            return
        if self._error:
            self._draw_centered(painter, self._error, QColor("#b45309"))
            return

        compiled = None
        if not self._warning:
            try:
                compiled = compile_topology(self._root)
            except ValueError as exc:
                self._draw_centered(painter, str(exc), QColor("#b45309"))
                return

        margin_x = 58.0
        x0 = margin_x
        x1 = max(x0 + 160.0, self.width() - margin_x)
        y = self.height() / 2.0
        node_counter = [2]
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        element_counter = [0]
        self._draw_component(
            painter, self._root, QPointF(x0, y), QPointF(x1, y), 1, 2,
            node_counter, element_counter,
        )
        self._draw_node(painter, QPointF(x0, y), 1, fixed=True)
        self._draw_node(painter, QPointF(x1, y), 2, controlled=True)

        summary = (
            f"节点 {len(compiled.nodes)}    单元 {len(compiled.elements)}"
            if compiled is not None
            else f"单元 {self._component_count(self._root)}    {self._warning}"
        )
        painter.setPen(QColor("#64748b"))
        painter.drawText(18, 26, summary)

    @staticmethod
    def _component_count(component: TopologyNode) -> int:
        if component.kind == "material":
            return 1
        return sum(TopologyPreview._component_count(child) for child in component.children)

    @staticmethod
    def _has_unassigned_material(component: TopologyNode) -> bool:
        if component.kind == "material":
            return component.material_tag is None
        return any(TopologyPreview._has_unassigned_material(child) for child in component.children)

    def _draw_component(
        self,
        painter: QPainter,
        component: TopologyNode,
        start: QPointF,
        end: QPointF,
        start_tag: int,
        end_tag: int,
        node_counter: list[int],
        element_counter: list[int],
    ) -> None:
        if component.kind == "material":
            element_counter[0] += 1
            self._draw_material(painter, component, start, end, element_counter[0])
            return

        if component.kind == "series":
            total = sum(child.length_weight for child in component.children)
            current = start
            current_tag = start_tag
            used = 0.0
            for index, child in enumerate(component.children):
                last = index == len(component.children) - 1
                used += child.length_weight
                ratio = used / total
                next_point = end if last else QPointF(start.x() + (end.x() - start.x()) * ratio, start.y())
                if last:
                    next_tag = end_tag
                else:
                    node_counter[0] += 1
                    next_tag = node_counter[0]
                self._draw_component(
                    painter, child, current, next_point, current_tag, next_tag,
                    node_counter, element_counter,
                )
                if not last:
                    self._draw_node(painter, next_point, next_tag)
                current = next_point
                current_tag = next_tag
            return

        count = len(component.children)
        spacing = min(74.0, max(42.0, (self.height() - 120.0) / max(count - 1, 1)))
        offsets = [(index - (count - 1) / 2.0) * spacing for index in range(count)]
        branch_in = start.x() + min(42.0, (end.x() - start.x()) * 0.14)
        branch_out = end.x() - min(42.0, (end.x() - start.x()) * 0.14)
        wire_pen = QPen(QColor("#94a3b8"), 2.0)
        for child, offset in zip(component.children, offsets):
            branch_start = QPointF(branch_in, start.y() + offset)
            branch_end = QPointF(branch_out, end.y() + offset)
            painter.setPen(wire_pen)
            painter.drawLine(start, branch_start)
            painter.drawLine(branch_end, end)
            self._draw_component(
                painter, child, branch_start, branch_end, start_tag, end_tag,
                node_counter, element_counter,
            )

    @staticmethod
    def _draw_material(
        painter: QPainter,
        component: TopologyNode,
        start: QPointF,
        end: QPointF,
        element_tag: int,
    ) -> None:
        mid_x = (start.x() + end.x()) / 2.0
        center_y = (start.y() + end.y()) / 2.0
        box_width = min(108.0, max(64.0, abs(end.x() - start.x()) * 0.48))
        left = QPointF(mid_x - box_width / 2.0, center_y)
        right = QPointF(mid_x + box_width / 2.0, center_y)
        painter.setPen(QPen(QColor("#64748b"), 2.0))
        painter.drawLine(start, left)
        painter.drawLine(right, end)
        rect = (mid_x - box_width / 2.0, center_y - 17.0, box_width, 34.0)
        painter.setPen(QPen(QColor("#2563eb"), 1.6))
        painter.setBrush(QColor("#eaf2ff"))
        painter.drawRoundedRect(*rect, 4.0, 4.0)
        painter.setPen(QColor("#173b70"))
        label = component.name or f"E{element_tag}"
        material_label = "未赋值" if component.material_tag is None else f"M{component.material_tag}"
        label = f"{label} · {material_label}"
        painter.drawText(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]), Qt.AlignmentFlag.AlignCenter, label)

    @staticmethod
    def _draw_node(
        painter: QPainter,
        point: QPointF,
        tag: int,
        *,
        fixed: bool = False,
        controlled: bool = False,
    ) -> None:
        painter.setPen(QPen(QColor("#0f172a"), 1.4))
        painter.setBrush(QColor("#0f172a"))
        painter.drawEllipse(point, 4.5, 4.5)
        painter.setPen(QColor("#475569"))
        painter.drawText(int(point.x() - 14), int(point.y() + 22), 28, 18, Qt.AlignmentFlag.AlignCenter, str(tag))
        if fixed:
            painter.setPen(QPen(QColor("#dc2626"), 2.0))
            painter.drawLine(QPointF(point.x() - 8, point.y() + 9), QPointF(point.x() + 8, point.y() + 9))
        if controlled:
            painter.setPen(QPen(QColor("#16a34a"), 2.0))
            painter.drawLine(QPointF(point.x() - 14, point.y() - 18), QPointF(point.x() + 2, point.y() - 18))
            painter.drawLine(QPointF(point.x() + 2, point.y() - 18), QPointF(point.x() - 4, point.y() - 23))
            painter.drawLine(QPointF(point.x() + 2, point.y() - 18), QPointF(point.x() - 4, point.y() - 13))

    def _draw_centered(self, painter: QPainter, text: str, color: QColor) -> None:
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.setPen(color)
        painter.drawText(self.rect().adjusted(36, 36, -36, -36), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)


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
        self.tree.setColumnWidth(0, 230)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(self._selection_changed)

        add_material = QPushButton("添加单元")
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
        for button in (add_material, wrap_series, wrap_parallel, duplicate, remove, move_up, move_down):
            toolbar.addWidget(button)
        toolbar.addStretch(1)

        self.name_edit = QLineEdit()
        self.material_combo = QComboBox()
        self.material_combo.addItem("未赋值", None)
        self.area_spin = QDoubleSpinBox()
        self.area_spin.setDecimals(6)
        self.area_spin.setRange(0.0, 1.0e12)
        self.area_spin.setSpecialValueText("使用项目默认值")
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setDecimals(4)
        self.weight_spin.setRange(0.0001, 1.0e6)
        self.weight_spin.setValue(1.0)
        self.kind_label = QLabel("-")
        self.name_edit.editingFinished.connect(self._properties_changed)
        self.material_combo.currentIndexChanged.connect(self._properties_changed)
        self.area_spin.valueChanged.connect(self._properties_changed)
        self.weight_spin.valueChanged.connect(self._properties_changed)

        properties = QWidget()
        form = QFormLayout(properties)
        form.setContentsMargins(0, 8, 0, 0)
        form.addRow("类型", self.kind_label)
        form.addRow("名称", self.name_edit)
        form.addRow("材料赋值", self.material_combo)
        form.addRow("单元面积", self.area_spin)
        form.addRow("长度权重", self.weight_spin)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(toolbar)
        left_layout.addWidget(self.tree, 1)
        left_layout.addWidget(properties)

        self.preview = TopologyPreview()
        self.preview.setMaximumHeight(180)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("节点与单元预览")
        title.setObjectName("sectionTitle")
        preview_layout.addWidget(title)
        preview_layout.addWidget(self.preview, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(preview_panel)
        layout.addWidget(left, 1)
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
