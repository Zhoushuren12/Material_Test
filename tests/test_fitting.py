from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from material_test.domain import (
    ExperimentData,
    FitConfig,
    FitParameterSpec,
    LoadingProtocol,
    MaterialTemplate,
    ParameterSpec,
    ProjectConfig,
    SimulationResult,
    SolverConfig,
)
from material_test.fitting import fit_project


class FittingTests(unittest.TestCase):
    def test_de_and_pso_fit_selected_parameter_and_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_file = root / "experiment.csv"
            data_file.write_text("u,f\n0,0\n1,10\n2,20\n3,30\n", encoding="utf-8")

            for algorithm in ("de", "pso"):
                config = self._config(root, data_file, algorithm)
                progress = []
                with patch("material_test.fitting.run_material_history", side_effect=self._fake_runner):
                    result = fit_project(config, run_dir=root / algorithm, progress=lambda *event: progress.append(event))

                self.assertAlmostEqual(result.fitted_material.parameter_values["k"], 10.0, places=5)
                self.assertLess(result.metrics.normalized_rmse, 1.0e-6)
                self.assertTrue(result.material_file.exists())
                self.assertTrue(result.summary_file.exists())
                self.assertTrue(result.figure_file.exists())
                self.assertGreater(result.global_evaluations, 0)
                self.assertEqual(progress[-1][0], result.total_evaluations)
                self.assertTrue(np.isfinite(progress[-1][2]))

    def test_both_searches_preserve_a_good_current_model_with_small_budget(self) -> None:
        """A random population must not replace an already fitting model."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_file = root / "experiment.csv"
            data_file.write_text("u,f\n0,0\n1,10\n2,20\n3,30\n", encoding="utf-8")
            for algorithm in ("de", "pso"):
                for scale in ("linear", "log"):
                    with self.subTest(algorithm=algorithm, scale=scale):
                        config = self._config(root, data_file, algorithm)
                        config.material.set_parameter_value("k", 10.)
                        config.fitting.parameters = [FitParameterSpec("k", 1., 1000., scale)]
                        config.fitting.max_evaluations = 8
                        config.fitting.local_refine = False
                        seen = []
                        def runner(**kwargs):
                            seen.append(kwargs["material"].parameter_values["k"])
                            return self._fake_runner(**kwargs)
                        with patch("material_test.fitting.run_material_history", side_effect=runner):
                            result = fit_project(config, run_dir=root / (algorithm + "-" + scale))
                        self.assertAlmostEqual(seen[0], 10., places=10)
                        self.assertAlmostEqual(result.fitted_material.parameter_values["k"], 10., places=10)
                        self.assertLess(result.metrics.rmse, 1e-9)
                        self.assertLessEqual(result.global_evaluations, 8)

    def test_local_refinement_uses_and_persists_configured_difference_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_file = root / "experiment.csv"
            data_file.write_text("u,f\n0,0\n1,10\n2,20\n3,30\n", encoding="utf-8")
            config = self._config(root, data_file, "de")
            config.fitting.local_diff_step = .0003
            mapping = config.fitting.to_mapping()
            restored = FitConfig.from_mapping(mapping)
            self.assertEqual(restored.local_diff_step, .0003)
            self.assertEqual(FitConfig.from_mapping({"parameters": mapping["parameters"]}).local_diff_step, .0001)
            from scipy.optimize import least_squares
            with patch("material_test.fitting.run_material_history", side_effect=self._fake_runner), \
                 patch("material_test.fitting.least_squares", wraps=least_squares) as refine:
                fit_project(config, run_dir=root / "step")
            self.assertEqual(refine.call_args.kwargs["diff_step"], .0003)
            for bad in (0., -1., float("nan"), float("inf")):
                config.fitting.local_diff_step = bad
                with self.assertRaises(ValueError):
                    config.fitting.validate()

    @staticmethod
    def _config(root: Path, data_file: Path, algorithm: str) -> ProjectConfig:
        return ProjectConfig(
            solver=SolverConfig(executable_path=root / "unused-opensees.exe"),
            material=MaterialTemplate(
                name="Elastic",
                command="uniaxialMaterial Elastic {tag} {k}",
                parameters=[ParameterSpec("k", 4.0)],
            ),
            loading=LoadingProtocol(amplitudes=[0.0, 1.0], du=1.0),
            experiment_path=data_file,
            displacement_column="u",
            force_column="f",
            output_dir=root,
            fitting=FitConfig(
                parameters=[FitParameterSpec("k", 1.0, 20.0)],
                algorithm=algorithm,
                max_evaluations=24,
                population_size=6,
                local_refine=True,
                local_max_evaluations=30,
                seed=7,
            ),
        )

    @staticmethod
    def _fake_runner(**kwargs) -> SimulationResult:
        material = kwargs["material"]
        displacement = np.asarray(kwargs["displacement_history"], dtype=float)
        force = material.parameter_values["k"] * displacement
        work_dir = kwargs["work_dir"]
        return SimulationResult(
            displacement=displacement,
            force=force,
            tcl_file=work_dir / "model.tcl",
            history_file=work_dir / "history.txt",
            response_file=work_dir / "response.txt",
        )


if __name__ == "__main__":
    unittest.main()
