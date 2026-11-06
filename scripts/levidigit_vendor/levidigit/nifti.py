"""NIfTI validation and staging."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import nibabel as nib
import numpy as np

from .constants import EXPECTED_SPACING_MM, SPACING_RTOL


def validate_spacing(image: nib.spatialimages.SpatialImage, path: Path) -> tuple[float, float, float]:
    spacing = tuple(float(value) for value in image.header.get_zooms()[:3])
    expected = np.full(3, EXPECTED_SPACING_MM, dtype=float)
    if not np.allclose(spacing, expected, rtol=SPACING_RTOL, atol=0.0):
        raise ValueError(
            f"Unexpected voxel spacing for {path.name}: {spacing} mm; "
            f"expected {EXPECTED_SPACING_MM} mm isotropic"
        )
    return spacing


def validate_input_nifti(path: Path) -> dict[str, object]:
    image = nib.load(str(path))
    if len(image.shape) != 3:
        raise ValueError(f"Expected a 3D image, got shape {image.shape}: {path}")
    spacing = validate_spacing(image, path)
    dtype = np.dtype(image.get_data_dtype())
    if not np.issubdtype(dtype, np.number):
        raise ValueError(f"Expected numeric image data, got {dtype}: {path}")
    return {
        "shape": [int(value) for value in image.shape],
        "spacing_mm": list(spacing),
        "dtype": str(dtype),
        "source_size_bytes": path.stat().st_size,
    }


def stage_single_channel_inputs(cases: dict[str, Path], destination: Path) -> dict[str, dict[str, object]]:
    destination.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, dict[str, object]] = {}
    for case_id, source in sorted(cases.items()):
        metadata = validate_input_nifti(source)
        target = destination / f"{case_id}_0000.nii.gz"
        if source.name.lower().endswith(".nii.gz"):
            try:
                os.link(source, target)
                staging_method = "hardlink"
            except OSError:
                shutil.copy2(source, target)
                staging_method = "copy"
        else:
            image = nib.load(str(source))
            nib.save(image, str(target))
            staging_method = "recompress"
        manifest[case_id] = {
            "source": str(source),
            "staged": str(target),
            "staging_method": staging_method,
            **metadata,
        }
    return manifest

