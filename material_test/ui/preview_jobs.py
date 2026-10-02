"""Lazy material previews; all widget updates remain on the GUI thread."""
from collections import OrderedDict
from threading import Event
from PySide6.QtCore import QObject, QRunnable, Signal
from material_test.material_preview import calculate_material_preview, MaterialPreviewCurve, preview_error_summary

PREVIEW_CACHE = OrderedDict()


class PreviewSignals(QObject):
    finished = Signal(str, object)


class PreviewJob(QRunnable):
    def __init__(self, entry, key, solver, area, length, cancel_event):
        super().__init__()
        self.entry, self.key, self.solver = entry, key, solver
        self.area, self.length, self.cancel_event = area, length, cancel_event
        self.signals = PreviewSignals()

    def run(self):
        if self.cancel_event.is_set():
            return
        try:
            curve = calculate_material_preview(solver=self.solver, material=self.entry.as_template(),
                material_type=self.entry.name, area=self.area, length=self.length,
                cancel_check=self.cancel_event.is_set)
        except Exception as exc:
            curve = MaterialPreviewCurve(None, None, preview_error_summary(exc))
        if not self.cancel_event.is_set():
            if curve.available:
                PREVIEW_CACHE[self.key] = curve
                while len(PREVIEW_CACHE) > 128:
                    PREVIEW_CACHE.popitem(last=False)
            self.signals.finished.emit(self.entry.id, curve)
