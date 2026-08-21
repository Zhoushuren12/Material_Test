from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from material_test.domain import ExperimentData, SimulationResult
from material_test.plotting import create_comparison_figure, save_comparison_plot


class ComparisonPlotTests(unittest.TestCase):
    def setUp(self) -> None:
        displacement = np.array([0.0, 1.0, 0.0, -1.0, 0.0])
        self.experiment = ExperimentData(
            displacement,
            np.array([0.0, 10.0, 0.0, -10.0, 0.0]),
        )
        self.current = self._simulation(displacement, self.experiment.force * 0.9)
        self.baseline = self._simulation(displacement, self.experiment.force * 0.8)

    def test_comparison_figure_uses_publication_axis_style(self) -> None:
        figure = create_comparison_figure(self.current, self.experiment, self.baseline)
        axes = figure.axes[0]

        self.assertEqual(axes.get_title(), "")
        self.assertEqual(axes.get_xlabel(), "Displacement")
        self.assertEqual(axes.get_ylabel(), "Force")
        self.assertEqual(axes.xaxis.label.get_fontsize(), 25.0)
        self.assertEqual(axes.yaxis.label.get_fontsize(), 25.0)
        self.assertEqual(axes.get_xticklabels()[0].get_fontsize(), 18.0)
        self.assertEqual(axes.get_yticklabels()[0].get_fontsize(), 18.0)
        self.assertEqual(len(axes.xaxis.get_minorticklocs()), 0)
        self.assertEqual(len(axes.yaxis.get_minorticklocs()), 0)
        self.assertAlmostEqual(axes.get_xlim()[0], axes.get_xticks()[0])
        self.assertAlmostEqual(axes.get_xlim()[1], axes.get_xticks()[-1])
        self.assertAlmostEqual(axes.get_ylim()[0], axes.get_yticks()[0])
        self.assertAlmostEqual(axes.get_ylim()[1], axes.get_yticks()[-1])

    def test_current_comparison_exports_at_300_dpi(self) -> None:
        with TemporaryDirectory(prefix="comparison_export_") as temp_dir:
            output = Path(temp_dir) / "comparison.png"
            saved = save_comparison_plot(output, self.current, self.experiment, self.baseline)

            self.assertEqual(saved, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 10_000)

    @staticmethod
    def _simulation(displacement: np.ndarray, force: np.ndarray) -> SimulationResult:
        base = Path("unused")
        return SimulationResult(
            displacement=displacement,
            force=force,
            tcl_file=base / "model.tcl",
            history_file=base / "history.txt",
            response_file=base / "response.txt",
        )


if __name__ == "__main__":
    unittest.main()
