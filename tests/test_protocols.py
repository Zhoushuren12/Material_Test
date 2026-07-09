from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

import numpy as np

from material_test.domain import LoadingProtocol
from material_test.protocols import generate_amp
from material_test.protocols import history_from_protocol


class ProtocolTests(unittest.TestCase):
    def test_generate_amp_reaches_requested_points(self) -> None:
        history = generate_amp([0.0, 1.0, -1.0, 0.0], 0.5)
        self.assertTrue(np.allclose(history, [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.0]))

    def test_generate_amp_rejects_bad_inputs(self) -> None:
        with self.assertRaises(ValueError):
            generate_amp([0.0], 0.5)
        with self.assertRaises(ValueError):
            generate_amp([0.0, 1.0], 0.0)

    def test_history_from_txt_column_can_append_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.txt"
            path.write_text("0 0\n1 10\n2 20\n", encoding="utf-8")
            history = history_from_protocol(
                LoadingProtocol(history_path=path, history_column="0", append_zero=True)
            )

        self.assertTrue(np.allclose(history, [0.0, 1.0, 2.0, 0.0]))


if __name__ == "__main__":
    unittest.main()
