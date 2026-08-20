from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np

from material_test.domain import MaterialTemplate, ModelingConfig, SimulationResult, SolverConfig
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
) -> SimulationResult:
    """Run a material test with a configured OpenSees executable."""
    executable = solver.executable_path.resolve()
    if not executable.exists():
        raise FileNotFoundError(f"OpenSees executable not found: {executable}")

    tcl_library = solver.tcl_library_path.resolve() if solver.tcl_library_path else None
    if tcl_library is not None and not tcl_library.exists():
        raise FileNotFoundError(f"Tcl library not found: {tcl_library}")

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
    write_material_test_tcl(
        tcl_file=tcl_file,
        history_file=history_file,
        response_file=response_file,
        material_command=material.render_command(),
        n_steps=history.size,
        area=area,
        length=length,
        test_material_tag=material.tag,
        topology=modeling.topology,
        material_tags=modeling.material_tags,
    )

    env = os.environ.copy()
    if tcl_library is not None:
        env["TCL_LIBRARY"] = str(tcl_library)

    cwd = (solver.working_dir or executable.parent).resolve()
    process = subprocess.run(
        [str(executable), str(tcl_file)],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=solver.timeout,
        check=False,
    )

    if process.returncode != 0:
        raise RuntimeError(
            "OpenSees failed\n"
            f"command: {executable} {tcl_file}\n"
            f"return code: {process.returncode}\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    if not response_file.exists():
        raise RuntimeError(
            "OpenSees did not write a response file\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    if response_file.stat().st_size == 0:
        raise RuntimeError("OpenSees wrote an empty response file")

    response = np.loadtxt(response_file)
    if response.ndim == 1:
        response = response.reshape(1, -1)
    if response.shape[1] < 2:
        raise RuntimeError(f"response file must contain at least two columns: {response_file}")

    return SimulationResult(
        displacement=response[:, 0],
        force=response[:, 1],
        tcl_file=tcl_file,
        history_file=history_file,
        response_file=response_file,
        stdout=process.stdout,
        stderr=process.stderr,
        returncode=process.returncode,
    )


def _safe_stem(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in value).strip("_")
    return cleaned or "material"
