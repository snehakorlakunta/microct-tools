#!/usr/bin/env python
"""Interim threshold BV/TV over a finished segmentation.

TV = voxels inside the mask; BV = mask voxels whose image intensity clears the
grey threshold (resolved from a Hounsfield threshold per scan — see
src/microct_lab/bvtv.py); BV/TV = BV / TV.

Deliberately dependency-light (SimpleITK + numpy only) and shaped like
measure_morphometry.py: reads the run's own <case>_0000.nii.gz + <case>.nii.gz,
writes <case>_measurement.json in the same envelope the worker already parses
({"case", "pipeline_version", "metrics", "environment"}), so the worker's
result handling is identical for both drivers.

Temporary by design — retire this when the official BV/TV calculation lands.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Threshold BV/TV over a segmentation mask",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--mask", required=True, help="Binary mask .nii.gz from a succeeded run")
    ap.add_argument("--image", required=True, help="Grayscale volume .nii.gz (_0000) from that run")
    ap.add_argument("--case", required=True, help="Case name (prefixes the outputs)")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--pipeline", default="bvtv_thresh_v1")
    ap.add_argument("--spacing-um", type=float, default=4.0,
                    help="Voxel size (um) for the mm^3 conversions")
    ap.add_argument("--threshold-grey", type=float, default=80.0,
                    help="Bone threshold on the 0..255 grey scale (already "
                         "resolved from HU by the server)")
    args = ap.parse_args()

    import numpy as np
    import SimpleITK as sitk

    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)

    log(f"[bvtv] mask  : {args.mask}")
    log(f"[bvtv] image : {args.image}")
    mask = sitk.GetArrayFromImage(sitk.ReadImage(args.mask))
    image = sitk.GetArrayFromImage(sitk.ReadImage(args.image))
    if mask.shape != image.shape:
        log(f"[bvtv] FAIL shape mismatch: mask {mask.shape} vs image {image.shape}")
        return 2

    inside = mask > 0
    tv = int(inside.sum())
    bv = int((inside & (image >= args.threshold_grey)).sum())
    bv_tv = float(bv / tv) if tv > 0 else 0.0

    vox_mm3 = (args.spacing_um / 1000.0) ** 3
    metrics = {
        "bv_tv": round(bv_tv, 6),
        "bv_voxels": bv,
        "tv_voxels": tv,
        "bv_mm3": round(bv * vox_mm3, 6),
        "tv_mm3": round(tv * vox_mm3, 6),
        "threshold_grey": args.threshold_grey,
        "spacing_um": args.spacing_um,
        "method": "intensity_threshold_within_mask",
    }
    log(f"[bvtv] TV={tv} vox, BV={bv} vox (grey >= {args.threshold_grey}) "
        f"-> BV/TV = {bv_tv:.4f}")

    result = {
        "case": args.case,
        "pipeline_version": args.pipeline,
        "metrics": metrics,
        "environment": {
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy_version": np.__version__,
            "total_seconds": round(time.time() - t0, 2),
        },
    }
    out_json = os.path.join(args.out, f"{args.case}_measurement.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    log(f"[bvtv] wrote {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
