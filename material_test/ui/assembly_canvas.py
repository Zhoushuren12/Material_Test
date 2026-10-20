"""Mouse-driven editing of explicit support nodes and elements."""
import math
from collections import Counter, defaultdict

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView


def display_positions(graph, *, expanded=False, spacing=64.):
    """Separate coincident connection nodes without changing the physical graph."""
    nodes = {n['tag']: n for n in graph['nodes']}
    coords = {tag: QPointF(n['x'], -n['y']) for tag, n in nodes.items()}
    positions = dict(coords)
    if not expanded:
        return positions
    groups, neighbours = defaultdict(list), defaultdict(list)
    for tag, p in coords.items():
        groups[(round(p.x(), 8), round(p.y(), 8))].append(tag)
    for e in graph['elements']:
        delta = coords[e['j']] - coords[e['i']]
        length = math.hypot(delta.x(), delta.y())
        if length > 1e-8:
            neighbours[e['i']].append(delta/length)
            neighbours[e['j']].append(-delta/length)

    def direction(tag):
        vectors = neighbours[tag]
        vector = sum(vectors, QPointF())
        length = math.hypot(vector.x(), vector.y())
        return vector/length if length > 1e-8 else (vectors[0] if vectors else QPointF())

    for group in groups.values():
        if len(group) < 2:
            continue
        tags = sorted(group)
        supported = [tag for tag in tags if any(nodes[tag]['fix'])]
        # A single support anchors the schematic connection; its partner moves.
        anchor = supported[0] if len(supported) == 1 else None
        if len(tags) == 2:
            a, b = tags
            vector = direction(b) - direction(a)
            length = math.hypot(vector.x(), vector.y())
            vector = vector/length if length > 1e-8 else QPointF(1., 0.)
            positions[a] = coords[a] - vector*(spacing if anchor == b else spacing/2)
            positions[b] = coords[b] + vector*(spacing if anchor == a else spacing/2)
            if anchor is not None:
                positions[anchor] = coords[anchor]
        else:
            moving = [tag for tag in tags if tag != anchor]
            moving.sort(key=lambda tag: (math.atan2(direction(tag).y(), direction(tag).x()), tag))
            radius = spacing/max(1e-8, 2*math.sin(math.pi/len(moving)))
            first = direction(moving[0])
            angle = math.atan2(first.y(), first.x())
            for index, tag in enumerate(moving):
                theta = angle + 2*math.pi*index/len(moving)
                positions[tag] = coords[tag] + QPointF(math.cos(theta), math.sin(theta))*radius
    return positions


