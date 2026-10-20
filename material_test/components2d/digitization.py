from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage


@dataclass(frozen=True)
class AxisCalibration:
    name: str
    bounds: tuple[int, int, int, int]
    x_range: tuple[float, float]
    y_range: tuple[float, float]
    exclusions: tuple[tuple[int, int, int, int], ...] = ()
    threshold: int = 145
    x_unit: str = "mm"
    y_unit: str = "kN"

    @property
    def pixel_scale(self):
        left, top, right, bottom = self.bounds
        return ((self.x_range[1] - self.x_range[0]) / (right - left),
                (self.y_range[1] - self.y_range[0]) / (bottom - top))


@dataclass
class DigitizedCurve:
    calibration: AxisCalibration
    points: np.ndarray
    mask: np.ndarray
    source_sha256: str


def digitize_curve(image_path, calibration):
    image = Image.open(image_path).convert("L")
    left, top, right, bottom = calibration.bounds
    if not (0 <= left < right < image.width and 0 <= top < bottom < image.height):
        raise ValueError("Axes must be calibrated within the image")
    if calibration.x_range[0] >= calibration.x_range[1] or calibration.y_range[0] >= calibration.y_range[1]:
        raise ValueError("Axis ranges must increase")
    gray = np.asarray(image)[top:bottom + 1, left:right + 1]
    mask = gray < calibration.threshold
    mask[:3, :] = False; mask[-3:, :] = False
    mask[:, :3] = False; mask[:, -3:] = False
    for x0, y0, x1, y1 in calibration.exclusions:
        mask[max(0, y0-top):max(0, y1-top+1), max(0, x0-left):max(0, x1-left+1)] = False
    labels, count = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    mask &= sizes[labels] >= 5
    # Collapse each dark vertical run to its center. These are unordered spatial
    # samples; intersecting loops cannot supply timestamps or cycle identity.
    samples = []
    dx, dy = calibration.pixel_scale
    for x in range(mask.shape[1]):
        rows = np.flatnonzero(mask[:, x])
        if not len(rows):
            continue
        runs = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
        for run in runs:
            y = float(np.mean(run))
            samples.append((calibration.x_range[0] + x*dx, calibration.y_range[1] - y*dy,
                            left+x, top+y, max(.5, len(run)/2)*dy))
    points = np.asarray(samples, dtype=float)
    if len(points) < 20:
        raise ValueError("Too few curve pixels after exclusions")
    return DigitizedCurve(calibration, points, mask, hashlib.sha256(Path(image_path).read_bytes()).hexdigest())


def save_digitized(curve, directory, image_path):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    name = curve.calibration.name
    np.savetxt(directory / f"{name}_points.csv", curve.points, delimiter=",", comments="",
               header="displacement,force,pixel_x,pixel_y,force_halfwidth", fmt="%.9g")
    Image.fromarray((curve.mask*255).astype(np.uint8)).save(directory / f"{name}_mask.png")
    metadata = {"schema": "material-test/image-observation/v1", "calibration": asdict(curve.calibration),
                "source": str(Path(image_path).resolve()), "source_sha256": curve.source_sha256,
                "ordering": "unordered", "time_history_recovered": False, "point_count": len(curve.points),
                "pixel_scale": curve.calibration.pixel_scale,
                "limitations": ["Overlapping cycles have no reliable time or cycle identity",
                                "Grid/legend removal and printed line width introduce digitization uncertainty",
                                "No experimental cycle energy or sequence RMSE may be inferred from this point cloud"]}
    (directory / f"{name}_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    rgb = Image.open(image_path).convert("RGB")
    overlay = np.asarray(rgb).copy()
    left, top, right, bottom = curve.calibration.bounds
    crop = overlay[top:bottom+1, left:right+1]
    crop[curve.mask] = (205, 40, 55)
    annotated = Image.fromarray(overlay)
    ImageDraw.Draw(annotated).rectangle(curve.calibration.bounds, outline=(0, 140, 90), width=2)
    annotated.save(directory / f"{name}_extraction_check.png")


def load_digitized(metadata_path):
    path = Path(metadata_path)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if metadata.get("schema") != "material-test/image-observation/v1" or metadata.get("ordering") != "unordered":
        raise ValueError("Not an unordered image observation")
    calibration = AxisCalibration(**metadata["calibration"])
    points = np.loadtxt(path.with_name(f"{calibration.name}_points.csv"), delimiter=",", skiprows=1, ndmin=2)
    mask = np.asarray(Image.open(path.with_name(f"{calibration.name}_mask.png"))) > 0
    return DigitizedCurve(calibration, points, mask, metadata["source_sha256"])
