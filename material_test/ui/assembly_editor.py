"""Compact finite-element editing inside the existing support assembly page."""
from copy import deepcopy
import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QScrollArea, QSplitter, QStackedWidget, QToolBar, QToolButton, QTreeWidget,
    QTreeWidgetItem, QSizePolicy, QVBoxLayout, QWidget)

from material_test.domain import ModelingConfig, TopologyNode
from material_test.explicit_assembly import empty_assembly, material_tags
from material_test.ui.assembly_canvas import AssemblyCanvas
from material_test.ui.editors import CompactDoubleSpinBox
from material_test.ui.topology_editor import TopologyEditor


class PropertyStack(QStackedWidget):
    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else super().sizeHint()

    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint() if self.currentWidget() else super().minimumSizeHint()


class AssemblyEditor(TopologyEditor):
    def __init__(self, parent=None):
        self.graph = empty_assembly()
        self._legacy = None
        self._history, self._future, self._edit_start = [], [], None
        self._syncing = True
        self.default_area, self.default_length = 1., 1000.
        super().__init__(parent)
        while self.layout().count():
            item = self.layout().takeAt(0)
            if item.widget(): item.widget().hide()
        self.name_edit.editingFinished.disconnect()
        self.material_combo.currentIndexChanged.disconnect()
        self.area_spin.valueChanged.disconnect()
        self.canvas = AssemblyCanvas(self)
        self.canvas.selection_changed.connect(self._selection_to_properties)
        self.canvas.position_changed.connect(lambda x,y: self.coordinates.setText(f'X {x:g}   Y {y:g}'))
        self._build_explicit_ui()
        self._syncing = False
        self.new_empty()

    def _action(self, title, icon, callback, *, checkable=False):
        action = QAction(self._icon(icon), title, self)
        action.setToolTip(title); action.setCheckable(checkable)
        action.triggered.connect(callback); self.toolbar.addAction(action)
        return action

    @staticmethod
    def _icon(name):
        return QIcon(str(Path(__file__).resolve().parents[1]/'resources'/'icons'/f'{name}.svg'))

    @staticmethod
    def _title(text):
        label = QLabel(text); label.setObjectName('sectionTitle'); return label

    def _number(self, value=0., *, positive=False):
        field = CompactDoubleSpinBox(); field.setDecimals(8)
        field.setRange(1e-12 if positive else -1e12, 1e12); field.setValue(value)
        field.setMinimumWidth(70); field.setObjectName('compactField')
        return field

    def _build_explicit_ui(self):
        self.toolbar = QToolBar(); self.toolbar.setMovable(False); self.toolbar.setObjectName('assemblyToolbar')
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.select_action = self._action('选择与移动', 'mouse-pointer-2', lambda: self.canvas.set_mode('select'), checkable=True)
        self.pan_action = self._action('平移视图', 'hand', lambda: self.canvas.set_mode('pan'), checkable=True)
        self.node_action = self._action('创建节点', 'circle-plus', lambda: self.canvas.set_mode('node'), checkable=True)
        self.draw_action = self._action('绘制单元', 'pen-line', lambda: self.canvas.set_mode('element'), checkable=True)
        self.tools = QActionGroup(self); self.tools.setExclusive(True)
        for action in (self.select_action,self.pan_action,self.node_action,self.draw_action): self.tools.addAction(action)
        self.select_action.setChecked(True)
        self.element_type = QComboBox()
        for kind in ('twoNodeLink', 'zeroLength', 'truss', 'corotTruss', 'elasticBeamColumn'):
            self.element_type.addItem(kind, kind)
        self.element_type.setToolTip('OpenSees element type'); self.element_type.setFixedWidth(170)
        self.element_type.currentIndexChanged.connect(lambda: self.draw_action.trigger())
        self.toolbar.addWidget(self.element_type)
        self.toolbar.addSeparator()
        self.undo_action = self._action('撤销', 'undo-2', self.undo)
        self.redo_action = self._action('重做', 'redo-2', self.redo)
        self._action('复制所选组件', 'copy', self.duplicate_selection)
        self._action('删除所选', 'trash', self.delete_selected)
        self.toolbar.addSeparator()
        self._action('适配模型', 'scan', lambda: self.canvas.fit_all())

        canvas_panel = QWidget(); canvas_panel.setObjectName('explicitCanvasPanel')
        canvas_layout = QVBoxLayout(canvas_panel); canvas_layout.setContentsMargins(0,0,0,0); canvas_layout.setSpacing(8)
        canvas_layout.addWidget(self.toolbar)
        canvas_layout.addWidget(self.canvas,1)
        bottom = QHBoxLayout()
        self.coordinates = QLabel('X 0   Y 0'); self.coordinates.setObjectName('mutedLabel'); bottom.addWidget(self.coordinates)
        bottom.addStretch(1)
        self.expand_check = QCheckBox('展开连接节点')
        self.expand_check.setToolTip('仅示意展开重合节点，实际坐标、约束和连接关系不变')
        self.expand_check.toggled.connect(lambda: self.canvas.render())
        bottom.addWidget(self.expand_check)
        self.snap_check = QCheckBox('吸附'); self.snap_check.setChecked(True); bottom.addWidget(self.snap_check)
        self.grid_spin = self._number(50, positive=True); self.grid_spin.setFixedWidth(82)
        self.grid_spin.setToolTip('网格间距（项目长度单位）'); self.grid_spin.valueChanged.connect(lambda: self.canvas.viewport().update())
        bottom.addWidget(self.grid_spin); canvas_layout.addLayout(bottom)

        self.right_panel = QWidget(); self.right_panel.setObjectName('assemblyInspector'); self.right_panel.setMinimumWidth(286)
        outer = QVBoxLayout(self.right_panel); outer.setContentsMargins(14,8,0,0); outer.setSpacing(10)
        self.inspector_splitter = QSplitter(Qt.Orientation.Vertical)
        self.inspector_splitter.setObjectName('assemblyInspectorSplitter')
        self.inspector_splitter.setChildrenCollapsible(False); self.inspector_splitter.setHandleWidth(10)
        outer.addWidget(self.inspector_splitter,1)

        component_panel = QWidget(); component_panel.setObjectName('assemblyComponentPanel')
        component_layout = QVBoxLayout(component_panel); component_layout.setContentsMargins(0,0,8,0); component_layout.setSpacing(6)
        component_layout.addWidget(self._title('装配组件'))
        self.entity_list = QTreeWidget(); self.entity_list.setHeaderLabels(['组件','类型 / 材料'])
        self.entity_list.setAlternatingRowColors(True); self.entity_list.setRootIsDecorated(False)
        self.entity_list.setMinimumHeight(64)
        self.entity_list.currentItemChanged.connect(self._list_selected)
        component_layout.addWidget(self.entity_list,1)
        self.inspector_splitter.addWidget(component_panel)

        property_panel = QWidget(); property_panel.setObjectName('assemblyPropertyPanel')
        property_layout = QVBoxLayout(property_panel); property_layout.setContentsMargins(0,0,0,0); property_layout.setSpacing(6)
        self.selection_title = self._title('属性'); property_layout.addWidget(self.selection_title)
        property_content = QWidget(); property_content.setObjectName('assemblyInspectorContent')
        property_content_layout = QVBoxLayout(property_content); property_content_layout.setContentsMargins(0,0,8,0)
        self.properties_scroll = QScrollArea(); self.properties_scroll.setWidgetResizable(True)
        self.properties_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.properties_scroll.setMinimumHeight(64)
        property_layout.addWidget(self.properties_scroll,1)
        self.inspector_splitter.addWidget(property_panel)
        self.properties = PropertyStack()
        self.properties.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Maximum)
        blank = QWidget(); self.properties.addWidget(blank)
        node_page = QWidget(); nf = QFormLayout(node_page); nf.setContentsMargins(0,0,8,0)
        self.node_x, self.node_y = self._number(), self._number()
        nf.addRow('X',self.node_x); nf.addRow('Y',self.node_y)
        self.node_fix = []
        fix_row = QWidget(); fixes = QHBoxLayout(fix_row); fixes.setContentsMargins(0,0,0,0)
        for dof in ('UX','UY','RZ'):
            field = QCheckBox(dof); fixes.addWidget(field); self.node_fix.append(field)
            field.toggled.connect(self._node_properties)
        nf.addRow('固定', fix_row)
        self.support_preset = QComboBox(); self.support_preset.addItems(['自定义','自由','铰支','固支','水平滑动','竖向滑动'])
        self.support_preset.currentIndexChanged.connect(self._preset_changed); nf.addRow('支承',self.support_preset)
        self.properties.addWidget(node_page)
        for field in (self.node_x,self.node_y): field.editingFinished.connect(self._node_properties)

        element_page = QWidget(); ef = QFormLayout(element_page); ef.setContentsMargins(0,0,8,0)
        self.selected_type = QComboBox()
        for index in range(self.element_type.count()):
            self.selected_type.addItem(self.element_type.itemText(index), self.element_type.itemData(index))
        ef.addRow('类型',self.selected_type); ef.addRow('名称',self.name_edit)
        self.node_i, self.node_j = QComboBox(), QComboBox(); ef.addRow('节点 i',self.node_i); ef.addRow('节点 j',self.node_j)
        self.direction_fields, self.material_fields = [], []
        for label in ('局部 X','局部 Y','转动 RZ'):
            field = QCheckBox(label); material = QComboBox()
            row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0,0,0,0)
            rl.addWidget(field); rl.addWidget(material,1); ef.addRow(row)
            self.direction_fields.append(field); self.material_fields.append(material)
            field.toggled.connect(self._element_properties); material.currentIndexChanged.connect(self._element_properties)
        ef.addRow('杆材料',self.material_combo); ef.addRow('A',self.area_spin)
        self.beam_e, self.beam_iz = self._number(200000,positive=True), self._number(1e6,positive=True)
        ef.addRow('E',self.beam_e); ef.addRow('Iz',self.beam_iz)
        self.transform_combo = QComboBox(); self.transform_combo.addItems(['Linear','PDelta','Corotational']); ef.addRow('几何变换',self.transform_combo)
        self.auto_axis = QCheckBox('随端点方向'); self.auto_axis.setChecked(True)
        self.axis_angle = self._number(); self.axis_angle.setSuffix(' deg')
        ef.addRow('局部轴',self.auto_axis); ef.addRow('轴角度',self.axis_angle)
        self._element_form = ef
        self.properties.addWidget(element_page); property_content_layout.addWidget(self.properties)
        property_content_layout.addStretch(1)
        self.properties_scroll.setWidget(property_content)
        for field in (self.selected_type,self.node_i,self.node_j,self.material_combo,self.transform_combo): field.currentIndexChanged.connect(self._element_properties)
        self.name_edit.editingFinished.connect(self._element_properties)
        for field in (self.area_spin,self.beam_e,self.beam_iz,self.axis_angle): field.editingFinished.connect(self._element_properties)
        self.auto_axis.toggled.connect(self._element_properties)

        loading_panel = QWidget(); loading_panel.setObjectName('assemblyLoadingPanel')
        loading_layout = QVBoxLayout(loading_panel); loading_layout.setContentsMargins(0,0,0,0); loading_layout.setSpacing(6)
        loading_layout.addWidget(self._title('位移控制加载'))
        loading_content = QWidget(); loading_content.setObjectName('assemblyInspectorContent')
        load_form = QFormLayout(loading_content); load_form.setContentsMargins(0,0,8,0); load_form.setVerticalSpacing(6)
        load_form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        self.loading_scroll = QScrollArea(); self.loading_scroll.setWidgetResizable(True)
        self.loading_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.loading_scroll.setMinimumHeight(64)
        loading_layout.addWidget(self.loading_scroll,1)
        self.inspector_splitter.addWidget(loading_panel)
        self.control_node = QComboBox(); self.control_dof = QComboBox()
        for label,dof in [('全局 X / UX',1),('全局 Y / UY',2),('转角 / RZ',3)]: self.control_dof.addItem(label,dof)
        self.analysis_combo = QComboBox(); self.analysis_combo.addItem('静力','static'); self.analysis_combo.addItem('瞬态','transient')
        self.dt_spin = self._number(.01,positive=True); self.dt_spin.setSuffix(' s')
        node_row = QWidget(); node_layout = QHBoxLayout(node_row); node_layout.setContentsMargins(0,0,0,0)
        node_layout.addWidget(self.control_node,1)
        self.assign_control_button = QToolButton()
        self.assign_control_button.setIcon(self._icon('crosshair'))
        self.assign_control_button.setToolTip('将选中节点设为加载节点')
        self.assign_control_button.clicked.connect(self.assign_selected_control)
        node_layout.addWidget(self.assign_control_button)
        load_form.addRow('节点',node_row); load_form.addRow('方向',self.control_dof)
        load_form.addRow('分析',self.analysis_combo); load_form.addRow('采样间隔',self.dt_spin)
        self.loading_scroll.setWidget(loading_content)
        self._loading_form = load_form
        for field in (self.control_node,self.control_dof,self.analysis_combo): field.currentIndexChanged.connect(self._control_changed)
        self.dt_spin.editingFinished.connect(self._control_changed)
        self.inspector_splitter.setStretchFactor(0,0)
        self.inspector_splitter.setStretchFactor(1,1)
        self.inspector_splitter.setStretchFactor(2,0)
        self.inspector_splitter.setSizes([170,330,200])
        self.inspector_splitter.handle(1).setToolTip('上下拖动，调整装配组件与属性区的高度')
        self.inspector_splitter.handle(2).setToolTip('上下拖动，调整属性区与位移控制加载区的高度')
        self.message = QLabel(); self.message.setWordWrap(True); self.message.setObjectName('previewNote'); outer.addWidget(self.message)
        self.command_layout = QHBoxLayout(); outer.addLayout(self.command_layout)
        self.splitter = QSplitter(); self.splitter.setChildrenCollapsible(False); self.splitter.setHandleWidth(10)
        self.splitter.setObjectName('assemblyPageSplitter')
        self.splitter.addWidget(canvas_panel); self.splitter.addWidget(self.right_panel)
        self.splitter.setStretchFactor(0,1); self.splitter.setStretchFactor(1,0); self.splitter.setSizes([800,310])
        self.splitter.handle(1).setToolTip('左右拖动，调整画布与装配面板的宽度')
        self.layout().addWidget(self.splitter)

    def node(self, tag):
        return next(n for n in self.graph['nodes'] if n['tag']==tag)

    def element(self, tag):
        return next(e for e in self.graph['elements'] if e['tag']==tag)

    def coincident_group(self, tags):
        tags = set(tags)
        while True:
            previous = set(tags)
            for e in self.graph['elements']:
                if e['type'] == 'zeroLength' and tags.intersection((e['i'],e['j'])):
                    tags.update((e['i'],e['j']))
            if tags == previous:
                return tags

    element_material_tags = staticmethod(material_tags)

    def new_empty(self):
        self.graph = empty_assembly(); self._legacy = None
        if hasattr(self,'canvas'):
            self.canvas.selection.clear(); self._history.clear(); self._future.clear(); self._refresh_explicit()

    def begin_edit(self):
        if self._edit_start is None: self._edit_start = (deepcopy(self.graph),deepcopy(self._legacy))

    def end_edit(self):
        if self._edit_start is not None and self.graph != self._edit_start[0]:
            self._history.append(self._edit_start); self._history = self._history[-100:]
            self._future.clear(); self._legacy = None
            self._refresh_explicit(); self.changed.emit()
        else:
            self._refresh_explicit()
        self._edit_start = None

    def undo(self):
        if not self._history: return
        self._future.append((deepcopy(self.graph),deepcopy(self._legacy)))
        self.graph,self._legacy = self._history.pop()
        self.canvas.selection.clear(); self._refresh_explicit(); self.changed.emit()

    def redo(self):
        if not self._future: return
        self._history.append((deepcopy(self.graph),deepcopy(self._legacy)))
        self.graph,self._legacy = self._future.pop()
        self.canvas.selection.clear(); self._refresh_explicit(); self.changed.emit()

    def create_node(self, x, y):
        key = max([n['tag'] for n in self.graph['nodes']],default=0)+1
        n = {'tag':key,'x':float(x),'y':float(y),'fix':[0,0,0]}
        self.graph['nodes'].append(n); return n

    def create_element(self, i, j, *, kind=None):
        key = max([e['tag'] for e in self.graph['elements']],default=0)+1
        kind = kind or self.element_type.currentData()
        e = {'tag':key,'name':f'E{key:02d}','type':kind,'i':i,'j':j}
        if kind in ('twoNodeLink','zeroLength'): e.update(materials=[None],directions=[1],axis=[1.,0.],axis_auto=True)
        elif kind in ('truss','corotTruss'): e.update(material=None,A=self.default_area)
        else: e.update(A=self.default_area,E=200000.,Iz=1e6,transformation='Linear')
        self.graph['elements'].append(e); return e

    def delete_selected(self):
        if not self.canvas.selection: return
        self.begin_edit()
        nodes = {tag for kind,tag in self.canvas.selection if kind=='node'}
        elements = {tag for kind,tag in self.canvas.selection if kind=='element'}
        self.graph['nodes'] = [n for n in self.graph['nodes'] if n['tag'] not in nodes]
        self.graph['elements'] = [e for e in self.graph['elements'] if e['tag'] not in elements and not nodes.intersection((e['i'],e['j']))]
        self.graph['constraints'] = [c for c in self.graph.get('constraints',[]) if not nodes.intersection((c['retained'],c['constrained']))]
        if self.graph['control']['node'] in nodes: self.graph['control']['node'] = None
        self.canvas.selection.clear(); self.end_edit(); self._selection_to_properties()

    def duplicate_selection(self):
        if not self.canvas.selection: return
        self.begin_edit(); tags = {tag for kind,tag in self.canvas.selection if kind=='node'}
        selected = [self.element(tag) for kind,tag in self.canvas.selection if kind=='element']
        for e in selected: tags.update((e['i'],e['j']))
        mapping = {}
        for tag in sorted(tags):
            n = self.node(tag); copy = deepcopy(n); new = self.create_node(n['x']+self.grid_spin.value(),n['y']+self.grid_spin.value())
            copy.update(tag=new['tag'],x=new['x'],y=new['y']); new.update(copy); mapping[tag] = new['tag']
        for constraint in list(self.graph.get('constraints',[])):
            if {constraint['retained'],constraint['constrained']}.issubset(mapping):
                copy = deepcopy(constraint)
                copy.update(retained=mapping[copy['retained']],constrained=mapping[copy['constrained']])
                self.graph['constraints'].append(copy)
        self.canvas.selection.clear()
        if not selected:
            self.canvas.selection.update(('node',tag) for tag in mapping.values())
        for e in selected:
            copy = deepcopy(e); new = self.create_element(mapping[e['i']],mapping[e['j']],kind=e['type'])
            copy.update(tag=new['tag'],name=new['name'],i=new['i'],j=new['j']); new.update(copy)
            self.canvas.selection.add(('element',new['tag']))
        self.end_edit(); self._selection_to_properties()

    def _refresh_explicit(self):
        self._syncing = True
        if self.canvas.start_node not in {n['tag'] for n in self.graph['nodes']}:
            self.canvas.start_node = None
        self.entity_list.clear()
        for n in self.graph['nodes']:
            item = QTreeWidgetItem([f"N{n['tag']}",f"({n['x']:g}, {n['y']:g})"]); item.setData(0,Qt.ItemDataRole.UserRole,('node',n['tag'])); self.entity_list.addTopLevelItem(item)
        for e in self.graph['elements']:
            tags = material_tags(e)
            material_text = ', '.join(f'Tag {t}' if t is not None else '未赋值' for t in tags)
            text = e['type'] + (f' · {material_text}' if material_text else '')
            item = QTreeWidgetItem([e.get('name') or f"E{e['tag']}",text]); item.setData(0,Qt.ItemDataRole.UserRole,('element',e['tag'])); item.setToolTip(1,e['type']); self.entity_list.addTopLevelItem(item)
        for combo in (self.node_i,self.node_j,self.control_node):
            combo.clear(); combo.addItem('未指定',None)
            for n in self.graph['nodes']: combo.addItem(f"N{n['tag']}",n['tag'])
        control = self.graph['control']
        self.control_node.setCurrentIndex(max(0,self.control_node.findData(control.get('node'))))
        self.control_dof.setCurrentIndex(max(0,self.control_dof.findData(control['dof'])))
        self.analysis_combo.setCurrentIndex(max(0,self.analysis_combo.findData(control.get('analysis','static'))))
        self.dt_spin.setValue(control.get('dt',.01)); self.dt_spin.setEnabled(control.get('analysis')=='transient')
        self._loading_form.setRowVisible(self.dt_spin,control.get('analysis')=='transient')
        self.undo_action.setEnabled(bool(self._history)); self.redo_action.setEnabled(bool(self._future))
        self._syncing = False
        self.properties.updateGeometry()
        self.canvas.render(); self._selection_to_properties()
        self.message.setText(f"{len(self.graph['nodes'])} 节点 · {len(self.graph['elements'])} 单元")

    def _list_selected(self, current, previous):
        if not self._syncing and current:
            self.canvas.select(*current.data(0,Qt.ItemDataRole.UserRole))

    def _selection_to_properties(self):
        self._syncing = True
        selection = sorted(self.canvas.selection)
        self.assign_control_button.setEnabled(len(selection)==1 and selection[0][0]=='node')
        for index in range(self.entity_list.topLevelItemCount()):
            item = self.entity_list.topLevelItem(index)
            if item.data(0,Qt.ItemDataRole.UserRole) in self.canvas.selection: self.entity_list.setCurrentItem(item); break
        if len(selection) != 1:
            self.selection_title.setText(f'已选 {len(selection)} 个对象' if selection else '属性'); self.properties.setCurrentIndex(0)
        else:
            kind,tag = selection[0]; self.selection_title.setText(f"{'节点 N' if kind=='node' else '单元 E'}{tag}")
            if kind == 'node':
                n = self.node(tag); self.properties.setCurrentIndex(1); self.node_x.setValue(n['x']); self.node_y.setValue(n['y'])
                for field,value in zip(self.node_fix,n['fix']): field.setChecked(bool(value))
                self.support_preset.setCurrentIndex(0)
            else:
                e = self.element(tag); self.properties.setCurrentIndex(2)
                self.selected_type.setCurrentIndex(self.selected_type.findData(e['type'])); self.name_edit.setText(e.get('name',''))
                self.node_i.setCurrentIndex(self.node_i.findData(e['i'])); self.node_j.setCurrentIndex(self.node_j.findData(e['j']))
                link = e['type'] in ('twoNodeLink','zeroLength'); truss = e['type'] in ('truss','corotTruss')
                for index,(direction,material) in enumerate(zip(self.direction_fields,self.material_fields),start=1):
                    direction.parentWidget().setVisible(link)
                    direction.setChecked(index in e.get('directions',[]))
                    material.setEnabled(direction.isChecked())
                    assigned = dict(zip(e.get('directions',[]),e.get('materials',[]))).get(index)
                    material.setCurrentIndex(max(0,material.findData(assigned)))
                for field,visible in ((self.material_combo,truss),(self.area_spin,not link),(self.beam_e,not link and not truss),(self.beam_iz,not link and not truss),(self.transform_combo,not link and not truss),(self.auto_axis,link),(self.axis_angle,link)):
                    self._element_form.setRowVisible(field,visible)
                self.material_combo.setCurrentIndex(max(0,self.material_combo.findData(e.get('material'))))
                self.area_spin.setEnabled(not link); self.area_spin.setValue(e.get('A',self.default_area))
                self.beam_e.setValue(e.get('E',200000)); self.beam_iz.setValue(e.get('Iz',1e6)); self.transform_combo.setCurrentText(e.get('transformation','Linear'))
                self.auto_axis.setChecked(e.get('axis_auto',True)); self.axis_angle.setEnabled(not self.auto_axis.isChecked())
                self._element_form.setRowVisible(self.axis_angle,link and not self.auto_axis.isChecked())
                axis = e.get('axis',[1,0]); self.axis_angle.setValue(math.degrees(math.atan2(axis[1],axis[0])))
        self._syncing = False
        self.properties.updateGeometry()

    def _node_properties(self):
        if self._syncing or len(self.canvas.selection)!=1: return
        kind,tag = next(iter(self.canvas.selection))
        if kind!='node': return
        self.begin_edit(); n = self.node(tag)
        dx,dy = self.node_x.value()-n['x'],self.node_y.value()-n['y']
        for key in self.coincident_group({tag}):
            other = self.node(key); other['x'] += dx; other['y'] += dy
        n['fix'] = [int(f.isChecked()) for f in self.node_fix]
        self.end_edit()

    def _preset_changed(self,index):
        if self._syncing or index==0: return
        values = ([0,0,0],[1,1,0],[1,1,1],[0,1,1],[1,0,1])[index-1]
        self._syncing = True
        for field,value in zip(self.node_fix,values): field.setChecked(bool(value))
        self._syncing = False; self._node_properties()

    def _element_properties(self):
        if self._syncing or len(self.canvas.selection)!=1: return
        kind,tag = next(iter(self.canvas.selection))
        if kind!='element': return
        i,j,element_type = self.node_i.currentData(),self.node_j.currentData(),self.selected_type.currentData()
        valid = {n['tag'] for n in self.graph['nodes']}
        if i not in valid or j not in valid or i==j:
            self._selection_to_properties(); self.message.setText('单元需要两个不同的有效节点'); return
        a,b = self.node(i),self.node(j)
        if element_type=='zeroLength' and math.hypot(a['x']-b['x'],a['y']-b['y'])>1e-8:
            self._selection_to_properties(); self.message.setText('zeroLength 端点必须重合'); return
        self.begin_edit(); e = self.element(tag)
        if element_type != e['type']:
            assigned = next((t for t in material_tags(e) if t is not None),None)
            e.clear(); e.update(tag=tag,type=element_type)
            if element_type in ('twoNodeLink','zeroLength'):
                e.update(directions=[1],materials=[assigned],axis=[1.,0.],axis_auto=True)
            elif element_type in ('truss','corotTruss'):
                e.update(material=assigned,A=self.default_area)
            else:
                e.update(A=self.default_area,E=200000.,Iz=1e6,transformation='Linear')
            e.update(name=self.name_edit.text(),i=i,j=j); self.end_edit(); return
        e.update(name=self.name_edit.text(),i=i,j=j)
        if e['type'] in ('twoNodeLink','zeroLength'):
            dirs = [i for i,f in enumerate(self.direction_fields,start=1) if f.isChecked()] or [1]
            e.update(directions=dirs,materials=[self.material_fields[i-1].currentData() for i in dirs],axis_auto=self.auto_axis.isChecked())
            angle = math.radians(self.axis_angle.value()); e['axis'] = [math.cos(angle),math.sin(angle)]
        elif e['type'] in ('truss','corotTruss'): e.update(material=self.material_combo.currentData(),A=self.area_spin.value() or self.default_area)
        else: e.update(A=self.area_spin.value() or self.default_area,E=self.beam_e.value(),Iz=self.beam_iz.value(),transformation=self.transform_combo.currentText())
        self.end_edit()

    def assign_selected_control(self):
        if len(self.canvas.selection)!=1: return
        kind,tag = next(iter(self.canvas.selection))
        if kind=='node':
            self.control_node.setCurrentIndex(self.control_node.findData(tag))

    def _control_changed(self):
        if self._syncing: return
        self.begin_edit()
        self.graph['control'].update(node=self.control_node.currentData(),dof=self.control_dof.currentData(),
                                     analysis=self.analysis_combo.currentData(),dt=self.dt_spin.value())
        self.end_edit()
        window = self.window()
        if hasattr(window,'_units_changed'):
            window._units_changed(mark=False)

    def modeling(self, *, require_assigned=True):
        if self._legacy is not None:
            return super().modeling(require_assigned=require_assigned)
        if require_assigned:
            missing,undefined = self.assignment_issues()
            if missing or undefined: raise ValueError('材料赋值待完善：'+', '.join(missing+undefined))
        config = ModelingConfig(topology='explicit',assembly=deepcopy(self.graph))
        config.validate(require_assigned=require_assigned); return config

    def set_modeling(self,modeling,test_material_tag):
        if modeling.assembly is None:
            self.set_root(deepcopy(modeling.resolved_root(test_material_tag))); return
        self.graph = deepcopy(modeling.assembly); self._legacy = None
        self._history.clear(); self._future.clear(); self.canvas.selection.clear(); self._refresh_explicit(); self.canvas.fit_all()

    def set_root(self,root):
        super().set_root(root)
        if hasattr(self,'canvas'):
            self._graph_from_root(self._node_from_item(self.tree.topLevelItem(0),validate=False))

    def _model_changed(self):
        super()._model_changed()
        if hasattr(self,'canvas'):
            root = self._node_from_item(self.tree.topLevelItem(0),validate=False)
            self._graph_from_root(root)

    def _graph_from_root(self,root):
        self.graph = empty_assembly(); self._legacy = deepcopy(root)
        first = self.create_node(0,0); last = self.create_node(self.default_length,0)
        first['fix'] = [1,1,1]; last['fix'] = [0,1,1]
        def emit(node,i,j):
            if node.kind=='material':
                e = self.create_element(i,j,kind='twoNodeLink' if self.force_deformation_mode else 'truss')
                e['name'] = node.name
                if e['type']=='truss': e.update(material=node.material_tag,A=node.area or self.default_area)
                else: e['materials'] = [node.material_tag]
            elif node.kind=='parallel':
                for child in node.children: emit(child,i,j)
            else:
                total = sum(c.length_weight for c in node.children); previous=i; fraction=0
                for index,child in enumerate(node.children):
                    fraction += child.length_weight/total
                    if index==len(node.children)-1: end=j
                    else:
                        a,b=self.node(i),self.node(j); n=self.create_node(a['x']+(b['x']-a['x'])*fraction,0); n['fix']=[0,1,1]; end=n['tag']
                    emit(child,previous,end); previous=end
        emit(root,first['tag'],last['tag']); self.graph['control']['node']=last['tag']
        self._refresh_explicit(); self.canvas.fit_all()

    def set_material_options(self,options):
        previous = self._syncing
        self._syncing = True
        super().set_material_options(options)
        if not hasattr(self,'material_fields'):
            self._syncing = previous; return
        for combo in self.material_fields:
            combo.clear(); combo.addItem('未赋值',None)
            for tag,label in options: combo.addItem(f'{tag} · {label}',tag)
        self._syncing = previous; self._selection_to_properties(); self.canvas.render()

    def assignment_issues(self):
        if not hasattr(self,'canvas') or self._legacy is not None: return super().assignment_issues()
        missing,undefined = [],[]
        for e in self.graph['elements']:
            for tag in material_tags(e):
                name = e.get('name') or f"E{e['tag']}"
                if tag is None: missing.append(name)
                elif tag not in self._material_options: undefined.append(f'{name} (Tag {tag})')
        return list(dict.fromkeys(missing)),list(dict.fromkeys(undefined))

    def count_material_references(self,tag):
        return sum(tag in material_tags(e) for e in self.graph['elements']) if hasattr(self,'canvas') else super().count_material_references(tag)

    def reassign_material_tag(self,old_tag,new_tag):
        if self._legacy is not None: return super().reassign_material_tag(old_tag,new_tag)
        self.begin_edit(); count=0
        for e in self.graph['elements']:
            if old_tag not in material_tags(e): continue
            count+=1
            if e['type'] in ('truss','corotTruss'): e['material']=new_tag
            else: e['materials']=[new_tag if tag==old_tag else tag for tag in e['materials']]
        self.end_edit(); return count

    def clear_undefined_assignments(self):
        if self._legacy is not None: return super().clear_undefined_assignments()
        count=0
        for tag in {t for e in self.graph['elements'] for t in material_tags(e) if t is not None and t not in self._material_options}:
            count+=self.reassign_material_tag(tag,None)
        return count
