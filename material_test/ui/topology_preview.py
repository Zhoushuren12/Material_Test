from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView

from material_test.domain import TopologyNode
from material_test.modeling import compile_topology


class TopologyPreview(QGraphicsView):
    """A fitted topology schematic with cursor-centered zoom and hand panning."""

    view_changed = Signal(int)
    summary_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(260, 140)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QColor("#fbfcfe"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setToolTip("滚轮放大 / 缩小 · 按住左键拖动 · 双击适配全图")
        self._root = None
        self._error = ""
        self._warning = ""
        self._auto_fit = True
        self._content_rect = QRectF(-100, -40, 200, 80)
        self._sizes = {}
        self._structure = None
        self._name_font = QFont("Microsoft YaHei UI", 10)
        self._name_font.setWeight(QFont.Weight.DemiBold)
        self._detail_font = QFont("Microsoft YaHei UI", 8)

    @staticmethod
    def _structure_key(node):
        if node is None:
            return None
        return (node.kind, tuple(TopologyPreview._structure_key(child) for child in node.children))

    def set_topology(self, root: TopologyNode | None) -> None:
        structure = self._structure_key(root)
        refit = structure != self._structure or self._auto_fit
        self._structure = structure
        self._root = root
        self._error = self._warning = ""
        self._sizes.clear()
        self.scene().clear()
        summary = "尚未创建模型"
        if root is None:
            self._add_message("尚未创建模型", "添加单元，开始搭建串并联拓扑")
        else:
            try:
                root.validate(require_assigned=False)
                if self._has_unassigned_material(root):
                    self._warning = "存在未赋值材料的单元"
                else:
                    compile_topology(root)
            except (TypeError, ValueError) as exc:
                self._error = str(exc)
            if self._error:
                summary = "拓扑待检查"
                self._add_message("请检查模型连接", self._error)
            else:
                width, _ = self._measure(root)
                width = max(840, width)
                node_counter, element_counter = [2], [0]
                self._draw_component(root, QPointF(0, 0), QPointF(width, 0), node_counter, element_counter)
                self._draw_node(QPointF(0, 0), 1, fixed=True)
                self._draw_node(QPointF(width, 0), 2, controlled=True)
                summary = f"{node_counter[0]} 个节点 · {element_counter[0]} 个单元"
                if self._warning:
                    summary += " · 存在未赋值单元"
        self._content_rect = self.scene().itemsBoundingRect().adjusted(-28, -28, 28, 28)
        self._update_pan_bounds()
        self.summary_changed.emit(summary)
        if refit:
            self.fit_to_view()

    def _update_pan_bounds(self):
        # Leave room around the model so hand dragging also works when fitted.
        scale = max(self.transform().m11(), 0.001)
        padding = max(self._content_rect.width(), self._content_rect.height(),
                      self.viewport().width() / scale, self.viewport().height() / scale)
        self.setSceneRect(self._content_rect.adjusted(-padding, -padding, padding, padding))

    def fit_to_view(self) -> None:
        self._auto_fit = True
        self.resetTransform()
        viewport = self.viewport().rect().adjusted(16, 16, -16, -16)
        scale = min(viewport.width() / max(self._content_rect.width(), 1),
                    viewport.height() / max(self._content_rect.height(), 1), 1.25)
        self.scale(max(scale, 0.001), max(scale, 0.001))
        self._update_pan_bounds()
        self.centerOn(self._content_rect.center())
        self.view_changed.emit(round(self.transform().m11() * 100))

    def zoom_by(self, factor: float, position: QPointF | None = None) -> None:
        position = position or QPointF(self.viewport().rect().center())
        before = self.mapToScene(position.toPoint())
        old_scale = self.transform().m11()
        new_scale = max(min(0.01, old_scale), min(6.0, old_scale * factor))
        self._auto_fit = False
        self.scale(new_scale / old_scale, new_scale / old_scale)
        self._update_pan_bounds()
        after = self.mapToScene(position.toPoint())
        self.translate(after.x() - before.x(), after.y() - before.y())
        self.view_changed.emit(round(new_scale * 100))

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self.zoom_by(math.pow(1.2, max(-4, min(4, delta / 120))), event.position())
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._auto_fit = False
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.fit_to_view()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit_to_view()
        else:
            self._update_pan_bounds()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._auto_fit:
            QTimer.singleShot(0, self.fit_to_view)

    def drawBackground(self, painter, rect) -> None:  # noqa: N802
        painter.fillRect(rect, self.backgroundBrush())
        spacing = 24 / max(self.transform().m11(), 0.001)
        pen = QPen(QColor("#e1e7ef"), 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x0 = math.floor(rect.left() / spacing) * spacing
        y0 = math.floor(rect.top() / spacing) * spacing
        for i in range(int(rect.width() / spacing) + 2):
            for j in range(int(rect.height() / spacing) + 2):
                painter.drawPoint(QPointF(x0 + i * spacing, y0 + j * spacing))

    @staticmethod
    def _has_unassigned_material(component):
        if component.kind == "material":
            return component.material_tag is None
        return any(TopologyPreview._has_unassigned_material(child) for child in component.children)

    def _measure(self, component):
        if component.kind == "material":
            label_width = QFontMetricsF(self._name_font).horizontalAdvance(component.name or "E000")
            size = (min(240, max(128, label_width + 30)) + 100, 78)
        else:
            sizes = [self._measure(child) for child in component.children]
            if component.kind == "series":
                size = (sum(width for width, _ in sizes), max(height for _, height in sizes))
            else:
                size = (max(width for width, _ in sizes) + 112,
                        sum(height for _, height in sizes) + 18 * (len(sizes) - 1))
        self._sizes[id(component)] = size
        return size

    def _wire(self, start, end, color="#94a3b8"):
        item = self.scene().addLine(start.x(), start.y(), end.x(), end.y(), QPen(QColor(color), 1.6))
        item.setZValue(-1)

    def _draw_component(self, component, start, end, node_counter, element_counter):
        if component.kind == "material":
            element_counter[0] += 1
            self._draw_material(component, start, end, element_counter[0])
        elif component.kind == "series":
            total_width = self._sizes[id(component)][0]
            used_width = 0
            current = start
            for index, child in enumerate(component.children):
                used_width += self._sizes[id(child)][0]
                last = index == len(component.children) - 1
                next_point = end if last else QPointF(start.x() + (end.x() - start.x()) * used_width / total_width, start.y())
                if not last:
                    node_counter[0] += 1
                    next_tag = node_counter[0]
                self._draw_component(child, current, next_point, node_counter, element_counter)
                if not last:
                    self._draw_node(next_point, next_tag)
                current = next_point
        else:
            top = start.y() - self._sizes[id(component)][1] / 2
            branches = []
            for child in component.children:
                height = self._sizes[id(child)][1]
                y = top + height / 2
                branches.append((child, y))
                top += height + 18
            bus_in, bus_out = start.x() + 28, end.x() - 28
            self._wire(start, QPointF(bus_in, start.y()))
            self._wire(QPointF(bus_out, end.y()), end)
            self._wire(QPointF(bus_in, branches[0][1]), QPointF(bus_in, branches[-1][1]))
            self._wire(QPointF(bus_out, branches[0][1]), QPointF(bus_out, branches[-1][1]))
            for child, y in branches:
                branch_start, branch_end = QPointF(start.x() + 56, y), QPointF(end.x() - 56, y)
                self._wire(QPointF(bus_in, y), branch_start)
                self._wire(branch_end, QPointF(bus_out, y))
                self._draw_component(child, branch_start, branch_end, node_counter, element_counter)

    def _text(self, text, font, color, center, *, width=None):
        item = self.scene().addSimpleText(text, font)
        item.setBrush(QColor(color))
        bounds = item.boundingRect()
        item.setPos(center.x() - (width or bounds.width()) / 2, center.y() - bounds.height() / 2)
        item.setZValue(2)
        return item

    def _draw_material(self, component, start, end, element_tag):
        width = self._sizes[id(component)][0] - 100
        center = (start + end) / 2
        rect = QRectF(center.x() - width / 2, center.y() - 25, width, 50)
        assigned = component.material_tag is not None
        stroke, fill = ("#b7cfee", "#edf4ff") if assigned else ("#efcf96", "#fff8ea")
        self._wire(start, QPointF(rect.left(), center.y()), "#64748b")
        self._wire(QPointF(rect.right(), center.y()), end, "#64748b")
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        card = self.scene().addPath(path, QPen(QColor(stroke), 1.2), QColor(fill))
        card.setData(0, "material")
        card.setData(1, component.name)
        label = component.name or f"E{element_tag:02d}"
        material_label = f"材料 M{component.material_tag}" if assigned else "尚未分配材料"
        card.setToolTip(f"{label}\n{material_label}\n长度权重 {component.length_weight:g}")
        display = QFontMetricsF(self._name_font).elidedText(label, Qt.TextElideMode.ElideRight, width - 24)
        name_item = self._text(display, self._name_font, "#254d7c" if assigned else "#986222", center + QPointF(0, -9))
        detail = self._text(material_label, self._detail_font, "#6882a1" if assigned else "#a77c42", center + QPointF(0, 12))
        name_item.setToolTip(card.toolTip())
        detail.setToolTip(card.toolTip())

    def _draw_node(self, point, tag, *, fixed=False, controlled=False):
        self.scene().addEllipse(point.x() - 5, point.y() - 5, 10, 10, QPen(QColor("#ffffff"), 2), QColor("#334155"))
        self._text(str(tag), self._detail_font, "#64748b", point + QPointF(0, 23))
        if fixed:
            self._wire(point + QPointF(-10, 10), point + QPointF(10, 10), "#dc706b")
        if controlled:
            self._wire(point + QPointF(-14, -19), point + QPointF(9, -19), "#399781")
            self._wire(point + QPointF(9, -19), point + QPointF(3, -24), "#399781")
            self._wire(point + QPointF(9, -19), point + QPointF(3, -14), "#399781")

    def _add_message(self, title, detail):
        self._text(title, self._name_font, "#52657c", QPointF(0, -18))
        # Wrap validation messages so long errors do not dominate the canvas.
        text = self.scene().addText(detail, self._detail_font)
        text.setDefaultTextColor(QColor("#8795a7"))
        text.setTextWidth(300)
        text.setPos(-150, 4)
