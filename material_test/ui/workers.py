from __future__ import annotations

import traceback
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from material_test.domain import ProjectConfig
from material_test.fitting import FitResult, fit_project
from material_test.workflow import WorkflowResult, run_project
from material_test.cancellation import AnalysisCancelled


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
