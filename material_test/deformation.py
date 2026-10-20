"""Nodal deformation histories tied to the geometry used by the solver."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class DeformationHistory:
    model: dict
    node_tags: tuple[int, ...]
    coordinates: np.ndarray
    displacements: np.ndarray
    timeline: np.ndarray
    control_displacement: np.ndarray
    control_force: np.ndarray
    run_dir: Path

    @classmethod
    def from_component(cls, project, result):
        model = deepcopy(project.data)
        tags = tuple(node['tag'] for node in model['nodes'])
        required = [f'node_{tag}_u{dof}' for tag in tags for dof in (1, 2, 3)]
        if any(name not in result.columns for name in required):
            raise ValueError('Missing nodal displacement histories; recalculate the assembly')
        displacements = np.column_stack([result.channel(name) for name in required])
        displacements = displacements.reshape(len(result.values), len(tags), 3)
        timeline = result.channel('time' if model['loading'].get('analysis') == 'transient' else 'sample').copy()
        if not np.isfinite(displacements).all() or np.any(np.diff(timeline) <= 0):
            raise ValueError('Invalid nodal deformation history')
        return cls(model, tags, np.array([[node['x'], node['y']] for node in model['nodes']], dtype=float),
                   displacements, timeline, result.channel('actuator_displacement').copy(),
                   result.channel('actuator_force').copy(), result.run_dir)

    @property
    def frame_count(self):
        return len(self.timeline)

    @property
    def span(self):
        return max(float(np.ptp(self.coordinates, axis=0).max()), 1.)

    @property
    def default_amplification(self):
        translation = float(np.linalg.norm(self.displacements[:, :, :2], axis=2).max())
        rotation = float(np.abs(self.displacements[:, :, 2]).max())
        scales = [1e6]
        if translation > 1e-12:
            scales.append(.15 * self.span / translation)
        if rotation > 1e-12:
            scales.append(.45 / rotation)
        return max(1., min(scales)) if len(scales) > 1 else 1.

    def positions(self, frame, amplification=1.):
        return self.coordinates + amplification * self.displacements[frame, :, :2]
