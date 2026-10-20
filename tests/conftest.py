"""Isolate GUI preferences from the user's saved session during tests."""
import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def session_settings(tmp_path, monkeypatch):
    filename = str(tmp_path / 'session.ini')
    monkeypatch.setattr('material_test.ui.project_session.QSettings',
                        lambda *_: QSettings(filename, QSettings.Format.IniFormat))
    return QSettings(filename, QSettings.Format.IniFormat)
