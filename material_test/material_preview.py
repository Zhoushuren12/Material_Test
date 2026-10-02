from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from material_test.domain import MaterialTemplate, ModelingConfig, SolverConfig
from material_test.opensees.runner import run_material_history
from material_test.protocols import generate_amp


@dataclass(frozen=True)
class MaterialPreviewCurve:
    displacement: np.ndarray | None
    force: np.ndarray | None
    note: str = ""

    @property
    def available(self) -> bool:
        return self.displacement is not None and self.force is not None


def preview_error_summary(error: Exception) -> str:
    """Return a compact UI message while callers retain the full diagnostic."""
    message = str(error)
    if "Traceback (most recent call last):" in message:
        # Workers send formatted tracebacks; the first line is only a heading.
        # Read the exception after the final stack, including chained errors.
        lines = message.rsplit("Traceback (most recent call last):", 1)[1].splitlines()
        for index, line in enumerate(lines):
            if line.strip() and not line[0].isspace():
                message = "\n".join(lines[index:])
                break
    lowered = message.lower()
    if "can't find a usable init.tcl" in lowered or "tcl initialization failed" in lowered:
        return "OpenSees 的 Tcl 运行库不可用，请在项目设置中检查 Tcl 库路径。"
    if "did not write a response file" in lowered:
        return "OpenSees 未生成响应文件，请检查求解器配置；详细信息见运行日志。"
    if "opensees failed" in lowered:
        return "OpenSees 计算失败；详细信息见运行日志。"
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "未知错误")
    if len(first_line) > 96:
        first_line = first_line[:93] + "..."
    return f"计算失败：{first_line}"


_AMPLITUDES = {
    "Elastic": (0.005, "symmetric"),
    "SMA": (6.0, "symmetric"),
    "SelfCentering": (6.0, "symmetric"),
    "Steel01": (0.006, "symmetric"),
    "Steel02": (0.006, "symmetric"),
    "ElasticPPGap": (0.004, "symmetric"),
    "Hysteretic": (5.0, "symmetric"),
    "Concrete02": (0.006, "compression"),
    "ViscoelasticGap": (0.004, "compression"),
    "TwoStage": (30.0, "symmetric"),
    "Series": (0.012, "symmetric"),
    "Parallel": (0.006, "symmetric"),
    "MinMax": (0.006, "symmetric"),
}


def standard_preview_history(material_type: str) -> np.ndarray | None:
    if material_type == "Viscous":
        return None
    amplitude, mode = _AMPLITUDES.get(material_type, (1.0, "symmetric"))
    if mode == "compression":
        turning_points = [0.0, -amplitude / 3.0, 0.0, -2.0 * amplitude / 3.0, 0.0, -amplitude, 0.0]
    else:
        turning_points = [
            0.0, amplitude / 3.0, 0.0, -amplitude / 3.0, 0.0,
            2.0 * amplitude / 3.0, 0.0, -2.0 * amplitude / 3.0, 0.0,
            amplitude, 0.0, -amplitude, 0.0,
        ]
    return generate_amp(turning_points, amplitude / 30.0)


def calculate_material_preview(
    *,
    solver: SolverConfig,
    material: MaterialTemplate,
    material_type: str,
    area: float = 1.0,
    length: float = 1.0,
    displacement_history: np.ndarray | None = None,
    modeling: ModelingConfig | None = None,
    material_mode: str = "stress_strain",
    cancel_check=None,
) -> MaterialPreviewCurve:
    if material_type == "Viscous":
        return MaterialPreviewCurve(
            None,
            None,
            "Viscous 是纯速度相关材料，需使用包含物理时间步的瞬态分析；静力位移预览无效。",
        )
    history = displacement_history
    if history is None:
        history = standard_preview_history(material_type)
    if history is None:
        return MaterialPreviewCurve(None, None, "当前材料没有可用的标准静力预览协议。")
    with TemporaryDirectory(prefix="material_preview_") as work_dir:
        result = run_material_history(
            solver=solver,
            material=material,
            displacement_history=np.asarray(history, dtype=float),
            work_dir=Path(work_dir),
            area=area,
            length=length,
            modeling=modeling,
            material_mode=material_mode,
            cancel_check=cancel_check,
        )
    note = ""
    if material_type == "ViscoelasticGap":
        note = "静力预览仅显示接触骨架；阻尼参数 c 仍需通过瞬态碰撞协议标定。"
    return MaterialPreviewCurve(result.displacement, result.force, note)
