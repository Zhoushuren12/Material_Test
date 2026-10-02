from __future__ import annotations
from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QWidget, QSpinBox, QDoubleSpinBox, QAbstractSpinBox

class MaterialTagSpinBox(QSpinBox):
    """A spin box whose arrows move through the loaded material tags."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[int] = []
        self.lineEdit().setReadOnly(True)

    def set_material_tags(self, tags: list[int], selected: int | None = None) -> None:
        self._tags = sorted(set(int(tag) for tag in tags))
        self.blockSignals(True)
        if self._tags:
            self.setRange(self._tags[0], self._tags[-1])
            value = selected if selected in self._tags else self._tags[0]
            self.setValue(value)
        else:
            self.setRange(1, 1)
            self.setValue(1)
        self.blockSignals(False)

    def stepBy(self, steps: int) -> None:
        if not self._tags:
            return
        try:
            index = self._tags.index(int(self.value()))
        except ValueError:
            index = 0
        index = max(0, min(len(self._tags) - 1, index + (1 if steps > 0 else -1)))
        self.setValue(self._tags[index])

    def stepEnabled(self) -> QAbstractSpinBox.StepEnabledFlag:
        if not self._tags:
            return QAbstractSpinBox.StepEnabledFlag.StepNone
        flags = QAbstractSpinBox.StepEnabledFlag.StepNone
        try:
            index = self._tags.index(int(self.value()))
        except ValueError:
            return QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        if index > 0:
            flags |= QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        if index < len(self._tags) - 1:
            flags |= QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        return flags


class CompactDoubleSpinBox(QDoubleSpinBox):
    """Compact numeric editor that accepts decimal and scientific notation."""

    _number_expression = QRegularExpression(
        r"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d*)?"
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scientific_validator = QRegularExpressionValidator(
            self._number_expression,
            self,
        )

    def textFromValue(self, value: float) -> str:
        return f"{value:.12g}"

    def valueFromText(self, text: str) -> float:
        return float(text.strip())

    def validate(self, text: str, position: int):
        return self._scientific_validator.validate(text.strip(), position)
