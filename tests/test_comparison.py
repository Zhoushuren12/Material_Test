from __future__ import annotations

import unittest

import numpy as np

from material_test.comparison import calculate_curve_metrics, calculate_error_metrics


class ComparisonMetricTests(unittest.TestCase):
    def test_curve_metrics_report_initial_stiffness_and_peak_force(self) -> None:
        displacement = np.array([0.0, 0.01, 0.02, 0.01, 0.0, -0.01, -0.02, 0.0])
        force = 100.0 * displacement

        metrics = calculate_curve_metrics(displacement, force)

        self.assertAlmostEqual(metrics.peak_absolute_force, 2.0)
        self.assertAlmostEqual(metrics.initial_stiffness, 100.0)
        self.assertAlmostEqual(metrics.hysteretic_integral, 0.0)
        self.assertAlmostEqual(metrics.residual_force, 0.0)

    def test_error_metrics_require_matching_sample_counts(self) -> None:
        metrics = calculate_error_metrics(np.array([0.0, 2.0]), np.array([0.0, 1.0]))
        self.assertIsNotNone(metrics)
        self.assertAlmostEqual(metrics.rmse, np.sqrt(0.5))
        self.assertIsNone(calculate_error_metrics(np.array([0.0]), np.array([0.0, 1.0])))


if __name__ == "__main__":
    unittest.main()
