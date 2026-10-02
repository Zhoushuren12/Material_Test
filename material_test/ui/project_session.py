"""Project lifecycle, one loading source, units and result freshness."""
from __future__ import annotations

from pathlib import Path
import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QComboBox, QFileDialog, QMessageBox, QTableWidgetItem
from material_test.domain import LoadingProtocol
from material_test.io.project_config import load_project_config, save_project_config
from material_test.io.experiment_data import load_experiment_data


class ProjectSessionMixin:
    def _initialize_session(self):
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.setInterval(400)
        self._auto_timer.timeout.connect(self._automatic_run)
        self.auto_recalculate_check.toggled.connect(self._auto_recalculate_changed)
        for name in ('solver_path_edit', 'tcl_library_edit', 'output_dir_edit', 'material_name_edit',
                     'history_path_edit', 'experiment_path_edit'):
            getattr(self, name).textChanged.connect(lambda *_: self._mark_comparison_dirty('项目输入已修改'))
        for name in ('timeout_spin', 'history_scale_spin', 'experiment_displacement_scale_spin',
                     'experiment_force_scale_spin', 'fit_max_evaluations_spin', 'fit_population_spin',
                     'fit_local_evaluations_spin'):
            getattr(self, name).valueChanged.connect(lambda *_: self._mark_comparison_dirty('项目输入已修改'))
        for name in ('history_column_combo', 'displacement_combo', 'force_combo'):
            getattr(self, name).currentIndexChanged.connect(lambda *_: self._mark_comparison_dirty('数据列已修改'))
        self.append_zero_check.toggled.connect(lambda *_: self._mark_comparison_dirty('加载制度已修改'))
        self.fit_local_refine_check.toggled.connect(lambda *_: self._mark_project_dirty())
        self.fit_algorithm_combo.currentIndexChanged.connect(lambda *_: self._mark_project_dirty())
        self.loading_source_combo.currentIndexChanged.connect(self._source_changed)
        self.force_unit_combo.currentIndexChanged.connect(self._units_changed)
        self.length_unit_combo.currentIndexChanged.connect(self._units_changed)
        self.material_mode_combo.currentIndexChanged.connect(self._mode_changed)
        for spin in (self.experiment_displacement_scale_spin, self.experiment_force_scale_spin):
            spin.valueChanged.connect(self._refresh_experiment_preview)
            spin.valueChanged.connect(self._refresh_loading_preview)
        self.history_scale_spin.valueChanged.connect(self._refresh_loading_preview)
        self.experiment_path_edit.textChanged.connect(self._update_source_labels)
        self.history_path_edit.textChanged.connect(self._update_source_labels)
        self._loading_ui = False
        self._project_dirty = False
        self._source_changed()
        self._project_dirty = False
        self._update_project_title()
        self._units_changed(mark=False)
        self.topology_editor.force_deformation_mode = self.material_mode_combo.currentData() == 'force_deformation'
        self.topology_editor.area_spin.setEnabled(not self.topology_editor.force_deformation_mode)
        self.statusBar().hide()

    def _mark_project_dirty(self, *args):
        if getattr(self, '_loading_ui', True):
            return
        self._project_dirty = True
        self._update_project_title()

    def _update_project_title(self):
        name = self.current_project_path.name if self.current_project_path else '未保存项目'
        suffix = ' *' if self._project_dirty else ''
        self.setWindowTitle(f'{name}{suffix} · Material Test')
        self.project_label.setText(name + suffix)

    def _confirm_unsaved(self):
        if not self._project_dirty:
            return True
        answer = QMessageBox.question(self, '保存项目修改', '当前项目有未保存的修改，是否保存？',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def _confirm_material_replacement(self, template):
        if not self._material_commands:
            return True
        from material_test.material_commands import parse_material_commands
        tags = {item.tag for item in parse_material_commands(template)}
        root = self.topology_editor.modeling(require_assigned=False).root
        cleared = []
        def walk(node):
            if node.kind == 'material' and node.material_tag is not None and node.material_tag not in tags:
                cleared.append(f'{node.name or "单元"} · Tag {node.material_tag}')
            for child in node.children:
                walk(child)
        walk(root)
        text = f'将替换全部 {len(self._material_commands)} 个材料定义。\n'
        text += '以下单元将保留但解除材料赋值：\n' + '\n'.join(cleared) if cleared else '现有同 Tag 的单元将使用新材料定义。'
        return QMessageBox.question(self, '确认替换材料集合', text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes

    def save_project(self, *args, save_as=False):
        path = self.current_project_path
        if save_as or path is None:
            selected, _ = QFileDialog.getSaveFileName(self, '另存为项目' if save_as else '保存项目',
                str(path or Path.cwd() / 'project.yaml'), 'YAML (*.yaml *.yml)')
            if not selected:
                return False
            path = Path(selected)
        try:
            save_project_config(self._config_from_ui(require_ready=False), path)
        except Exception as exc:
            QMessageBox.critical(self, '保存项目', str(exc))
            return False
        self.current_project_path = path.resolve()
        self._project_dirty = False
        self._update_project_title()
        self._append_log(f'已保存项目：{path}')
        return True

    def save_project_as(self):
        return self.save_project(save_as=True)

    def load_project(self):
        if self._thread is not None or not self._confirm_unsaved():
            return
        path, _ = QFileDialog.getOpenFileName(self, '打开项目', str(Path.cwd()), 'YAML (*.yaml *.yml)')
        if not path:
            return
        try:
            config = load_project_config(path)
            self._loading_ui = True
            self._populate_from_config(config)
        except Exception as exc:
            QMessageBox.critical(self, '打开项目', str(exc))
            return
        finally:
            self._loading_ui = False
        self.current_project_path = Path(path).resolve()
        self._project_dirty = False
        self._update_project_title()
        self._update_source_labels()
        self._append_log(f'已打开项目：{path}')

    def closeEvent(self, event):
        if self._thread is not None:
            answer = QMessageBox.question(self, '任务正在运行', '取消当前任务后关闭窗口？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self._close_when_idle = True
                self.cancel_analysis()
            event.ignore()
            return
        if self._confirm_unsaved():
            self._auto_timer.stop()
            event.accept()
        else:
            event.ignore()

    def _loading_protocol_from_ui(self):
        source = self.loading_source_combo.currentData() or 'amplitudes'
        if source == 'experiment':
            path = self.experiment_path_edit.text().strip()
            return LoadingProtocol(source=source, history_path=Path(path) if path else None,
                history_column=self.displacement_combo.currentText() or None,
                history_sheet=getattr(self, '_experiment_sheet', 0),
                history_scale=self.experiment_displacement_scale_spin.value())
        path = self.history_path_edit.text().strip()
        return LoadingProtocol(source=source,
            amplitudes=self._parse_amplitudes() if source == 'amplitudes' else [],
            du=self.du_spin.value(), history_path=Path(path) if path else None,
            history_column=self.history_column_combo.currentText() or None,
            history_sheet=getattr(self, '_history_sheet', 0),
            append_zero=self.append_zero_check.isChecked(), history_scale=self.history_scale_spin.value())

    def _comparison_config_from_ui(self):
        return self._config_from_ui(require_ready=True)

    def _source_changed(self, *args):
        source = self.loading_source_combo.currentData()
        self.amplitudes_edit.setEnabled(source == 'amplitudes')
        self.du_spin.setEnabled(source == 'amplitudes')
        self.history_column_combo.setEnabled(source == 'external')
        self.history_scale_spin.setEnabled(source == 'external')
        self.append_zero_check.setEnabled(source != 'experiment')
        self._update_source_labels()
        self._refresh_loading_preview()
        self._mark_comparison_dirty('加载来源已修改')

    def _update_source_labels(self, *args):
        source = self.loading_source_combo.currentText()
        text = f'当前加载：{source} · 位移 [{self.length_unit_combo.currentText()}]'
        if self.loading_source_combo.currentData() == 'external':
            text += ' · ' + Path(self.history_path_edit.text()).name
        elif self.loading_source_combo.currentData() == 'experiment':
            text += ' · ' + Path(self.experiment_path_edit.text()).name
        for label in getattr(self, '_source_badges', []):
            label.setText(text)
        if hasattr(self, 'fit_page_button'):
            self._validate_fit_inputs()

    def _mode_changed(self, *args):
        direct = self.material_mode_combo.currentData() == 'force_deformation'
        if direct:
            self.area_spin.setValue(1.0)
            self.length_spin.setValue(1.0)
        self.area_spin.setEnabled(not direct)
        self.length_spin.setEnabled(not direct)
        self.topology_editor.area_spin.setEnabled(not direct)
        self.topology_editor.force_deformation_mode = direct
        self._units_changed()

    def _units_changed(self, *args, mark=True):
        force, length = self.force_unit_combo.currentText(), self.length_unit_combo.currentText()
        self.area_spin.setSuffix(' ' + length + '²')
        self.length_spin.setSuffix(' ' + length)
        for name in ('plot_widget', 'material_preview_plot', 'model_preview_plot', 'experiment_preview_plot', 'fitting_preview_plot'):
            plot = getattr(self, name, None)
            if plot is not None:
                plot.set_units(force, length)
        self._update_source_labels()
        self._populate_comparison_parameters()
        self._refresh_loading_preview()
        if self._selected_material_row >= 0:
            self._select_material_command(self._selected_material_row)
        if mark:
            self._mark_comparison_dirty('单位或输入模式已修改')

    def _parameter_unit(self, parameter):
        force, length = self.force_unit_combo.currentText(), self.length_unit_combo.currentText()
        direct = self.material_mode_combo.currentData() == 'force_deformation'
        stress = force if direct else f'{force}/{length}²'
        return {'dimensionless':'无量纲', 'force':stress, 'deformation':length if direct else '应变',
                'model stress unit':stress, 'model strain unit':length if direct else '应变',
                'force/deformation':f'{force}/{length}' if direct else stress,
                'stress/strain':f'{force}/{length}' if direct else stress,
                'strain':length if direct else '应变',
                'force/velocity':f'{force}·s/{length}',
                'force/(velocity^alpha)':f'{force}/({length}/s)^α',
                'velocity':f'{length}/s'}.get(parameter.unit, parameter.unit)

    def _parameter_description(self, parameter):
        text = parameter.description.split('·')[-1].strip()
        if self.material_mode_combo.currentData() == 'force_deformation':
            text = text.replace('弹性模量', '刚度').replace('屈服强度', '屈服力').replace('应力', '力')
            if parameter.unit in ('strain', 'model strain unit', 'deformation'):
                text = text.replace('应变', '变形')
        return text

    def _comparison_experiment(self):
        path = self.experiment_path_edit.text().strip()
        if not path or not self.displacement_combo.currentText() or not self.force_combo.currentText():
            return None
        try:
            key = (path, Path(path).stat().st_mtime_ns, self.displacement_combo.currentText(), self.force_combo.currentText(),
                   self.experiment_displacement_scale_spin.value(), self.experiment_force_scale_spin.value(),
                   getattr(self, '_experiment_sheet', 0))
            if getattr(self, '_experiment_cache_key', None) == key:
                return self._experiment_cache
            result = load_experiment_data(path, displacement_column=self.displacement_combo.currentText(),
                force_column=self.force_combo.currentText(),
                sheet_name=getattr(self, '_experiment_sheet', 0),
                displacement_scale=self.experiment_displacement_scale_spin.value(),
                force_scale=self.experiment_force_scale_spin.value())
            self._experiment_cache_key, self._experiment_cache = key, result
            return result
        except (ValueError, OSError, KeyError):
            return None

    def _set_status(self, text, state='waiting'):
        if not hasattr(self, 'run_state_label'):
            return
        colors = {'waiting':'#64748b', 'stale':'#a16207', 'running':'#1f5fae',
                  'complete':'#15803d', 'failed':'#b91c1c'}
        color = colors.get(state, colors['waiting'])
        self.run_state_label.setText(text)
        self.run_state_label.setStyleSheet(f'color: {color}; font-weight:600;')
        self.statusBar().clearMessage()

    def _mark_comparison_dirty(self, reason):
        if getattr(self, '_loading_ui', True):
            return
        self._mark_project_dirty()
        self._generation += 1
        self._comparison_dirty = self.current_result is not None
        if self._comparison_dirty:
            self.plot_widget.set_stale(True)
            self.stale_notice.show()
            self.set_baseline_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.publish_button.setEnabled(False)
            self._set_status(reason + ' · 待重新计算', 'stale')
        else:
            self._set_status('等待计算当前模型')
        for name in ('material_preview_plot', 'model_preview_plot'):
            plot = getattr(self, name, None)
            if plot is not None:
                plot.set_stale(True)
        if hasattr(self, '_auto_timer') and self.auto_recalculate_check.isChecked():
            self._auto_timer.start()
        self._update_navigation_states()

    def _auto_recalculate_changed(self, checked):
        if checked:
            self._auto_timer.start()
        else:
            self._auto_timer.stop()
            self._auto_run_pending = False

    def _automatic_run(self):
        if not self.auto_recalculate_check.isChecked() or self._loading_ui:
            return
        if self._thread is not None:
            self._auto_run_pending = True
            return
        try:
            self._comparison_config_from_ui()
        except (ValueError, OSError) as exc:
            self._set_status(f'自动重算等待输入：{exc}', 'waiting')
            return
        self.start_run()

    def _update_navigation_states(self):
        if not hasattr(self, 'fit_page_button'):
            return
        from material_test.protocols import history_from_protocol
        try:
            history_from_protocol(self._loading_protocol_from_ui())
            loading_ready = True
        except (ValueError, OSError, KeyError):
            loading_ready = False
        issues = self.topology_editor.assignment_issues()
        ready = [Path(self.solver_path_edit.text()).is_file(), loading_ready, bool(self._material_commands),
                 bool(self._material_commands) and not any(issues), self.current_result is not None and not self._comparison_dirty]
        titles = ['项目与单位', '试验与加载（可跳过试验）', '材料', '支撑装配', '对比与标定']
        for i, (title, valid) in enumerate(zip(titles, ready)):
            self.nav_list.item(i).setText(f'{i+1:02d}  {title}\n      {"✓ 已就绪" if valid else "! 待处理"}')
        self._validate_fit_inputs()

    def apply_loading_template(self):
        kind = self.loading_template_combo.currentData()
        amplitude = self.loading_amplitude_spin.value()
        cycles = self.loading_cycles_spin.value()
        points = [0.0]
        levels = [amplitude] if kind == 'constant' else [amplitude / 3, amplitude * 2 / 3, amplitude]
        for level in levels:
            for _ in range(cycles):
                points.extend([level, -level, 0.0])
        self.amplitudes_edit.setPlainText(', '.join(f'{x:g}' for x in points))
        self.du_spin.setValue(amplitude / 30)
        self.loading_source_combo.setCurrentIndex(self.loading_source_combo.findData('amplitudes'))

    def edit_loading_table(self):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTableWidget, QDialogButtonBox, QPushButton
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑加载转折点（项目位移单位）')
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 1)
        table.setHorizontalHeaderLabels(['位移转折点'])
        try:
            points = self._parse_amplitudes()
        except ValueError:
            points = [0.0, 1.0, -1.0, 0.0]
        for value in points:
            row = table.rowCount(); table.insertRow(row); table.setItem(row, 0, QTableWidgetItem(str(value)))
        layout.addWidget(table)
        add = QPushButton('添加转折点'); add.clicked.connect(lambda: table.insertRow(table.rowCount())); layout.addWidget(add)
        remove = QPushButton('删除所选转折点'); remove.clicked.connect(lambda: table.removeRow(table.currentRow())); layout.addWidget(remove)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        def accept():
            try:
                values = [float(table.item(r, 0).text()) for r in range(table.rowCount())]
                if len(values) < 2 or not np.isfinite(values).all():
                    raise ValueError('至少输入两个有限数值')
            except (ValueError, AttributeError) as exc:
                QMessageBox.warning(dialog, '加载转折点', str(exc)); return
            self.amplitudes_edit.setPlainText(', '.join(map(str, values)))
            dialog.accept()
        buttons.accepted.connect(accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec()
