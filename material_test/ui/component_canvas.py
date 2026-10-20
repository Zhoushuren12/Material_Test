from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem,
                              QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView)


class ComponentCanvas(QGraphicsView):
    selected = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#fafbfc"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scene().selectionChanged.connect(self._selection_changed)
        self._project = None
        self.setMinimumSize(360, 180)

    def _selection_changed(self):
        items = self.scene().selectedItems()
        if items:
            kind, key = items[0].data(0), items[0].data(1)
            if kind and key:
                self.selected.emit(kind, key)

    def _selectable(self, item, kind, key, tooltip):
        from PySide6.QtWidgets import QGraphicsItem
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        item.setData(0, kind); item.setData(1, key); item.setToolTip(tooltip)
        return item

    def set_project(self, project, *, result=None, frame=0, amplification=1., refit=True):
        self._project = project
        self.scene().clear()
        nodes = {n["tag"]: n for n in project.data["nodes"]}
        span = max(max(n["x"] for n in nodes.values())-min(n["x"] for n in nodes.values()),
                   max(n["y"] for n in nodes.values())-min(n["y"] for n in nodes.values()), 1.)
        mark = span/90
        def position(key):
            n = nodes[key]
            x, y = n["x"], n["y"]
            if result is not None:
                x += amplification*result.channel(f"node_{key}_u1")[frame]
                y += amplification*result.channel(f"node_{key}_u2")[frame]
            return QPointF(x, -y)
        def label(text, p, color="#30383c", anchor=None, left=False, compact=None):
            item = self.scene().addSimpleText(text)
            item.setBrush(QColor(color))
            from PySide6.QtGui import QFont
            font = QFont("Microsoft YaHei UI", 9); font.setPixelSize(12)
            item.setFont(font)
            from PySide6.QtWidgets import QGraphicsItem
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setPos(p)
            item.setData(2, anchor if anchor is not None else p)
            item.setData(3,left)
            item.setData(4, text); item.setData(5, compact or text)
            return item
        for c in project.data.get("constraints", []):
            a, b = position(c["retained"]), position(c["constrained"])
            pen = QPen(QColor("#84928b"), 2., Qt.PenStyle.DashLine); pen.setCosmetic(True)
            self.scene().addLine(a.x(), a.y(), b.x(), b.y(), pen)
        palette = {"elasticBeamColumn": "#465051", "truss": "#c06a3d", "corotTruss": "#c06a3d", "zeroLength": "#b34857", "twoNodeLink": "#16896a"}
        for e in project.data["elements"]:
            a, b = position(e["i"]), position(e["j"])
            state = ''
            color = palette[e['type']]
            if result is not None:
                force_channels = [c for c in project.data['channels'] if c.get('quantity') == 'element' and c.get('element') == e['tag'] and c.get('response') in {'force','axialForce','basicForce'}]
                if force_channels:
                    channel = force_channels[0]
                    value = result.channel(channel['name'])[frame]
                    state = f"\n{channel['name']} = {value:.6g}"
                    color = '#b34857' if value > 1e-9 else '#217b9b' if value < -1e-9 else '#727d78'
            pen = QPen(QColor(color), 5. if e["type"] == "elasticBeamColumn" else 2.5)
            pen.setCosmetic(True)
            center = (a+b)/2
            display = e.get("display", {})
            if display.get("shape") == "plate":
                length, width = float(display["length"]), float(display["width"])
                outline = self.scene().addRect(center.x()-length/2, center.y()-width/2, length, width,
                                              QPen(QColor("#b34857"), 1), QColor("#f5e9ec"))
                outline.setToolTip("Physical outline metadata; zeroLength solver endpoints remain coincident")
            elif display.get("shape") == "pin":
                diameter = float(display.get("diameter", 4*mark))
                self.scene().addEllipse(center.x()-diameter/2, center.y()-diameter/2, diameter, diameter,
                                       QPen(QColor("#aa8740"), 1), QColor("#f2e7ce"))
            if math.hypot(b.x()-a.x(), b.y()-a.y()) < mark:
                path = QPainterPath()
                path.moveTo(center + QPointF(-3*mark, 0))
                for k in range(1, 8):
                    path.lineTo(center + QPointF((-3+6*k/8)*mark, (1 if k%2 else -1)*mark))
                path.lineTo(center + QPointF(3*mark, 0))
                item = self.scene().addPath(path, pen)
            else:
                item = self.scene().addLine(a.x(), a.y(), b.x(), b.y(), pen)
            self._selectable(item, "element", e["tag"], f"E{e['tag']} {e.get('name', '')}\n{e['type']} : N{e['i']} - N{e['j']}{state}")
            name = e.get('name','').split(':')[0][:24]
            label(f"E{e['tag']} {name}", center+QPointF(4*mark, -2*mark), palette[e["type"]],anchor=center,compact=f"E{e['tag']}")
        grouped = {}
        for n in nodes.values():
            p = position(n["tag"])
            key = (round(p.x(), 7), round(p.y(), 7))
            index = grouped.get(key, 0); grouped[key] = index+1
            item = self.scene().addEllipse(p.x()-mark/3, p.y()-mark/3, 2*mark/3, 2*mark/3,
                                          QPen(QColor("#ffffff"), 1), QColor("#16896a" if index else "#30383c"))
            self._selectable(item, "node", n["tag"], f"N{n['tag']} {n.get('name', '')}\nx={n['x']}, y={n['y']}\nfix={n.get('fix', [0,0,0])}")
            text = label(f"N{n['tag']}", p+QPointF(-7*mark if index == 0 else mark, (index+1)*mark),anchor=p,left=index==0)
            self._selectable(text, "node", n["tag"], item.toolTip())
            if any(n.get("fix", [])):
                support = QPainterPath(); support.moveTo(p+QPointF(0, mark))
                support.lineTo(p+QPointF(-mark, 3*mark)); support.lineTo(p+QPointF(mark, 3*mark)); support.closeSubpath()
                self.scene().addPath(support, QPen(QColor("#727d78"), 1), QColor("#e5ebe8"))
        self.setSceneRect(self.scene().itemsBoundingRect().adjusted(-8*mark, -8*mark, 8*mark, 8*mark))
        if refit:
            self.fit_all()
        else:
            self._arrange_labels()

    def fit_all(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._arrange_labels()

    def _arrange_labels(self):
        labels = [i for i in self.scene().items() if isinstance(i,QGraphicsSimpleTextItem)]
        protected = [i.deviceTransform(self.viewportTransform()).mapRect(i.boundingRect()).adjusted(-2,-2,2,2)
                     for i in self.scene().items()
                     if isinstance(i,(QGraphicsLineItem,QGraphicsPathItem,QGraphicsEllipseItem,QGraphicsRectItem))]
        viewport = self.viewport().rect().adjusted(3,3,-3,-3)
        placed = []
        for item in labels:
            compact = self.viewport().height() < 240
            item.setText(item.data(5 if compact else 4))
            anchor = self.mapFromScene(item.data(2))
            width,height = item.boundingRect().width(),item.boundingRect().height()
            horizontal = [-width-8,8] if item.data(3) else [8,-width-8]
            offsets = [(dx + (-shift if dx < 0 else shift),dy)
                       for shift in (0,16,40,80,128)
                       for dy in [v for level in range(8,169,16) for v in (level,-height-level)]
                       for dx in horizontal]
            offsets.sort(key=lambda p: abs(p[0]+width/2)+abs(p[1]+height/2))
            found = False
            for dx,dy in offsets:
                position = QPointF(anchor.x()+dx,anchor.y()+dy).toPoint()
                box = item.boundingRect().translated(position).adjusted(-3,-2,3,2)
                if viewport.contains(box.toAlignedRect()) and not any(box.intersects(other) for other in protected+placed):
                    item.setPos(self.mapToScene(position)); found=True; break
            if not found:
                # Dense assemblies can exhaust nearby slots; use free viewport space.
                for y in range(8,viewport.bottom()-int(height),int(height)+8):
                    for x in range(8,viewport.right()-int(width),16):
                        position = QPointF(x,y).toPoint()
                        box = item.boundingRect().translated(position).adjusted(-3,-2,3,2)
                        if not any(box.intersects(other) for other in protected+placed):
                            item.setPos(self.mapToScene(position)); found=True; break
                    if found: break
            if not found:
                box = item.deviceTransform(self.viewportTransform()).mapRect(item.boundingRect())
            placed.append(box)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1/1.15
        self.scale(factor, factor)
        self._arrange_labels()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._project is not None:
            self.fit_all()
