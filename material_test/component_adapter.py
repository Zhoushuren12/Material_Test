"""Thin adapter to the shared structural component kernel (no copied solver).

Material_Test can evaluate saved explicit components for advanced studies without
importing a PyQt UI into its PySide process. A clean OpenSees process is used.
"""
from pathlib import Path
import sys

def _kernel():
    root=Path(__file__).resolve().parents[3]/'Structural analysis'
    if not (root/'structure_app/services/brace_component.py').is_file():
        raise RuntimeError('需要同一工作区的 Structural analysis 构件内核；未退回另一套求解实现')
    if str(root) not in sys.path:sys.path.insert(0,str(root))
    from structure_app.services.brace_package import import_brace_package
    from structure_app.services.brace_single_test import run_single_brace_test
    from structure_app.services.brace_component import IDENTITY_SCALE
    return import_brace_package,run_single_brace_test,IDENTITY_SCALE

def evaluate_component(package_path, loading_path, step_mm, *, geometry=None, overrides=None):
    load,run,scale=_kernel()
    resource=load(package_path)
    if overrides:
        from structure_app.services.brace_assignment import apply_overrides
        resource.package_snapshot=apply_overrides(resource.package_snapshot,overrides)
        resource.content_sha256=resource.package_snapshot['content_sha256']
    return run(resource,scale,loading_path,step_mm,geometry=geometry)
