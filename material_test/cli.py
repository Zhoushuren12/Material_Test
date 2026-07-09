from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from material_test.io.project_config import load_project_config
from material_test.workflow import run_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenSees material testing utility.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a material test project config.")
    run_parser.add_argument("config", type=Path, help="Project YAML config path.")
    run_parser.add_argument("--run-dir", type=Path, default=None, help="Override run output directory.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        config = load_project_config(args.config)
        result = run_project(config, run_dir=args.run_dir)
        print(f"Run directory: {result.run_dir}")
        print(f"Material: {config.material.name}")
        print(f"OpenSees executable: {config.solver.executable_path}")
        print(f"Force range: {result.simulation.force.min():.3f} to {result.simulation.force.max():.3f}")
        print(f"Response data: {result.simulation.response_file}")
        print(f"Simulation CSV: {result.simulation_csv}")
        print(f"Overlay figure: {result.figure_file}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
