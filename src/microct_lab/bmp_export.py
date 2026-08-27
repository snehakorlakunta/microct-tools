"""Export a segmentation mask volume to a folder of per-slice BMP images.

Used two ways:
  * automatically at the end of a segmentation run (the pipeline hands us the
    in-memory ``seg`` array — see scripts/segment_microct.py), and
  * on demand for an existing run via the ``/runs/{id}/export_bmp`` endpoint,
    where we read the stored ``<case>.nii.gz`` back from disk.

Two output modes: the classic mask (background -> 0, ROI -> 255; multi-label
masks spread across 0..255) and masked-image (the ORIGINAL image pixels inside
the mask, black elsewhere — see mask_array_to_bmp). Filenames mirror the source
slice names when the slice folder is provided and the counts line up, so the
exported folder aligns 1:1 with the original input stack (drop-in
slice-for-slice correspondence).
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
                      names: Optional[Sequence[str]] = None,
                      image=None) -> dict:
    """Write each Z-slice of a 3D label array ``(Z, Y, X)`` as an 8-bit BMP.

    Two modes:
      * mask only (``image=None``) — background 0, ROI 255 (multi-label masks
        spread over 0..255), as always.
      * masked image (``image`` = the aligned grayscale volume) — each slice is
        the ORIGINAL image pixels inside the mask, black elsewhere. This is the
        QC-friendly export: what the model kept, shown as actual bone.

    Returns ``{"count", "bytes", "dir", "labels", "mode"}``.
    """
    import numpy as np
    from PIL import Image

    arr = np.asarray(arr)
    if arr.ndim != 3:
        raise ValueError(f"expected a 3D mask (Z, Y, X), got shape {tuple(arr.shape)}")
    if image is not None:
        image = np.asarray(image)
        if image.shape != arr.shape:
            raise ValueError(f"image shape {tuple(image.shape)} does not match "
                             f"mask shape {tuple(arr.shape)}")
    os.makedirs(out_dir, exist_ok=True)
    z_count = int(arr.shape[0])
    max_label = int(arr.max()) if arr.size else 0
    if not names or len(names) != z_count:
        width = max(4, len(str(max(z_count - 1, 0))))
        names = [f"mask_{i:0{width}d}.bmp" for i in range(z_count)]

    total = 0
    for i in range(z_count):
        sl = arr[i]
        if image is not None:
            out = np.where(sl > 0, image[i], 0).astype(np.uint8)
        elif max_label <= 1:
            out = (sl > 0).astype(np.uint8) * 255
        else:  # multi-label: spread classes across the 0..255 range
            out = np.clip(sl.astype(np.float32) * (255.0 / max_label), 0, 255).astype(np.uint8)
        path = os.path.join(out_dir, names[i])
        Image.fromarray(out, "L").save(path, format="BMP")
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return {"count": z_count, "bytes": total, "dir": out_dir, "labels": max_label,
            "mode": "masked_image" if image is not None else "mask"}


def export_mask_bmp(mask_path: str, out_dir: str,
                    slices_folder: Optional[str] = None,
                    pattern: str = "*rec*.bmp",
                    image_path: Optional[str] = None) -> dict:
    """Read a mask ``.nii.gz`` and write per-slice BMPs, mirroring source names.

    With ``image_path`` (the run's own ``<case>_0000.nii.gz``, which is always
    aligned with the mask — cropped runs included), the output is the masked
    IMAGE rather than the binary mask."""
    import SimpleITK as sitk

    arr = sitk.GetArrayFromImage(sitk.ReadImage(mask_path))  # (Z, Y, X)
    image = None
    if image_path:
        image = sitk.GetArrayFromImage(sitk.ReadImage(image_path))
    names = None
    if slices_folder and os.path.isdir(slices_folder):
        files = sorted_slice_files(slices_folder, pattern)
        if len(files) == arr.shape[0]:
            names = [os.path.splitext(os.path.basename(f))[0] + ".bmp" for f in files]
    return mask_array_to_bmp(arr, out_dir, names, image=image)


def bmp_dir_for(output_dir: str, case: str, mode: str = "mask") -> str:
    """Canonical location of a run's per-slice BMP folder for the given mode.
    Mask and masked-image exports live side by side, never overwriting each
    other."""
    suffix = "maskimg_bmp" if mode == "masked_image" else "mask_bmp"
    return os.path.join(output_dir, f"{case}_{suffix}")
