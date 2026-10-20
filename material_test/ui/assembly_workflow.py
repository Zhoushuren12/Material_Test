"""Use the existing project session for assembly calculations and curve dialogs."""
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from material_test.domain import LoadingProtocol, MaterialTemplate, ModelingConfig, ParameterSpec, ProjectConfig
from material_test.ui.plot_canvas import HysteresisPreviewWidget


class AssemblyWorkflowMixin:
    def _analysis_busy(self):
        return self._thread is not None

    def _update_deformation_actions(self):
        ready = getattr(self, '_assembly_deformation', None) is not None and not self._comparison_dirty and not self._analysis_busy()
        for name in ('assembly_deformation_button', 'assembly_response_deformation'):
            button = getattr(self, name, None)
            if button is not None: button.setEnabled(ready)

    def _clear_assembly_deformation(self, message, *, reset=False):
        self._assembly_deformation = None
        dialog = getattr(self, 'assembly_deformation_dialog', None)
        if dialog is not None:
            if reset: dialog.clear(message)
            else: dialog.invalidate(message)
        self._update_deformation_actions()

    def open_assembly_deformation(self):
        if self._analysis_busy() or self._comparison_dirty or getattr(self, '_assembly_deformation', None) is None:
            return
        from material_test.ui.assembly_deformation import AssemblyDeformationDialog
        if getattr(self, 'assembly_deformation_dialog', None) is None:
            self.assembly_deformation_dialog = AssemblyDeformationDialog(self)
        dialog = self.assembly_deformation_dialog
        if dialog.history is not self._assembly_deformation:
            dialog.set_history(self._assembly_deformation)
        dialog.show(); dialog.raise_()

    def _set_running(self, running, text):
        super()._set_running(running, text)
        if running: self._clear_assembly_deformation('计算中，等待新的变形结果')
        else: self._update_deformation_actions()

    def open_assembly_response(self):
        if self._analysis_busy(): return
        try:
            config = self._config_from_ui()
            from material_test.protocols import history_from_protocol
            history_from_protocol(config.loading)
        except Exception as exc:
            QMessageBox.warning(self,'装配检查',str(exc)); return
        if getattr(self,'assembly_response_dialog',None) is None:
            dialog = QDialog(self); dialog.setWindowTitle('支撑滞回曲线'); dialog.resize(820,600)
            layout = QVBoxLayout(dialog)
            self.assembly_response_plot = HysteresisPreviewWidget(); layout.addWidget(self.assembly_response_plot,1)
            self.assembly_response_status = QLabel(); self.assembly_response_status.setWordWrap(True); layout.addWidget(self.assembly_response_status)
            row = QHBoxLayout(); row.addStretch(1)
            self.assembly_response_deformation = QPushButton('阻尼器变形图')
            self.assembly_response_deformation.clicked.connect(self.open_assembly_deformation)
            self.assembly_response_deformation.setEnabled(False)
            self.assembly_response_cancel = QPushButton('取消计算'); self.assembly_response_cancel.clicked.connect(self.cancel_analysis)
            self.assembly_response_export = QPushButton('导出曲线图'); self.assembly_response_export.clicked.connect(self.export_figure)
            row.addWidget(self.assembly_response_deformation); row.addWidget(self.assembly_response_export); row.addWidget(self.assembly_response_cancel); layout.addLayout(row)
            self.assembly_response_dialog = dialog
        self.assembly_response_plot.clear('计算中')
        self.assembly_response_export.setEnabled(False); self.assembly_response_cancel.setEnabled(True)
        self.assembly_response_status.setText('OpenSees 计算中')
        self.assembly_response_dialog.show(); self.assembly_response_dialog.raise_()
        self.start_run()

    @Slot(object)
    def _run_finished(self,result):
        super()._run_finished(result)
        self._assembly_deformation = None if self._comparison_dirty else result.simulation.deformation
        dialog = getattr(self, 'assembly_deformation_dialog', None)
        if dialog is not None and self._assembly_deformation is not None:
            dialog.set_history(self._assembly_deformation)
        self._update_deformation_actions()
        if getattr(self,'assembly_response_dialog',None) is not None:
            force,length = self.force_unit_combo.currentText(),self.length_unit_combo.currentText()
            graph = self.topology_editor.modeling(require_assigned=False).assembly
            rotation = graph is not None and graph['control']['dof']==3
            self.assembly_response_plot.set_units(f'{force}*{length}' if rotation else force,'rad' if rotation else length)
            self.assembly_response_plot.update_curve(result.simulation.displacement,result.simulation.force,title='支撑滞回曲线',experiment=result.experiment)
            self.assembly_response_status.setText('当前输入已变化，结果待更新' if self._comparison_dirty else f'计算完成 · {len(result.simulation.force)} 个加载点')
            self.assembly_response_plot.set_stale(self._comparison_dirty)
            self.assembly_response_export.setEnabled(not self._comparison_dirty)
            self.assembly_response_cancel.setEnabled(False)

    def _mark_comparison_dirty(self,reason):
        super()._mark_comparison_dirty(reason)
        if not self._loading_ui:
            self._clear_assembly_deformation(reason+'，请重新计算')
        if not self._loading_ui and getattr(self,'assembly_response_dialog',None) is not None:
            self.assembly_response_plot.set_stale(True); self.assembly_response_export.setEnabled(False)
            self.assembly_response_status.setText(reason+'，请重新计算')

    @Slot()
    def _run_cancelled(self):
        super()._run_cancelled()
        self._clear_assembly_deformation('计算已取消，等待重新计算')
        if getattr(self,'assembly_response_dialog',None) is not None:
            self.assembly_response_status.setText('计算已取消'); self.assembly_response_cancel.setEnabled(False)

    @Slot(str)
    def _run_failed(self,message):
        self._clear_assembly_deformation('计算失败，请检查运行日志并重新计算')
        if getattr(self,'assembly_response_dialog',None) is not None:
            self.assembly_response_status.setText('计算失败，请检查约束与材料赋值；详见运行日志')
            self.assembly_response_cancel.setEnabled(False)
        super()._run_failed(message)

    def import_component_project(self,path):
        from material_test.components2d.model import load_component, ordered_history
        project = load_component(path)
        data = project.data
        if data.get('fitting',{}).get('observations'):
            raise ValueError('此组件包含专用观测拟合配置，请先保存不含专用拟合配置的副本再导入支撑装配。')
        commands,parameters = [],[]
        for template in project.materials.values():
            source = template.command.replace('{tag}',str(template.tag))
            for parameter in template.parameters:
                new_name = f'm{template.tag}_{parameter.name}'
                source = source.replace('{'+parameter.name+'}','{'+new_name+'}')
                mapping = parameter.to_mapping(); mapping['name'] = new_name
                parameters.append(ParameterSpec.from_mapping(mapping))
            commands.append(source)
        material = MaterialTemplate(data['name'],'\n'.join(commands),next(iter(project.materials)),parameters)
        graph = {key:deepcopy(data.get(key,[])) for key in ('nodes','elements','constraints')}
        for node in graph['nodes']: node.setdefault('fix',[0,0,0])
        graph['control'] = {'analysis':'static','dt':.01,**{key:data['loading'][key] for key in ('node','dof','analysis','dt') if key in data['loading']}}
        graph['source_metadata'] = {key:deepcopy(data[key]) for key in ('groups','ports','channels','plot') if key in data}
        for e in graph['elements']:
            if e['type'] in ('twoNodeLink','zeroLength'): e['axis_auto'] = False
        history = ordered_history(data['loading'])
        config = ProjectConfig(solver=project.solver,material=material,
                               loading=LoadingProtocol(source='samples',samples=history.tolist(),du=.1),
                               output_dir=project.output_root,modeling=ModelingConfig(topology='explicit',assembly=graph),
                               force_unit=data['units']['force'],length_unit=data['units']['length'],material_mode='force_deformation')
        self._populate_from_config(config)
        self.current_project_path = None; self._project_dirty = True
        self._update_project_title(); self._navigate_to(3)
        self._remember_project(path)
        self._append_log(f'已导入显式装配：{Path(path).resolve()}；请另存为材料测试项目')
