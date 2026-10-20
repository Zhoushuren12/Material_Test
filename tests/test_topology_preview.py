from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from material_test.domain import TopologyNode
from material_test.ui.topology_editor import TopologyEditor
from material_test.ui.topology_preview import TopologyPreview


class TopologyPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.preview = TopologyPreview()
        self.preview.resize(700, 330)
        self.preview.set_topology(TopologyNode.material(1, name="SMA-UD-09"))
        self.preview.show()
        self.app.processEvents()

    def tearDown(self):
        self.preview.close()

    def test_wheel_zooms_around_cursor_and_can_zoom_out(self):
        viewport = self.preview.viewport()
        position = QPointF(210, 140)
        before = self.preview.mapToScene(position.toPoint())
        original_scale = self.preview.transform().m11()
        for delta in (120, -120):
            event = QWheelEvent(position, QPointF(viewport.mapToGlobal(position.toPoint())),
                                QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(viewport, event)
            if delta > 0:
                self.assertGreater(self.preview.transform().m11(), original_scale)
                after = self.preview.mapToScene(position.toPoint())
                self.assertLess((after - before).manhattanLength(), 2)
        self.assertAlmostEqual(self.preview.transform().m11(), original_scale)

    def test_left_drag_pans_and_double_click_restores_fit(self):
        viewport = self.preview.viewport()
        start, end = QPoint(300, 150), QPoint(390, 170)
        fitted_scale = self.preview.transform().m11()
        center_before = self.preview.mapToScene(viewport.rect().center())
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(end),
                           QPointF(viewport.mapToGlobal(end)), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(viewport, move)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        center_after = self.preview.mapToScene(viewport.rect().center())
        self.assertGreater((center_after - center_before).manhattanLength(), 30)
        self.assertLess(center_after.x(), center_before.x())
        self.preview.zoom_by(2)
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=start)
        self.assertTrue(self.preview._auto_fit)
        self.assertAlmostEqual(self.preview.transform().m11(), fitted_scale)

    def test_twenty_branches_and_nested_series_do_not_overlap_or_clip(self):
        branch = TopologyNode.group("parallel", [
            TopologyNode.material(309 + i, name=f"SMA-UD-{i + 9:02d}") for i in range(20)
        ])
        root = TopologyNode.group("series", [branch, TopologyNode.group("parallel", [
            TopologyNode.group("series", [TopologyNode.material(1), TopologyNode.material(2)]),
            TopologyNode.material(3),
        ])])
        self.preview.set_topology(root)
        cards = [item for item in self.preview.scene().items() if item.data(0) == "material"]
        self.assertEqual(len(cards), 23)
        for i, item in enumerate(cards):
            for other in cards[i + 1:]:
                self.assertFalse(item.sceneBoundingRect().intersects(other.sceneBoundingRect()))
        visible = self.preview.mapToScene(self.preview.viewport().rect()).boundingRect()
        self.assertTrue(visible.contains(self.preview._content_rect))

    def test_assignment_edit_preserves_user_view(self):
        self.preview.zoom_by(1.5)
        scale = self.preview.transform().m11()
        center = self.preview.mapToScene(self.preview.viewport().rect().center())
        self.preview.set_topology(TopologyNode.material(2, name="SMA-UD-09"))
        self.assertAlmostEqual(self.preview.transform().m11(), scale)
        after = self.preview.mapToScene(self.preview.viewport().rect().center())
        self.assertLess((after - center).manhattanLength(), 2)

    def test_unassigned_and_invalid_models_have_visible_feedback(self):
        summary = []
        self.preview.summary_changed.connect(summary.append)
        self.preview.set_topology(TopologyNode(kind="material", name="E01"))
        self.assertIn("未赋值", summary[-1])
        self.assertEqual(len([i for i in self.preview.scene().items() if i.data(0) == "material"]), 1)
        self.preview.set_topology(TopologyNode(kind="series", children=[]))
        self.assertTrue(self.preview._error)
        self.assertEqual(summary[-1], "拓扑待检查")

    def test_zoom_and_fit_controls_do_not_modify_model(self):
        editor = TopologyEditor()
        changed = []
        editor.changed.connect(lambda: changed.append(True))
        editor.preview.zoom_by(2)
        editor.preview.fit_to_view()
        self.assertFalse(changed)
        editor.close()


if __name__ == "__main__":
    unittest.main()
