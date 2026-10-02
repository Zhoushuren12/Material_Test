from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from material_test.io.project_config import load_project_config
from material_test.fitting import fit_project
from material_test.workflow import run_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenSees material testing utility.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    component=subparsers.add_parser('component-test',help='Test an .oasbrace with the shared structural component kernel.')
    component.add_argument('package',type=Path)
    component.add_argument('--path',default='0,5,-5,0,10,-10,0',help='Ordered displacement turning points, mm')
    component.add_argument('--step',type=float,default=.5)
    component.add_argument('--output',type=Path,required=True,help='New JSON result file (never overwrite)')

    run_parser = subparsers.add_parser("run", help="Run a material test project config.")
    run_parser.add_argument("config", type=Path, help="Project YAML config path.")
    run_parser.add_argument("--run-dir", type=Path, default=None, help="Override run output directory.")

    fit_parser = subparsers.add_parser("fit", help="Fit selected material parameters to experiment data.")
    fit_parser.add_argument("config", type=Path, help="Project YAML config path with a fitting section.")
    fit_parser.add_argument("--run-dir", type=Path, default=None, help="Override fit output directory.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command=='component-test':
        import json
        from dataclasses import asdict
        from material_test.component_adapter import evaluate_component
        if args.output.exists():parser.error('Output already exists; choose a new result file.')
        result=evaluate_component(args.package,[float(v.strip()) for v in args.path.split(',')],args.step)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(asdict(result),stream,ensure_ascii=False,indent=2,allow_nan=False)
        print(f'Component result: {args.output}')
        return 0

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

    if args.command == "fit":
        config = load_project_config(args.config)
        result = fit_project(config, run_dir=args.run_dir)
        print(f"Fit directory: {result.run_dir}")
        print(f"Global algorithm: {result.global_algorithm}")
        print(f"Evaluations: global={result.global_evaluations}, local={result.local_evaluations}")
        print(f"Normalized RMSE: {result.metrics.normalized_rmse:.6g}")
        print("Best parameters:")
        for name, value in result.fitted_material.parameter_values.items():
            print(f"  {name}: {value:.12g}")
        print(f"Fitted material: {result.material_file}")
        print(f"Summary: {result.summary_file}")
        print(f"Overlay figure: {result.figure_file}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
