from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from material_test.opensees.tcl_writer import write_material_test_tcl


class TclWriterTests(unittest.TestCase):
    def test_writes_material_command_and_response_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tcl_file = root / "model.tcl"
            history_file = root / "history.txt"
            response_file = root / "response.txt"
            write_material_test_tcl(
                tcl_file=tcl_file,
                history_file=history_file,
                response_file=response_file,
                material_command="uniaxialMaterial Elastic 1 100",
                n_steps=3,
                test_material_tag=7,
            )
            text = tcl_file.read_text(encoding="utf-8")

        self.assertIn("uniaxialMaterial Elastic 1 100", text)
        self.assertIn("element Truss 1 1 2 1 7", text)
        self.assertIn("response.txt", text)


if __name__ == "__main__":
    unittest.main()
