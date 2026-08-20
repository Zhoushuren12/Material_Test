from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from material_test.domain import ProjectConfig
from material_test.fitting import FitResult, fit_project
from material_test.workflow import WorkflowResult, run_project


class RunWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.config = config

    @Slot()
    def run(self) -> None:
        try:
            result: WorkflowResult = run_project(self.config)
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(result)


class FitWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, config: ProjectConfig) -> None:
        super().__init__()
        self.config = config

    @Slot()
    def run(self) -> None:
        try:
            result: FitResult = fit_project(self.config)
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.finished.emit(result)
