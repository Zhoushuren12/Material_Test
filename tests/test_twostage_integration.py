from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from material_test.io.project_config import load_project_config
from material_test.workflow import run_project


ROOT = Path(__file__).resolve().parents[1]


class TwoStageIntegrationTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "OpenSees.exe").exists(), "OpenSees.exe is not available")
    def test_twostage_demo_runs_with_generic_workflow(self) -> None:
        config = load_project_config(ROOT / "examples" / "projects" / "twostage_demo.yaml")
        with tempfile.TemporaryDirectory() as tmp:
            result = run_project(config, run_dir=Path(tmp) / "run")

            self.assertTrue(result.simulation.tcl_file.exists())
            self.assertTrue(result.simulation.response_file.exists())
            self.assertTrue(result.figure_file.exists())
            self.assertGreater(result.figure_file.stat().st_size, 0)
            self.assertTrue(np.isfinite(result.simulation.force).all())
            self.assertLess(result.simulation.force.min(), -500.0)
            self.assertGreater(result.simulation.force.max(), 500.0)


if __name__ == "__main__":
    unittest.main()
