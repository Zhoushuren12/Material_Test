"""Cancellable calculation and review-before-apply fitting workflow."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QDialogButtonBox, QMessageBox, QFileDialog
from material_test.domain import FitParameterSpec
from material_test.ui.workers import RunWorker, FitWorker
from material_test.material_preview import preview_error_summary
from material_test.workflow import WorkflowResult
from material_test.brace_export import build_brace_package


class FittingWorkflowMixin:
    def _validate_fit_inputs(self, *args):
        if not hasattr(self, 'fit_page_button') or getattr(self, '_syncing_fit', False):
            return
        errors, selected = [], 0
        for row in range(self.fit_table.rowCount()):
            item = self.fit_table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            selected += 1
            name = self._table_text(row, 1, self.fit_table)
            valid = True
            try:
                scale = self.fit_table.cellWidget(row, 4).currentData()
                low, high = float(self._table_text(row, 2, self.fit_table)), float(self._table_text(row, 3, self.fit_table))
                FitParameterSpec(name, low, high, scale).validate()
                parameter = self._material_parameters[name]
                if parameter.min is not None and low < parameter.min or parameter.max is not None and high > parameter.max:
                    raise ValueError('边界超出材料允许范围')
            except (ValueError, AttributeError, KeyError) as exc:
                valid = False; errors.append(f'{name}: {exc}')
            self.fit_table.blockSignals(True)
            for col in (2, 3):
                cell = self.fit_table.item(row, col)
                if cell is not None:
                    cell.setBackground(QColor('white' if valid else '#fee2e2'))
            self.fit_table.blockSignals(False)
        has_experiment = self._comparison_experiment() is not None
        if not has_experiment:
            hint = '参数验证模式：可计算和发布；自动拟合需导入有效试验数据。'
        elif errors:
            hint = '请检查拟合边界：' + '；'.join(errors)
        elif not selected:
            hint = '请选择待拟合参数，并核对预填边界。'
        else:
            hint = f'已选择 {selected} 个参数；拟合结束后确认应用。'
        self.fit_validation_label.setText(hint)
        self.fit_page_button.setEnabled(has_experiment and selected > 0 and not errors and self._thread is None)
        return has_experiment and selected > 0 and not errors

    def start_run(self):
        if self._thread is not None:
            return
        try:
            config = self._comparison_config_from_ui()
            from material_test.protocols import history_from_protocol
            history_from_protocol(config.loading)
        except Exception as exc:
            QMessageBox.critical(self, '运行分析', str(exc)); return
        self._auto_timer.stop()
        self._run_generation = self._generation
        self._set_running(True, 'OpenSees 计算中…')
        self._append_log('开始材料分析 · ' + self.loading_source_combo.currentText())
        self._thread = QThread(self)
        self._worker = RunWorker(copy.deepcopy(config))
        self._start_worker(self._worker, self._run_finished)

    def start_fit(self):
        if self._thread is not None or not self._validate_fit_inputs():
            return
        try:
            config = self._config_from_ui(require_fitting=True, require_ready=True)
            config.fitting.validate()
        except Exception as exc:
            QMessageBox.critical(self, '参数拟合', str(exc)); return
        self._auto_timer.stop()
        self._run_generation = self._generation
        self._fit_original_values = self._material_from_ui().parameter_values.copy()
        self._set_running(True, '参数拟合中…')
        self._thread = QThread(self)
        self._worker = FitWorker(copy.deepcopy(config))
        self._worker.progress.connect(self._fit_progress_changed)
        self._start_worker(self._worker, self._fit_finished)

    def _fit_progress_changed(self, count, total, best):
        self.fit_progress.setRange(0, total)
        self.fit_progress.setValue(count)
        metric = f'最优 NRMSE {best:.5g}' if np.isfinite(best) else '尚无有效候选'
        self.fit_progress.setFormat(f'{count}/{total} 次 · {metric}')
        self._set_status(f'拟合评估 {count}/{total} · {metric}', 'running')

    def cancel_analysis(self):
        self._auto_run_pending = False
        self._rerun_after_fit = False
        self._auto_timer.stop()
        if self._worker is not None:
            self._worker.cancel()
            self._set_status('正在取消，请稍候…', 'waiting')

    def _run_cancelled(self):
        self._append_log('任务已取消，未应用未完成的计算或拟合结果。')
        self._set_status('已取消', 'waiting')

    def _start_worker(self, worker, finished_callback):
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.finished.connect(finished_callback)
        worker.failed.connect(self._run_failed)
        worker.cancelled.connect(self._run_cancelled)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(self._thread.quit)
            signal.connect(worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def _fit_finished(self, result):
        if self._run_generation != self._generation:
            self._append_log('拟合期间输入发生变化，保留原参数；请重新拟合。')
            self._set_status('拟合结果与当前输入不一致，未应用', 'stale')
            return
        self.fit_progress.setValue(self.fit_progress.maximum())
        self._pending_fit = result
        self.fitting_preview_plot.update_series([
            (result.experiment.displacement, result.experiment.force, '试验', '#111827'),
            (result.simulation.displacement, result.simulation.force, '拟合候选', '#1f5fae')],
            title='拟合候选（尚未应用）', x_label='位移', y_label='力')
        dialog = QDialog(self)
        dialog.setWindowTitle('确认拟合参数')
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f'拟合完成：NRMSE {result.metrics.normalized_rmse:.6g}。应用后自动计算完整加载历程。'))
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(['参数', '原值', '拟合值', '变化'])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for parameter in result.fitted_material.parameters:
            old = self._fit_original_values.get(parameter.name, parameter.value)
            row = table.rowCount(); table.insertRow(row)
            change = f'{(parameter.value-old)/abs(old)*100:+.3g}%' if old else '原值为零'
            for col, value in enumerate((parameter.name, f'{old:.10g}', f'{parameter.value:.10g}', change)):
                table.setItem(row, col, QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText('应用并计算')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('放弃')
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_fit_result(result)
        else:
            self._append_log('已放弃拟合候选，原参数保持不变。')
            self._set_status('拟合候选已放弃', 'waiting')

    def _apply_fit_result(self, result):
        self._last_fit_original = self._material_from_ui().parameter_values.copy()
        if self.current_result is not None and not self._comparison_dirty:
            self.baseline_result = self.current_result
        for parameter in result.fitted_material.parameters:
            self._set_material_parameter_value(parameter.name, parameter.value)
        self.undo_fit_button.setEnabled(True)
        self._auto_timer.stop()
        self._rerun_after_fit = True
        self._append_log('已应用拟合参数，准备完整加载复算。')
        if self._thread is None:
            self._rerun_after_fit = False
            QTimer.singleShot(0, self.start_run)

    def undo_fit(self):
        for name, value in getattr(self, '_last_fit_original', {}).items():
            self._set_material_parameter_value(name, value)
        self._last_fit_original = {}
        self.undo_fit_button.setEnabled(False)
        self._append_log('已恢复应用拟合前的参数。')

    def _run_finished(self, result):
        self.current_result = result
        self._comparison_dirty = self._run_generation != self._generation
        self._append_log(f'分析完成：{result.run_dir}')
        if result.simulation.stderr.strip():
            self._append_log(result.simulation.stderr)
        self._refresh_comparison_view()
        self._refresh_fitting_preview()
        self.stale_notice.setVisible(self._comparison_dirty)
        self._set_status('计算期间输入已修改，结果待更新' if self._comparison_dirty else '完整加载计算完成',
                         'stale' if self._comparison_dirty else 'complete')
        self._update_navigation_states()

    def _run_failed(self, message):
        self._append_log(message)
        self._set_status('运行失败', 'failed')
        self.log_dock.show()
        QMessageBox.critical(self, '运行失败', preview_error_summary(RuntimeError(message)) + '\n完整异常见底部运行日志。')

    def _thread_finished(self):
        self._thread = None
        self._worker = None
        self._set_running(False, self.run_state_label.text())
        if getattr(self, '_close_when_idle', False):
            self._close_when_idle = False
            QTimer.singleShot(0, self.close)
        elif getattr(self, '_rerun_after_fit', False):
            self._rerun_after_fit = False
            QTimer.singleShot(0, self.start_run)
        elif self._auto_run_pending:
            self._auto_run_pending = False
            self._auto_timer.start()

    def _set_running(self, running, text):
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.fit_cancel_button.setEnabled(running)
        self.load_project_action.setEnabled(not running)
        self.undo_fit_button.setEnabled(not running and bool(getattr(self, '_last_fit_original', {})))
        ready = not running and isinstance(self.current_result, WorkflowResult) and not self._comparison_dirty
        for button in (self.publish_button, self.set_baseline_button, self.export_button):
            button.setEnabled(ready)
        self.clear_baseline_button.setEnabled(not running and self.baseline_result is not None)
        if running:
            self.fit_page_button.setEnabled(False)
            self._set_status(text, 'running')
        else:
            self._validate_fit_inputs()

    def publish_brace(self):
        if self._thread is not None or not isinstance(self.current_result, WorkflowResult) or self._comparison_dirty:
            QMessageBox.information(self, '发布支撑模型', '请先完成当前输入的完整加载计算。试验数据可选。')
            return
        try:
            config = self._comparison_config_from_ui()
            package = build_brace_package(config, result_dir=self.current_result.run_dir)
            path, _ = QFileDialog.getSaveFileName(self, '发布支撑模型', str(self.current_result.run_dir / 'support.oasbrace'), '支撑模型包 (*.oasbrace)')
            if not path:
                return
            Path(path).write_text(json.dumps(package, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            self._append_log(f'已发布支撑包：{path} · {package["evidence"]["description"]}')
            QMessageBox.information(self, '支撑模型已发布', f'已保存：{path}\n来源：{package["evidence"]["description"]}')
        except (ValueError, OSError, KeyError) as exc:
            QMessageBox.warning(self, '无法发布支撑模型', str(exc))
