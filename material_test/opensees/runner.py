from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import numpy as np

from material_test.domain import MaterialTemplate, ModelingConfig, SimulationResult, SolverConfig
from material_test.cancellation import AnalysisCancelled
from material_test.modeling import defined_material_tags
from material_test.material_commands import validate_solver_materials
from material_test.opensees.tcl_writer import write_material_test_tcl


def run_material_history(
    *,
    solver: SolverConfig,
    material: MaterialTemplate,
    displacement_history: np.ndarray,
    work_dir: Path,
    area: float = 1.0,
    length: float = 1.0,
    modeling: ModelingConfig | None = None,
    material_mode: str = "stress_strain",
    force_unit: str = "N",
    length_unit: str = "mm",
    cancel_check=None,
    record_deformation: bool = True,
) -> SimulationResult:
    """Run a material test with a configured OpenSees executable."""
    if cancel_check and cancel_check():
        raise AnalysisCancelled("计算已取消")
    validate_solver_materials(material)
    if modeling is not None and modeling.assembly is not None:
        from material_test.explicit_assembly import run_explicit_history
        return run_explicit_history(solver=solver, material=material, modeling=modeling,
                                    displacement_history=displacement_history, work_dir=work_dir,
                                    force_unit=force_unit,length_unit=length_unit,
                                    cancel_check=cancel_check, record_deformation=record_deformation)
    executable = solver.executable_path.resolve()
    if not executable.exists():
        raise FileNotFoundError(f"OpenSees executable not found: {executable}")

    tcl_library = _resolve_tcl_library(solver, executable)

    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    history = np.asarray(displacement_history, dtype=float).ravel()
    if history.size < 2:
        raise ValueError("displacement_history must contain at least two points")
    if not np.isclose(history[0], 0.0):
        history = np.insert(history, 0, 0.0)

    stem = _safe_stem(material.name)
    history_file = work_dir / f"{stem}_history.txt"
    response_file = work_dir / f"{stem}_response.txt"
    tcl_file = work_dir / f"{stem}_model.tcl"

    # Path time series advances after each analysis step; append one stable value
    # while the Tcl loop records the current state before each step.
    np.savetxt(history_file, np.r_[history, history[-1]], fmt="%.12g")
    modeling = modeling or ModelingConfig()
    modeling.validate()
    material_command = material.render_command()
    topology_root = modeling.resolved_root(material.tag)
    missing_tags = sorted(set(topology_root.material_tags()) - defined_material_tags(material_command))
    if missing_tags:
        missing = ", ".join(str(tag) for tag in missing_tags)
        raise ValueError(f"explicit model references undefined material tags: {missing}")
    write_material_test_tcl(
        tcl_file=tcl_file,
        history_file=history_file,
        response_file=response_file,
        material_command=material_command,
        n_steps=history.size,
        area=area,
        length=length,
        test_material_tag=material.tag,
        topology=modeling.topology,
        material_tags=modeling.material_tags,
        topology_root=topology_root,
        material_mode=material_mode,
    )

    env = os.environ.copy()
    if tcl_library is not None:
        env["TCL_LIBRARY"] = str(tcl_library)

    cwd = (solver.working_dir or executable.parent).resolve()
    if not str(work_dir).isascii():
        # Native PathSeries uses ANSI fopen on Windows; relative filenames work
        # when the OS establishes the Unicode working directory for the process.
        cwd = work_dir
    source_path = tcl_file.as_posix()
    for character in ('\\', '"', '$', '['):
        source_path = source_path.replace(character, '\\' + character)
    bootstrap = f'fconfigure stdin -encoding utf-8\nsource -encoding utf-8 "{source_path}"\nexit\n'
    process = subprocess.Popen(
        [str(executable)],
        cwd=cwd,
        env=env,
        text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    started = time.monotonic()
    try:
        while True:
            if cancel_check and cancel_check():
                raise AnalysisCancelled("计算已取消")
            if time.monotonic() - started >= solver.timeout:
                raise TimeoutError(f"OpenSees 计算超过 {solver.timeout:g} 秒")
            try:
                stdout, stderr = process.communicate(bootstrap, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                bootstrap = None
                continue
    except BaseException:
        process.kill()
        process.communicate()
        raise

    if process.returncode != 0:
        raise RuntimeError(
            "OpenSees failed\n"
            f"command: {executable} {tcl_file}\n"
            f"return code: {process.returncode}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    if not response_file.exists():
        initialization_hint = ""
        diagnostic_output = f"{stdout}\n{stderr}".lower()
        if "can't find a usable init.tcl" in diagnostic_output:
            initialization_hint = (
                "\nOpenSees Tcl initialization failed. Configure solver.tcl_library_path "
                "or place init.tcl under lib/tcl8.6 next to OpenSees.exe."
            )
        raise RuntimeError(
            "OpenSees did not write a response file\n"
            f"command: {executable} {tcl_file}\n"
            f"return code: {process.returncode}"
            f"{initialization_hint}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    if response_file.stat().st_size == 0:
        raise RuntimeError("OpenSees wrote an empty response file")

    response = np.loadtxt(response_file)
    if response.ndim == 1:
        response = response.reshape(1, -1)
    if response.shape[1] < 2:
        raise RuntimeError(f"response file must contain at least two columns: {response_file}")
    if len(response) != len(history) or not np.all(np.isfinite(response[:, :2])):
        raise RuntimeError(
            f'计算未完成完整加载历程：应有 {len(history)} 点，实际 {len(response)} 点；不能发布不完整结果。\n'
            f'stdout:\n{stdout}\nstderr:\n{stderr}'
        )
    if not np.allclose(response[:, 0], history, rtol=1e-8, atol=1e-9):
        raise RuntimeError('计算位移与输入加载历程不一致；请检查收敛和输出文件。')

    return SimulationResult(
        displacement=response[:, 0],
        force=response[:, 1],
        tcl_file=tcl_file,
        history_file=history_file,
        response_file=response_file,
        stdout=stdout,
        stderr=stderr,
        returncode=process.returncode,
    )


def _safe_stem(value: str) -> str:
    # The bundled Windows OpenSees build cannot reliably decode non-ASCII
    # script paths passed on its command line. UI display names may be Chinese,
    # so keep generated solver filenames strictly ASCII.
    cleaned = "".join(
        char.lower() if char.isascii() and char.isalnum() else "_"
        for char in value
    ).strip("_")
    return cleaned or "material"


def _resolve_tcl_library(solver: SolverConfig, executable: Path) -> Path | None:
    """Resolve Tcl explicitly, then fall back to a library bundled with OpenSees."""
    if solver.tcl_library_path is not None:
        configured = solver.tcl_library_path.resolve()
        if not configured.exists():
            raise FileNotFoundError(f"Tcl library not found: {configured}")
        return configured

    bundled_candidates = (
        executable.parent / "lib" / "tcl8.6",
        executable.parent.parent / "lib" / "tcl8.6",
    )
    for candidate in bundled_candidates:
        if (candidate / "init.tcl").is_file():
            return candidate.resolve()
    return None
