"""Export a segmentation mask volume to a folder of per-slice BMP images.

Used two ways:
  * automatically at the end of a segmentation run (the pipeline hands us the
    in-memory ``seg`` array — see scripts/segment_microct.py), and
  * on demand for an existing run via the ``/runs/{id}/export_bmp`` endpoint,
    where we read the stored ``<case>.nii.gz`` back from disk.

Mask-only output: background -> 0, ROI -> 255 for a binary mask; for a
multi-label mask the labels are spread across 0..255 so each class is a
distinct gray value. Filenames mirror the source slice names when the slice
folder is provided and the counts line up, so the exported folder aligns 1:1
with the original input stack (drop-in slice-for-slice correspondence).
"""
from __future__ import annotations

import glob
import os
from typing import Optional, Sequence


def sorted_slice_files(slices_folder: str, pattern: str = "*rec*.bmp") -> list[str]:
    """Return the source slices in the SAME order the pipeline stacked them.

    Mirrors ``find_slices`` in the segmentation pipeline: glob the pattern, drop
    SkyScan ``_spr`` / ``_pp`` preview images, then natural-sort (falling back to
    a plain sort when natsort is unavailable — zero-padded SkyScan indices sort
    identically either way).
    """
    files = [f for f in glob.glob(os.path.join(slices_folder, pattern))
             if "_spr" not in os.path.basename(f).lower()
             and "_pp" not in os.path.basename(f).lower()]
    try:
        from natsort import natsorted
        return list(natsorted(files))
    except Exception:
        files.sort()
        return files


def mask_array_to_bmp(arr, out_dir: str,
                      names: Optional[Sequence[str]] = None) -> dict:
    """Write each Z-slice of a 3D label array ``(Z, Y, X)`` as an 8-bit BMP.

    Returns ``{"count", "bytes", "dir", "labels"}``.
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(arr)
    if arr.ndim != 3:
        raise ValueError(f"expected a 3D mask (Z, Y, X), got shape {tuple(arr.shape)}")
    os.makedirs(out_dir, exist_ok=True)
    z_count = int(arr.shape[0])
    max_label = int(arr.max()) if arr.size else 0
    if not names or len(names) != z_count:
        width = max(4, len(str(max(z_count - 1, 0))))
        names = [f"mask_{i:0{width}d}.bmp" for i in range(z_count)]

    total = 0
    for i in range(z_count):
        sl = arr[i]
        if max_label <= 1:
            out = (sl > 0).astype(np.uint8) * 255
        else:  # multi-label: spread classes across the 0..255 range
            out = np.clip(sl.astype(np.float32) * (255.0 / max_label), 0, 255).astype(np.uint8)
        path = os.path.join(out_dir, names[i])
        Image.fromarray(out, "L").save(path, format="BMP")
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return {"count": z_count, "bytes": total, "dir": out_dir, "labels": max_label}


def export_mask_bmp(mask_path: str, out_dir: str,
                    slices_folder: Optional[str] = None,
                    pattern: str = "*rec*.bmp") -> dict:
    """Read a mask ``.nii.gz`` and write per-slice BMPs, mirroring source names."""
    import SimpleITK as sitk

    arr = sitk.GetArrayFromImage(sitk.ReadImage(mask_path))  # (Z, Y, X)
    names = None
    if slices_folder and os.path.isdir(slices_folder):
        files = sorted_slice_files(slices_folder, pattern)
        if len(files) == arr.shape[0]:
            names = [os.path.splitext(os.path.basename(f))[0] + ".bmp" for f in files]
    return mask_array_to_bmp(arr, out_dir, names)


def bmp_dir_for(output_dir: str, case: str) -> str:
    """Canonical location of a run's per-slice mask BMP folder."""
    return os.path.join(output_dir, f"{case}_mask_bmp")
