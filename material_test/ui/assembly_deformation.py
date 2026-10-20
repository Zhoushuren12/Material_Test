"""Read-only playback of the solved assembly's nodal displacements."""
import math
from collections import defaultdict

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QGraphicsItem, QHBoxLayout, QLabel, QMessageBox, QSlider,
    QSpinBox, QStyle, QToolButton, QVBoxLayout)

from material_test.ui.component_canvas import ComponentCanvas


def pen(color, width=2., style=Qt.PenStyle.SolidLine):
    value = QPen(QColor(color), width, style)
    value.setCosmetic(True)
    return value


class DeformationCanvas(ComponentCanvas):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = None
        self.selected_tag = None
        self.positions = {}
        self.rotation_items = {}
        self._rotating_tags = set()
        self._bounds = QRectF()
        self.setObjectName('assemblyCanvas')
        self.setToolTip('节点位移与转角回放；单元以变形后端点连线表示，不表示内部曲率')

    def clear(self):
        self.history = self._project = None
        self.positions.clear()
        self.rotation_items.clear()
        self.scene().clear()

    def set_history(self, history):
        self.history = self._project = history
        self.selected_tag = history.model['loading']['node']
        self._rotating_tags = {tag for index, tag in enumerate(history.node_tags)
                               if np.any(np.abs(history.displacements[:, index, 2]) > 1e-12)}
        self.set_amplification(history.default_amplification)

    def set_amplification(self, amplification):
        if self.history is None:
            return
        h = self.history
        lower = np.minimum(h.coordinates, h.coordinates + amplification * h.displacements[:, :, :2].min(axis=0)).min(axis=0)
        upper = np.maximum(h.coordinates, h.coordinates + amplification * h.displacements[:, :, :2].max(axis=0)).max(axis=0)
        margin = h.span * .12
        self._bounds = QRectF(lower[0]-margin, -upper[1]-margin,
                             upper[0]-lower[0]+2*margin, upper[1]-lower[1]+2*margin)

    def _label(self, text, point, color):
        label = self.scene().addSimpleText(text)
        font = QFont('Microsoft YaHei UI'); font.setPixelSize(12)
        label.setFont(font); label.setBrush(QColor(color))
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        label.setPos(point)
        for index, value in ((2, point), (3, False), (4, text), (5, text)):
            label.setData(index, value)

    def show_frame(self, frame, amplification, *, undeformed=True, labels=True, refit=False):
        if self.history is None:
            return
        h = self.history
        self.scene().clear(); self.rotation_items.clear()
        original = {tag: QPointF(x, -y) for tag, (x, y) in zip(h.node_tags, h.coordinates)}
        self.positions = {tag: QPointF(x, -y) for tag, (x, y) in zip(h.node_tags, h.positions(frame, amplification))}
        lookup = {tag: index for index, tag in enumerate(h.node_tags)}
        mark = h.span / 75.
        if undeformed:
            for element in h.model['elements']:
                a, b = original[element['i']], original[element['j']]
                self.scene().addLine(a.x(), a.y(), b.x(), b.y(), pen('#b5bfbd', 2., Qt.PenStyle.DashLine))
            for p in original.values():
                self.scene().addEllipse(p.x()-mark/3, p.y()-mark/3, 2*mark/3, 2*mark/3,
                                       pen('#b5bfbd', 1.), QColor('#ffffff'))
        for constraint in h.model.get('constraints', []):
            a, b = self.positions[constraint['retained']], self.positions[constraint['constrained']]
            self.scene().addLine(a.x(), a.y(), b.x(), b.y(), pen('#8caaa7', 1.5, Qt.PenStyle.DotLine))
        palette = {'elasticBeamColumn': '#347d88', 'twoNodeLink': '#16896a',
                   'zeroLength': '#b34857', 'truss': '#9f743f', 'corotTruss': '#9f743f'}
        for element in h.model['elements']:
            a, b = self.positions[element['i']], self.positions[element['j']]
            color = palette[element['type']]
            shape = QPainterPath(a); shape.lineTo(b)
            if element['type'] in {'zeroLength', 'twoNodeLink'}:
                center = (a+b)/2
                delta = b-a
                length = math.hypot(delta.x(), delta.y())
                axis = delta/length if length > 1e-12 else QPointF(1., 0.)
                normal = QPointF(-axis.y(), axis.x())
                half = min(3*mark, length*.35) if length > mark else 2*mark
                shape.moveTo(center-axis*half)
                for k in range(1, 8):
                    shape.lineTo(center + axis*((k/8*2-1)*half) + normal*(mark*.6*(1 if k%2 else -1)))
                shape.lineTo(center+axis*half)
            item = self.scene().addPath(shape, pen(color, 3. if element['type']=='elasticBeamColumn' else 2.))
            relative = h.displacements[frame, lookup[element['j']]] - h.displacements[frame, lookup[element['i']]]
            unit = h.model['units']['length']
            item.setToolTip(f"E{element['tag']} · {element['type']} · N{element['i']} → N{element['j']}\n"
                            f'端点 ΔUX {relative[0]:.6g} {unit} · ΔUY {relative[1]:.6g} {unit}\nΔRZ {relative[2]:.6g} rad')
        groups = defaultdict(int)
        for node in h.model['nodes']:
            tag = node['tag']; p = self.positions[tag]
            key = (round(original[tag].x(), 8), round(original[tag].y(), 8))
            index = groups[key]; groups[key] += 1
            ux, uy, rz = h.displacements[frame, lookup[tag]]
            color = '#bd772c' if tag == self.selected_tag else '#283638' if index == 0 else '#b34857'
            item = self.scene().addEllipse(p.x()-mark/3, p.y()-mark/3, 2*mark/3, 2*mark/3,
                                          pen('#ffffff', 1.), QColor(color))
            self._selectable(item, 'node', tag, f'N{tag}\nUX {ux:.6g} · UY {uy:.6g} {h.model["units"]["length"]}\nRZ {rz:.6g} rad')
            # Concentric orientation glyphs expose independent coincident rotations.
            if tag in self._rotating_tags:
                radius = mark*(1.7+index*1.2)
                theta = amplification*rz
                end = p+QPointF(math.cos(theta), -math.sin(theta))*radius
                arc = QPainterPath(p+QPointF(radius, 0.))
                arc.arcTo(QRectF(p.x()-radius, p.y()-radius, 2*radius, 2*radius), 0., math.degrees(theta))
                arc.moveTo(p); arc.lineTo(end)
                self.rotation_items[tag] = self.scene().addPath(arc, pen(color, 1.7))
                if undeformed:
                    self.scene().addLine(p.x(), p.y(), p.x()+radius, p.y(), pen('#b5bfbd', 1., Qt.PenStyle.DashLine))
            if labels:
                self._label(f'N{tag}', p+QPointF(mark, (index+1)*mark), color)
            if any(node.get('fix', [])):
                support = QPainterPath(p+QPointF(0., mark))
                support.lineTo(p+QPointF(-mark, 3*mark)); support.lineTo(p+QPointF(mark, 3*mark)); support.closeSubpath()
                self.scene().addPath(support, pen('#859590', 1.), QColor('#edf1ef'))
            item.setZValue(index+1)
        self.setSceneRect(self._bounds)
        if refit:
            self.fit_all()
        else:
            self._arrange_labels()


class AssemblyDeformationDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('阻尼器变形图'); self.resize(1040, 720); self.setMinimumSize(680, 500)
        self.history = None; self.valid = False
        self.timer = QTimer(self); self.timer.timeout.connect(self._advance_frame)
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 14, 16, 14); layout.setSpacing(10)
        heading = QHBoxLayout()
        self.title = QLabel('节点变形'); self.title.setObjectName('sectionTitle'); self.title.setWordWrap(True)
        heading.addWidget(self.title, 1)
        self.fit_button = self._tool('适配视图', QStyle.StandardPixmap.SP_TitleBarMaxButton, self.fit_view)
        self.export_button = self._tool('导出当前变形图', QStyle.StandardPixmap.SP_DialogSaveButton, self.export_current)
        heading.addWidget(self.fit_button); heading.addWidget(self.export_button); layout.addLayout(heading)
        settings = QHBoxLayout()
        settings.addWidget(QLabel('变形倍率'))
        self.amplification = QDoubleSpinBox(); self.amplification.setDecimals(3)
        self.amplification.setRange(.001, 1e6); self.amplification.setValue(1.); self.amplification.setFixedWidth(118)
        self.amplification.valueChanged.connect(self._scale_changed); settings.addWidget(self.amplification)
        self.auto_button = self._tool('自动变形倍率', QStyle.StandardPixmap.SP_BrowserReload, self.auto_scale)
        settings.addWidget(self.auto_button)
        self.undeformed = QCheckBox('未变形轮廓'); self.undeformed.setChecked(True)
        self.node_labels = QCheckBox('节点编号'); self.node_labels.setChecked(True)
        for field in (self.undeformed, self.node_labels):
            field.toggled.connect(self._show_frame); settings.addWidget(field)
        settings.addStretch(1); layout.addLayout(settings)
        self.canvas = DeformationCanvas(); self.canvas.selected.connect(self._select_node)
        layout.addWidget(self.canvas, 1)
        inspection = QHBoxLayout()
        self.node_selector = QComboBox(); self.node_selector.setFixedWidth(105)
        self.node_selector.currentIndexChanged.connect(self._node_changed)
        inspection.addWidget(self.node_selector)
        self.node_values = QLabel(); self.node_values.setWordWrap(True); inspection.addWidget(self.node_values, 1)
        layout.addLayout(inspection)
        playback = QHBoxLayout()
        self.first_button = self._tool('第一步', QStyle.StandardPixmap.SP_MediaSkipBackward, lambda: self.seek(0))
        self.previous_button = self._tool('上一步', QStyle.StandardPixmap.SP_MediaSeekBackward, lambda: self.seek(self.frame.value()-1))
        self.play_button = self._tool('播放', QStyle.StandardPixmap.SP_MediaPlay, self.toggle_play)
        self.next_button = self._tool('下一步', QStyle.StandardPixmap.SP_MediaSeekForward, lambda: self.seek(self.frame.value()+1))
        self.last_button = self._tool('最后一步', QStyle.StandardPixmap.SP_MediaSkipForward, lambda: self.seek(self.frame.maximum()))
        for button in (self.first_button, self.previous_button, self.play_button, self.next_button, self.last_button): playback.addWidget(button)
        self.frame = QSlider(Qt.Orientation.Horizontal); self.frame.setRange(0, 0)
        self.frame.valueChanged.connect(self._show_frame); self.frame.sliderPressed.connect(self.stop)
        playback.addWidget(self.frame, 1)
        self.frame_number = QSpinBox(); self.frame_number.setRange(1, 1); self.frame_number.setFixedWidth(78)
        self.frame_number.valueChanged.connect(lambda value: self.seek(value-1)); playback.addWidget(self.frame_number)
        self.frame_total = QLabel('/ 0'); playback.addWidget(self.frame_total)
        self.speed = QComboBox(); self.speed.setFixedWidth(82)
        for value in (.25, .5, 1., 2., 4.): self.speed.addItem(f'{value:g}×', value)
        self.speed.setCurrentIndex(2); self.speed.setToolTip('播放速度'); self.speed.currentIndexChanged.connect(self._set_interval)
        playback.addWidget(self.speed); layout.addLayout(playback)
        self.status = QLabel('等待计算'); self.status.setWordWrap(True); self.status.setObjectName('runState')
        layout.addWidget(self.status)
        self._controls = (self.fit_button, self.export_button, self.auto_button, self.amplification,
                          self.undeformed, self.node_labels, self.node_selector, self.first_button,
                          self.previous_button, self.play_button, self.next_button, self.last_button, self.frame,
                          self.frame_number, self.speed)
        self.clear()

    def _tool(self, title, icon, callback):
        button = QToolButton(); button.setObjectName('canvasTool')
        button.setIcon(self.style().standardIcon(icon)); button.setToolTip(title); button.setAccessibleName(title)
        button.clicked.connect(callback)
        return button

    def _enable_controls(self):
        for control in self._controls: control.setEnabled(self.valid)

    def set_history(self, history):
        self.stop(); self.history = history; self.valid = True
        self.title.setText(history.model['name'] + ' · 节点变形')
        self.canvas.set_history(history)
        for field in (self.frame, self.frame_number, self.amplification, self.node_selector): field.blockSignals(True)
        self.frame.setRange(0, history.frame_count-1); self.frame.setValue(0)
        self.frame_number.setRange(1, history.frame_count); self.frame_number.setValue(1)
        self.frame_total.setText(f'/ {history.frame_count}')
        self.amplification.setValue(history.default_amplification)
        self.node_selector.clear()
        for tag in history.node_tags: self.node_selector.addItem(f'N{tag}', tag)
        self.node_selector.setCurrentIndex(self.node_selector.findData(history.model['loading']['node']))
        for field in (self.frame, self.frame_number, self.amplification, self.node_selector): field.blockSignals(False)
        self._enable_controls(); self._set_interval(); self._show_frame(refit=True)

    def invalidate(self, message):
        self.stop(); self.valid = False; self._enable_controls(); self.status.setText(message)

    def clear(self, message='等待计算'):
        self.invalidate(message); self.history = None; self.canvas.clear()
        self.title.setText('节点变形'); self.node_selector.clear(); self.node_values.clear()
        self.frame.setRange(0, 0); self.frame_total.setText('/ 0')

    def _show_frame(self, *args, refit=False):
        if not self.valid: return
        index = self.frame.value()
        self.frame_number.blockSignals(True); self.frame_number.setValue(index+1); self.frame_number.blockSignals(False)
        self._inspect_node()
        self.canvas.show_frame(index, self.amplification.value(), undeformed=self.undeformed.isChecked(),
                               labels=self.node_labels.isChecked(), refit=refit)
        h = self.history; loading = h.model['loading']; units = h.model['units']
        dof = loading['dof']; name = ('UX', 'UY', 'RZ')[dof-1]
        length = 'rad' if dof == 3 else units['length']
        force = f"{units['force']}·{units['length']}" if dof == 3 else units['force']
        clock = f't = {h.timeline[index]:.6g} s · ' if loading.get('analysis') == 'transient' else ''
        self.status.setText(f"{clock}控制 N{loading['node']} · {name} {h.control_displacement[index]:.6g} {length} · "
                            f"{'M' if dof == 3 else 'F'} {h.control_force[index]:.6g} {force}")

    def _inspect_node(self, *args):
        if not self.valid: return
        tag = self.node_selector.currentData()
        if tag is None: return
        index = self.history.node_tags.index(tag)
        ux, uy, rz = self.history.displacements[self.frame.value(), index]
        unit = self.history.model['units']['length']
        self.node_values.setText(f'UX {ux:.6g} {unit}    UY {uy:.6g} {unit}    RZ {rz:.6g} rad')
        self.canvas.selected_tag = tag

    def _node_changed(self, *args):
        self._inspect_node()
        # A scene selection signal can arrive inside the clicked item's mouse event.
        QTimer.singleShot(0, self._show_frame)

    def _select_node(self, kind, tag):
        if kind == 'node': self.node_selector.setCurrentIndex(self.node_selector.findData(tag))

    def _scale_changed(self, *args):
        if self.valid:
            self.canvas.set_amplification(self.amplification.value()); self._show_frame(refit=True)

    def auto_scale(self):
        if self.valid: self.amplification.setValue(self.history.default_amplification)

    def fit_view(self):
        if self.valid: self.canvas.fit_all()

    def seek(self, index):
        self.stop()
        if self.valid: self.frame.setValue(max(0, min(index, self.frame.maximum())))

    def _set_interval(self, *args):
        if self.history is None: return
        loading = self.history.model['loading']
        seconds = float(loading['dt']) if loading.get('analysis') == 'transient' else .04
        self.timer.setInterval(max(1, min(2147483647, round(1000*seconds/self.speed.currentData()))))

    def toggle_play(self):
        if not self.valid: return
        if self.timer.isActive():
            self.stop(); return
        if self.frame.value() == self.frame.maximum(): self.frame.setValue(0)
        self.timer.start()
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)); self.play_button.setToolTip('暂停')

    def stop(self):
        self.timer.stop()
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)); self.play_button.setToolTip('播放')

    def _advance_frame(self):
        if not self.valid: self.stop(); return
        self.frame.setValue(min(self.frame.value()+1, self.frame.maximum()))
        if self.frame.value() == self.frame.maximum(): self.stop()

    def export_image(self, path):
        if not self.valid: return False
        self.stop()
        diagram = self.canvas.viewport().grab().toImage()
        image = QImage(diagram.width(), diagram.height()+88, QImage.Format.Format_ARGB32)
        image.fill(QColor('#ffffff'))
        painter = QPainter(image)
        font = QFont('Microsoft YaHei UI'); font.setPixelSize(14); painter.setFont(font); painter.setPen(QColor('#283638'))
        caption = (f'{self.title.text()}\n{self.status.text()}\n'
                   f'加载点 {self.frame.value()+1}/{self.history.frame_count} · 变形倍率 {self.amplification.value():g}×')
        painter.drawText(QRectF(12, 5, image.width()-24, 78), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, caption)
        painter.drawImage(0, 88, diagram); painter.end()
        return image.save(str(path), 'PNG')

    def export_current(self):
        if not self.valid: return
        self.stop()
        path, _ = QFileDialog.getSaveFileName(self, '导出当前变形图',
            str(self.history.run_dir/f'deformation_frame_{self.frame.value()+1:04d}.png'), 'PNG (*.png)')
        if path and not self.export_image(path): QMessageBox.warning(self, '导出变形图', '无法保存图片，请检查目标位置。')

    def hideEvent(self, event):
        self.stop(); super().hideEvent(event)