class AssemblyCanvas(QGraphicsView):
    selection_changed = Signal()
    position_changed = Signal(float, float)

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setScene(QGraphicsScene(self))
        self.setObjectName('assemblyCanvas')
        self.setBackgroundBrush(QColor('#fbfcfc'))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.mode = 'select'
        self.selection = set()
        self.positions = {}
        self.start_node = None
        self._drag = self._rubber = self._pan = None
        self._draw_press = self._draw_pointer = None
        self._before = None
        self._auto_fit = True
        self.setSceneRect(-400, -400, 1600, 1200)
        self.scale(.6, .6)
        self.centerOn(250, 0)

    def set_mode(self, mode):
        self.mode = mode; self.start_node = None
        self._draw_press = self._draw_pointer = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if mode == 'pan' else
                       Qt.CursorShape.ArrowCursor if mode == 'select' else Qt.CursorShape.CrossCursor)
        self.render()

    def point(self, position, *, snap=True):
        p = self.mapToScene(position)
        x, y = p.x(), -p.y()
        if snap and self.editor.snap_check.isChecked():
            grid = self.editor.grid_spin.value()
            x, y = round(x/grid)*grid, round(y/grid)*grid
        return x, y

    def hit(self, position):
        hits = [(item.data(0), item.data(1)) for item in self.items(position) if item.data(0) in ('node', 'element')]
        nodes = [hit for hit in hits if hit[0] == 'node']
        return (nodes or hits or [None])[0]

    def node_at(self, position):
        p = self.mapToScene(position)
        tolerance = 12 / max(self.transform().m11(), .001)
        candidates = []
        for node in self.editor.graph['nodes']:
            delta = self.positions.get(node['tag'], QPointF(node['x'], -node['y'])) - p
            if math.hypot(delta.x(), delta.y()) < tolerance:
                candidates.append(node)
        selected = [n for n in candidates if ('node', n['tag']) in self.selection]
        return (selected or candidates or [None])[0]

    def select(self, kind, tag, *, additive=False):
        if not additive: self.selection.clear()
        key = (kind, tag)
        if additive and key in self.selection: self.selection.remove(key)
        else: self.selection.add(key)
        self.render(); self.selection_changed.emit()

    def mousePressEvent(self, event):
        self.setFocus()
        p = event.position().toPoint()
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and self.mode == 'pan'):
            self._pan = p; self.setCursor(Qt.CursorShape.ClosedHandCursor); return
        if event.button() == Qt.MouseButton.RightButton:
            self.start_node = None; self.editor.select_action.trigger(); return
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        if self.mode == 'node':
            self.editor.begin_edit()
            x, y = self.point(p)
            n = self.editor.create_node(x, y)
            self.selection = {('node', n['tag'])}
            self.editor.end_edit(); self.selection_changed.emit(); return
        if self.mode == 'element':
            self._draw_press = p if self.start_node is None else None
            self._element_click(p)
            return
        self._select_press(event,p)

    def _element_click(self,p):
        self.editor.begin_edit()
        node = self.node_at(p)
        if self.start_node is None:
            if node is None:
                node = self.editor.create_node(*self.point(p))
            self.start_node = node['tag']
            self.editor.end_edit(); self.render(); return
        if self.editor.element_type.currentData() == 'zeroLength':
            first = self.editor.node(self.start_node)
            if node is None or node['tag'] == self.start_node:
                node = self.editor.create_node(first['x'], first['y'])
            elif math.hypot(first['x']-node['x'], first['y']-node['y']) > 1e-8:
                self.editor.end_edit(); self.editor.message.setText('zeroLength 端点必须重合'); return
        elif node is None:
            node = self.editor.create_node(*self.point(p))
        if node['tag'] != self.start_node:
            e = self.editor.create_element(self.start_node, node['tag'])
            self.selection = {('element', e['tag'])}; self.start_node = None
        self.editor.end_edit(); self.selection_changed.emit()

    def _select_press(self,event,p):
        hit = self.hit(p)
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if hit:
            if hit not in self.selection or ctrl: self.select(*hit, additive=ctrl)
            tags = {tag for kind, tag in self.selection if kind == 'node'}
            for kind, tag in self.selection:
                if kind == 'element':
                    e = self.editor.element(tag); tags.update((e['i'], e['j']))
            # Coincident zeroLength pairs move as a unit; their DOFs stay separate.
            tags = self.editor.coincident_group(tags)
            self._drag = (self.point(p, snap=False), {tag: (self.editor.node(tag)['x'], self.editor.node(tag)['y']) for tag in tags})
            self.editor.begin_edit()
        else:
            if not ctrl: self.selection.clear(); self.selection_changed.emit()
            self._rubber = (self.mapToScene(p), self.mapToScene(p))
        self.render()

    def mouseMoveEvent(self, event):
        p = event.position().toPoint()
        node = self.node_at(p)
        self.position_changed.emit(node['x'], node['y']) if node is not None else self.position_changed.emit(*self.point(p))
        if self.mode=='element' and self.start_node is not None:
            x,y = self.point(p); self._draw_pointer = QPointF(x,-y); self.render()
        if self._pan is not None:
            delta = p-self._pan
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-delta.y())
            self._pan = p; self._auto_fit = False; return
        if self._drag is not None:
            start, nodes = self._drag; x, y = self.point(p, snap=False)
            dx, dy = x-start[0], y-start[1]
            if self.editor.snap_check.isChecked():
                grid = self.editor.grid_spin.value(); dx, dy = round(dx/grid)*grid, round(dy/grid)*grid
            for tag, (nx, ny) in nodes.items():
                n = self.editor.node(tag); n['x'], n['y'] = nx+dx, ny+dy
            self.render(); return
        if self._rubber is not None:
            self._rubber = (self._rubber[0], self.mapToScene(p)); self.render(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._draw_press is not None:
            if (event.position().toPoint()-self._draw_press).manhattanLength()>12:
                self._element_click(event.position().toPoint())
            self._draw_press = None; self._draw_pointer = None; self.render(); return
        if self._pan is not None:
            self._pan = None; self.setCursor(Qt.CursorShape.OpenHandCursor if self.mode == 'pan' else Qt.CursorShape.ArrowCursor); return
        if self._drag is not None:
            self._drag = None; self.editor.end_edit(); self.selection_changed.emit(); return
        if self._rubber is not None:
            rect = QRectF(*self._rubber).normalized()
            for node in self.editor.graph['nodes']:
                if rect.contains(self.positions[node['tag']]):
                    self.selection.add(('node', node['tag']))
            for element in self.editor.graph['elements']:
                if {('node', element['i']), ('node', element['j'])}.issubset(self.selection):
                    self.selection.add(('element', element['tag']))
            self._rubber = None; self.render(); self.selection_changed.emit(); return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete: self.editor.delete_selected(); return
        if event.key() == Qt.Key.Key_Escape: self.start_node = None; self.editor.select_action.trigger(); return
        if event.matches(QKeySequence.StandardKey.Undo): self.editor.undo(); return
        if event.matches(QKeySequence.StandardKey.Redo): self.editor.redo(); return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1/1.15
        if .03 < self.transform().m11()*factor < 30:
            self.scale(factor, factor); self._auto_fit = False; self.render()
        event.accept()

    def fit_all(self):
        nodes = self.editor.graph['nodes']
        if nodes:
            xs, ys = [n['x'] for n in nodes], [-n['y'] for n in nodes]
            width,height = max(xs)-min(xs),max(ys)-min(ys)
            margin = max(100,100*max(width/max(100,self.viewport().width()-200),height/max(100,self.viewport().height()-160)))
            rect = QRectF(min(xs),min(ys),width,height).adjusted(-margin,-margin,margin,margin)
            self.setSceneRect(self.sceneRect().united(rect.adjusted(-margin,-margin,margin,margin)))
            self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.resetTransform(); self.scale(.6, .6); self.centerOn(250, 0)
        self._auto_fit = True; self.render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_fit and hasattr(self.editor, 'graph'): self.fit_all()

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        step = self.editor.grid_spin.value()
        while step*self.transform().m11() < 18: step *= 2
        if rect.width()/step > 300 or rect.height()/step > 300: return
        pen = QPen(QColor('#e7edeb'), 1); pen.setCosmetic(True); painter.setPen(pen)
        x = math.floor(rect.left()/step)*step
        while x < rect.right(): painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom())); x += step
        y = math.floor(rect.top()/step)*step
        while y < rect.bottom(): painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y)); y += step
        pen.setColor(QColor('#cedcd6')); painter.setPen(pen)
        painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0))

    def render(self):
        self.scene().clear()
        scale = max(self.transform().m11(), .03)
        nodes = {n['tag']: n for n in self.editor.graph['nodes']}
        expand = getattr(self.editor, 'expand_check', None)
        self.positions = display_positions(self.editor.graph, expanded=bool(expand and expand.isChecked()), spacing=64/scale)
        if nodes:
            xs,ys = [p.x() for p in self.positions.values()],[p.y() for p in self.positions.values()]
            bounds = QRectF(min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys)).adjusted(-250,-250,250,250)
            if not self.sceneRect().contains(bounds):
                self.setSceneRect(self.sceneRect().united(bounds))
        def pos(tag):
            return self.positions[tag]
        def pen(color, width=2, style=Qt.PenStyle.SolidLine):
            p = QPen(QColor(color), width, style); p.setCosmetic(True); return p
        def label(text, p, color, kind=None, tag=None):
            item = self.scene().addSimpleText(text, QFont('Microsoft YaHei UI', 9))
            item.setBrush(QColor(color)); item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            item.setPos(p); item.setData(0, kind); item.setData(1, tag); return item
        grouped = {}
        group_sizes = Counter(tuple(sorted((e['i'],e['j']))) for e in self.editor.graph['elements'])
        palette = {'twoNodeLink': '#167d70', 'zeroLength': '#b34857', 'truss': '#377eb2', 'corotTruss': '#377eb2', 'elasticBeamColumn': '#59646a'}
        for e in self.editor.graph['elements']:
            a, b = pos(e['i']), pos(e['j']); pair = tuple(sorted((e['i'], e['j'])))
            index = grouped.get(pair, 0); grouped[pair] = index+1
            selected = ('element', e['tag']) in self.selection
            color = '#e09a23' if selected else palette[e['type']]
            if any(t is None for t in self.editor.element_material_tags(e)): color = '#b45d55' if not selected else color
            center = (a+b)/2
            path = QPainterPath(a)
            if (b-a).manhattanLength() < 1e-8:
                radius = (18+index*14)/scale
                path.cubicTo(a+QPointF(-radius, -radius), a+QPointF(radius, -radius), b)
                center = a+QPointF(0, -radius)
            elif index:
                dx, dy = b.x()-a.x(), b.y()-a.y(); length = math.hypot(dx, dy)
                middle = center+QPointF(-dy/length, dx/length)*(35*index/scale)
                path.quadTo(middle, b); center = (center+middle)/2
            else:
                if e['type'] in ('twoNodeLink', 'zeroLength'):
                    dx, dy = b.x()-a.x(), b.y()-a.y(); length = math.hypot(dx, dy)
                    normal = QPointF(-dy/length, dx/length)*5/scale
                    path.lineTo(a+(b-a)*.35)
                    for k in range(1, 8): path.lineTo(a+(b-a)*(.35+.3*k/8)+normal*(1 if k%2 else -1))
                    path.lineTo(a+(b-a)*.65)
                path.lineTo(b)
            item = self.scene().addPath(path, pen(color, 4 if selected or e['type']=='elasticBeamColumn' else 2.5))
            item.setData(0, 'element'); item.setData(1, e['tag'])
            text = f"E{e['tag']}" + (' · '+','.join(str(t) if t is not None else '?' for t in self.editor.element_material_tags(e)) if e['type'] != 'elasticBeamColumn' else '')
            link = e['type'] in ('twoNodeLink','zeroLength')
            label_y = -24 if not link or (index==0 and group_sizes[pair]>1) else 10+18*index
            label_position = center+QPointF(8/scale,label_y/scale)
            if (b-a).manhattanLength()<1e-8:
                label_position = a+QPointF(24/scale,-(24+index*20)/scale)
            element_label = label(text,label_position,color,'element',e['tag'])
            if expand and expand.isChecked() and e['type'] == 'elasticBeamColumn':
                delta = b-a
                # Keep short member labels clear of the expanded endpoint labels.
                if abs(delta.x()) < abs(delta.y()):
                    element_label.setPos(center+QPointF(-(element_label.boundingRect().width()+8)/scale,-7/scale))
                elif delta.x()*delta.y() < 0 and abs(delta.y())*4 > abs(delta.x()):
                    element_label.setPos(center+QPointF(8/scale,18/scale))
            item.setToolTip(f"{e.get('name', text)}\n{e['type']}  N{e['i']} → N{e['j']}")
        for c in self.editor.graph.get('constraints', []):
            a,b = pos(c['retained']),pos(c['constrained'])
            self.scene().addLine(a.x(), a.y(), b.x(), b.y(), pen('#8a9690', 1.5, Qt.PenStyle.DashLine))
        coincident = {}
        for n in nodes.values():
            p = pos(n['tag']); key = (p.x(), p.y()); index = coincident.get(key, 0); coincident[key] = index+1
            radius = 5/scale; color = '#e09a23' if ('node', n['tag']) in self.selection else '#283f36'
            item = self.scene().addEllipse(p.x()-radius, p.y()-radius, 2*radius, 2*radius, pen('#ffffff', 1.5), QColor(color))
            item.setData(0, 'node'); item.setData(1, n['tag']); item.setZValue(10)
            text = label(f"N{n['tag']}", p+QPointF(8/scale, (8+index*18)/scale), color, 'node', n['tag'])
            text.setZValue(11)
            tooltip = f"N{n['tag']}\nX {n['x']:g}  Y {n['y']:g}"
            item.setToolTip(tooltip); text.setToolTip(tooltip)
            if any(n['fix']):
                size = 11/scale
                path = QPainterPath(p+QPointF(0,size/2)); path.lineTo(p+QPointF(-size,size*1.5)); path.lineTo(p+QPointF(size,size*1.5)); path.closeSubpath()
                self.scene().addPath(path, pen('#71867d', 1), QColor('#dce8e2'))
        control = self.editor.graph['control']
        if control.get('node') in nodes:
            p = pos(control['node']); dof = control['dof']; length = 55/scale
            if dof==3:
                radius = 28/scale
                arc = QPainterPath(); arc.arcMoveTo(QRectF(p.x()-radius,p.y()-radius,2*radius,2*radius),20)
                arc.arcTo(QRectF(p.x()-radius,p.y()-radius,2*radius,2*radius),20,270)
                self.scene().addPath(arc,pen('#be6f20',2.5)); end = arc.currentPosition()
                direction = QPointF(.94,-.34)
            else:
                direction = QPointF(1,0) if dof==1 else QPointF(0,-1)
                end = p+direction*length
                self.scene().addLine(p.x(), p.y(), end.x(), end.y(), pen('#be6f20', 2.5))
            path = QPainterPath(end); normal = QPointF(-direction.y(),direction.x())*5/scale
            path.lineTo(end-direction*12/scale+normal); path.lineTo(end-direction*12/scale-normal); path.closeSubpath()
            self.scene().addPath(path, pen('#be6f20'), QColor('#be6f20'))
            label(('UX','UY','RZ')[dof-1], end+QPointF(7/scale,-14/scale), '#a55c14')
        if self.start_node in nodes:
            p = pos(self.start_node); r = 10/scale
            self.scene().addEllipse(p.x()-r,p.y()-r,2*r,2*r,pen('#167d70',2))
            if self._draw_pointer is not None:
                end = self._draw_pointer
                self.scene().addLine(p.x(),p.y(),end.x(),end.y(),pen('#167d70',1.5,Qt.PenStyle.DashLine))
        if self._rubber is not None:
            self.scene().addRect(QRectF(*self._rubber).normalized(), pen('#167d70',1), QColor(22,125,112,30))
