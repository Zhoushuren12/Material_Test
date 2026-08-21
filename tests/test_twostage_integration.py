from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from material_test.io.project_config import load_project_config
from material_test.io.material_library import load_material_library
from material_test.material_preview import calculate_material_preview
from material_test.domain import SolverConfig
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

    @unittest.skipUnless((ROOT / "OpenSees.exe").exists(), "OpenSees.exe is not available")
    def test_bundled_tcl_library_is_discovered_for_material_previews(self) -> None:
        solver = SolverConfig(executable_path=ROOT / "OpenSees.exe")
        library = load_material_library()

        for entry_id in ("elastic", "steel01", "sma"):
            with self.subTest(material=entry_id):
                entry = library.get(entry_id)
                curve = calculate_material_preview(
                    solver=solver,
                    # Library additions use the Chinese display name, which
                    # must not leak into the OpenSees script filename.
                    material=entry.instantiate(tag=1),
                    material_type=entry.name,
                )
                self.assertTrue(curve.available)
                self.assertGreater(len(curve.displacement), 2)
                self.assertTrue(np.isfinite(curve.force).all())

    @unittest.skipUnless((ROOT / "OpenSees.exe").exists(), "OpenSees.exe is not available")
    def test_nonlinear_library_previews_are_not_straight_lines(self) -> None:
        solver = SolverConfig(executable_path=ROOT / "OpenSees.exe")
        library = load_material_library()

        for entry_id in ("sma", "self_centering", "steel01", "steel02", "hysteretic"):
            with self.subTest(material=entry_id):
                entry = library.get(entry_id)
                curve = calculate_material_preview(
                    solver=solver,
                    material=entry.as_template(),
                    material_type=entry.name,
                )
                slopes = np.diff(curve.force) / np.diff(curve.displacement)
                slopes = slopes[np.isfinite(slopes)]
                rounded = np.unique(np.round(slopes, decimals=3))
                self.assertGreater(len(rounded), 1)


if __name__ == "__main__":
    unittest.main()
