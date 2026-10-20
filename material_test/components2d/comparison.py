from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


def rasterize_response(curve, x, y):
    x, y = np.asarray(x), np.asarray(y)
    if len(x) != len(y) or len(x) < 2 or not np.isfinite(np.column_stack((x, y))).all():
        raise ValueError("A complete finite simulated trajectory is required")
    calibration = curve.calibration
    dx, dy = calibration.pixel_scale
    pixels = np.column_stack(((x-calibration.x_range[0])/dx, (calibration.y_range[1]-y)/dy))
    height, width = curve.mask.shape
    image = Image.new("1", (width, height), 0)
    ImageDraw.Draw(image).line([tuple(v) for v in pixels], fill=1, width=1)
    mask = np.asarray(image).copy()
    outside = np.mean((pixels[:, 0] < 0) | (pixels[:, 0] >= width) | (pixels[:, 1] < 0) | (pixels[:, 1] >= height))
    return mask, float(outside)


def image_metrics(curve, x, y, *, tolerance_pixels=2.):
    simulation, outside = rasterize_response(curve, x, y)
    if not simulation.any():
        return {"symmetric_distance_pixels": 1e6, "coverage": 0., "precision": 0., "outside_fraction": outside}
    to_target = ndimage.distance_transform_edt(~curve.mask)[simulation]
    to_simulation = ndimage.distance_transform_edt(~simulation)[curve.mask]
    return {"symmetric_distance_pixels": float((to_target.mean()+to_simulation.mean())/2 + 100*outside),
            "simulation_to_image_mean_pixels": float(to_target.mean()),
            "image_to_simulation_mean_pixels": float(to_simulation.mean()),
            "distance_p95_pixels": float(max(np.quantile(to_target, .95), np.quantile(to_simulation, .95))),
            "coverage": float(np.mean(to_simulation <= tolerance_pixels)),
            "precision": float(np.mean(to_target <= tolerance_pixels)),
            "tolerance_pixels": float(tolerance_pixels), "outside_fraction": outside,
            "experimental_sequence_rmse": None, "experimental_cycle_energy": None}
