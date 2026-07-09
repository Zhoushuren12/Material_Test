from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from material_test.io.experiment_data import list_data_columns, load_experiment_data


class ExperimentDataTests(unittest.TestCase):
    def test_load_csv_with_column_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment.csv"
            path.write_text("u,f\n0,0\n1,10\n2,20\n", encoding="utf-8")

            self.assertEqual(list_data_columns(path), ["u", "f"])
            data = load_experiment_data(path, displacement_column="u", force_column="f")

        self.assertEqual(data.displacement.tolist(), [0.0, 1.0, 2.0])
        self.assertEqual(data.force.tolist(), [0.0, 10.0, 20.0])

    def test_non_numeric_data_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "experiment.csv"
            path.write_text("u,f\n0,0\nbad,10\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_experiment_data(path, displacement_column="u", force_column="f")


if __name__ == "__main__":
    unittest.main()
