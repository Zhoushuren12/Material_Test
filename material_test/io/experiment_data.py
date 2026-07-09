from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from material_test.domain import ExperimentData


def list_data_columns(path: str | Path, *, sheet_name: str | int | None = 0) -> list[str]:
    path = Path(path)
    frame = _read_table(path, sheet_name=sheet_name, nrows=0)
    return [str(column) for column in frame.columns]


def load_experiment_data(
    path: str | Path,
    *,
    displacement_column: str | int,
    force_column: str | int,
    sheet_name: str | int | None = 0,
) -> ExperimentData:
    path = Path(path)
    frame = _read_table(path, sheet_name=sheet_name)
    displacement = load_numeric_column_from_frame(frame, displacement_column, "displacement")
    force = load_numeric_column_from_frame(frame, force_column, "force")
    return ExperimentData(
        displacement=displacement,
        force=force,
        source_path=path,
        displacement_column=displacement_column,
        force_column=force_column,
    )


def load_numeric_column(
    path: str | Path,
    *,
    column: str | int,
    sheet_name: str | int | None = 0,
    label: str = "data",
) -> pd.Series:
    frame = _read_table(Path(path), sheet_name=sheet_name)
    return load_numeric_column_from_frame(frame, column, label)


def load_numeric_column_from_frame(frame: pd.DataFrame, column: str | int, label: str) -> pd.Series:
    return _select_numeric_column(frame, column, label)


def _read_table(path: Path, *, sheet_name: str | int | None = 0, nrows: int | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path, nrows=nrows)
        frame.columns = [str(column) for column in frame.columns]
        return frame
    if suffix == ".txt":
        frame = pd.read_csv(path, sep=r"\s+", header=None, nrows=nrows)
        frame.columns = [str(column) for column in frame.columns]
        return frame
    if suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(path, sheet_name=sheet_name, nrows=nrows)
        frame.columns = [str(column) for column in frame.columns]
        return frame
    raise ValueError(f"unsupported experiment data file type: {path.suffix}")


def _select_numeric_column(frame: pd.DataFrame, column: str | int, label: str) -> pd.Series:
    if isinstance(column, int):
        try:
            series = frame.iloc[:, column]
        except IndexError as exc:
            raise ValueError(f"{label} column index out of range: {column}") from exc
    else:
        if column not in frame.columns:
            raise ValueError(f"{label} column not found: {column}")
        series = frame[column]

    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.isna().any():
        bad_count = int(numeric.isna().sum())
        raise ValueError(f"{label} column contains {bad_count} non-numeric value(s)")
    return numeric
