from __future__ import annotations

import traceback
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from material_test.domain import ProjectConfig
from material_test.fitting import FitResult, fit_project
from material_test.workflow import WorkflowResult, run_project
from material_test.cancellation import AnalysisCancelled


class WorkerCallbacks(QObject):
    """Give queued callbacks a concrete GUI-thread QObject receiver."""
    def __init__(self, window, finished_callback):
        super().__init__(window)
        self.window = window
        self.finished_callback = finished_callback

    @Slot(object)
    def finished(self,result):
        self.finished_callback(result)

    @Slot(str)
    def failed(self,message):
        self.window._run_failed(message)

    @Slot()
    def cancelled(self):
        self.window._run_cancelled()

    @Slot(int,int,float)
    def progress(self,count,total,best):
        self.window._fit_progress_changed(count,total,best)

    @Slot()
    def thread_finished(self):
        self.window._thread_finished()
        self.deleteLater()


class RunWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.config = config
        self.cancel_event = Event()

    def cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result: WorkflowResult = run_project(self.config, cancel_check=self.cancel_event.is_set)
        except AnalysisCancelled:
            self.cancelled.emit()
            return
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(result)


class FitWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(int, int, float)

    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.config = config
        self.cancel_event = Event()

    def cancel(self):
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result: FitResult = fit_project(self.config, progress=self.progress.emit, cancel_check=self.cancel_event.is_set)
        except AnalysisCancelled:
            self.cancelled.emit()
            return
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(result)
