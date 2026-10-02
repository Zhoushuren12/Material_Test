from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from material_test.domain import LoadingProtocol
from material_test.io.experiment_data import load_numeric_column


def generate_amp(amplitudes: Sequence[float], du: float) -> np.ndarray:
    """Generate a displacement history through the requested amplitude points."""
    if len(amplitudes) < 2:
        raise ValueError("amplitudes must contain at least two values")

    du = float(du)
    if not np.isfinite(du) or du <= 0.0:
        raise ValueError("du must be positive")
    if not np.isfinite(amplitudes).all():
        raise ValueError("加载幅值必须为有限数值")
    if np.sum(np.abs(np.diff(amplitudes)) / du) > 1_000_000:
        raise ValueError("加载点超过 100 万，请增大位移增量或缩短加载历程")

    history = [float(amplitudes[0])]
    for start, end in zip(amplitudes[:-1], amplitudes[1:]):
        start = float(start)
        end = float(end)
        delta = end - start
        if abs(delta) < 1e-14:
            continue

        direction = 1.0 if delta > 0.0 else -1.0
        n_full_steps = int(abs(delta) // du)
        for step in range(1, n_full_steps + 1):
            value = start + direction * du * step
            if (direction > 0.0 and value < end) or (direction < 0.0 and value > end):
                history.append(value)

        if not np.isclose(history[-1], end):
            history.append(end)

    return np.asarray(history, dtype=float)


def history_from_protocol(protocol: LoadingProtocol) -> np.ndarray:
    source = protocol.source
    if source == "auto":
        source = "external" if protocol.history_path is not None else "amplitudes"
    if source not in {"amplitudes", "external", "experiment"}:
        raise ValueError("请选择幅值序列、外部文件或试验位移作为加载来源")
    if source in {"external", "experiment"}:
        if protocol.history_path is None:
            raise ValueError("所选加载来源缺少数据文件")
        if not np.isfinite(protocol.history_scale) or protocol.history_scale == 0:
            raise ValueError("加载换算系数必须为有限非零数值")
        if protocol.history_column is None:
            raise ValueError("loading.history.displacement_column is required when loading.history.path is set")
        history = load_numeric_column(
            protocol.history_path,
            column=protocol.history_column,
            sheet_name=protocol.history_sheet,
            label="loading history",
        ).to_numpy(dtype=float) * protocol.history_scale
        if history.size < 2 or not np.isfinite(history).all():
            raise ValueError("加载历程至少需要两个有限数值")
        if protocol.append_zero and not np.isclose(history[-1], 0.0):
            history = np.r_[history, 0.0]
        return history

    history = generate_amp(protocol.amplitudes, protocol.du)
    if protocol.append_zero and not np.isclose(history[-1], 0.0):
        history = np.r_[history, 0.0]
    return history
