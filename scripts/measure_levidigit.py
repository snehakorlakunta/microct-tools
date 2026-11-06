#!/usr/bin/env python
"""Official BV/TV and digit length: the vendored LeviDigit postprocessing over a
finished segmentation.

LeviDigit (scripts/levidigit_vendor/, see PROVENANCE.md there) is the OFFICIAL
measurement in this app. It is run here exactly the way its own
`postprocessing/analyze_bvtv.py` runs it: `levidigit.bvtv.analyze_bvtv` over an
input folder holding `<case>_0000.nii.gz` and a mask folder holding
`<case>.nii.gz`. This script only stages those two files from the run's outputs,
calls it, and folds its row into the `<case>_measurement.json` envelope the
worker already parses ({"case", "pipeline_version", "metrics", "environment"}).

What LeviDigit computes (see its bvtv.py / digit_length.py):
  TV       = voxels inside the segmentation mask
  BV       = mask voxels with image intensity >= --bone-threshold (0..255 grey,
             inclusive). A fixed grey threshold, NOT converted from HU.
  BV/TV    = BV / TV, reported as a percent (and here also as a fraction)
  length   = straight line from the largest-concavity centroid to the distal
             endpoint, counting only the sub-segments that lie inside the mask

Its input checks are kept as-is and are deliberately strict: 0.004 mm isotropic
spacing (+-1%), a 0..255 image, a binary 0/1 mask, matching shape and affine. Any
of those failing fails the measurement with LeviDigit's own message — they are
the conditions its numbers are defined under.

--bvtv-only skips digit length (used for non-digit datasets, e.g. uct ho, where
BV/TV is meaningful but a digit length is not). A digit length LeviDigit could
not compute is NOT a failure: the measurement succeeds with
digit_length_status "failed: ..." and no length, never a length of zero.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent / "levidigit_vendor"
VENDOR_COMMIT = "4e27656ce3909a3a73dc0b02f5a9b36a7676a45c"

# Row keys copied out of LeviDigit's measurements.csv row into `metrics`.
NUMERIC_KEYS = [
    "bone_threshold", "voxel_volume_mm3",
    "tv_voxels", "bv_voxels", "tv_mm3", "bv_mm3", "bv_tv_percent",
    "digit_length_mm", "digit_length_um",
    "concavity_voxels", "concavity_mm3", "counted_fraction",
    "concavity_centroid_i", "concavity_centroid_j", "concavity_centroid_k",
    "concavity_centroid_x_mm", "concavity_centroid_y_mm", "concavity_centroid_z_mm",
    "distal_endpoint_i", "distal_endpoint_j", "distal_endpoint_k",
    "distal_endpoint_x_mm", "distal_endpoint_y_mm", "distal_endpoint_z_mm",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def _num(v):
    """LeviDigit blanks the length columns with "" when length fails — map those
    (and anything else non-numeric) to None so they can never read as zero."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v) if str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _nifti_name(case: str, suffix: str, src: Path) -> str:
    ext = ".nii.gz" if src.name.lower().endswith(".nii.gz") else ".nii"
    return f"{case}{suffix}{ext}"


def build_metrics(row: dict, *, bvtv_only: bool, spacing_um: float | None) -> dict:
    metrics: dict = {k: _num(row.get(k)) for k in NUMERIC_KEYS if k in row}
    pct = metrics.get("bv_tv_percent")
    # The fraction alongside the percent: normalization (routers/analyses.py) and
    # the SK interim method both work in 0..1.
    metrics["bv_tv"] = round(pct / 100.0, 8) if pct is not None else None
    if bvtv_only:
        metrics["digit_length_status"] = "not measured: BV/TV only (non-digit dataset)"
        metrics["digit_length_mm"] = None
        metrics["digit_length_um"] = None
    else:
        metrics["digit_length_status"] = str(row.get("digit_length_status") or "unknown")
    metrics["digit_length_complete"] = metrics["digit_length_status"] == "complete"
    for k in ("tv_voxels", "bv_voxels", "concavity_voxels"):
        if metrics.get(k) is not None:
            metrics[k] = int(metrics[k])
    metrics["spacing_um"] = spacing_um
    metrics["source_tag"] = "LeviDigit"
    metrics["method"] = "levidigit"
    metrics["threshold_rule"] = "image intensity >= bone_threshold inside predicted mask"
    metrics["digit_length_rule"] = ("straight concavity-centroid-to-distal line; sum only "
                                    "subsegments inside the binary ROI")
    metrics["levidigit_commit"] = VENDOR_COMMIT
    return metrics


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LeviDigit BV/TV + digit length over a segmentation",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--mask", required=True, help="Binary mask .nii.gz from a succeeded run")
    ap.add_argument("--image", required=True, help="Grayscale volume .nii.gz (_0000) from that run")
    ap.add_argument("--case", required=True, help="Case name (prefixes the outputs)")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--pipeline", default="levidigit_v1")
    ap.add_argument("--bone-threshold", type=float, required=True,
                    help="Inclusive bone threshold on the 0..255 grey scale")
    ap.add_argument("--spacing-um", type=float, default=None,
                    help="Recorded for provenance only; LeviDigit reads spacing from the NIfTI header")
    ap.add_argument("--skip-viz", action="store_true", help="Skip the PNG/TIFF figures")
    ap.add_argument("--bvtv-only", action="store_true", help="Skip digit length")
    args = ap.parse_args(argv)

    t0 = time.time()
    sys.path.insert(0, str(VENDOR_DIR))
    import numpy as np
    from levidigit import bvtv as ld

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mask_src, image_src = Path(args.mask), Path(args.image)
    case = args.case

    log(f"[levidigit] mask      : {mask_src}")
    log(f"[levidigit] image     : {image_src}")
    log(f"[levidigit] threshold : >= {args.bone_threshold:g} (grey 0..255)")
    log(f"[levidigit] mode      : {'BV/TV only' if args.bvtv_only else 'BV/TV + digit length'}")

    # Stage the two files the way LeviDigit's folder-based API expects them.
    root = out / "levidigit"
    in_dir, mask_dir = root / "input", root / "masks"
    for d in (in_dir, mask_dir):
        if d.exists():
            shutil.rmtree(d)
    staged_img = in_dir / _nifti_name(case, "_0000", image_src)
    staged_mask = mask_dir / _nifti_name(case, "", mask_src)
    _link_or_copy(image_src, staged_img)
    _link_or_copy(mask_src, staged_mask)

    try:
        if args.bvtv_only:
            row = ld.measure_case(case, staged_img, staged_mask, args.bone_threshold)
        else:
            rows = ld.analyze_bvtv(in_dir, mask_dir, args.bone_threshold,
                                   root / "measurements.csv",
                                   make_figures=not args.skip_viz)
            row = next((r for r in rows if r.get("case_id") == case), None)
            if row is None:
                log(f"[levidigit] FAIL no result row for case {case!r}")
                return 2
    except (ValueError, FileNotFoundError) as e:
        # LeviDigit's own input validation (spacing, intensity range, binary
        # mask, geometry). Its message says exactly what was wrong.
        log(f"[levidigit] FAIL {e}")
        return 2
    finally:
        # The staged copies are hardlinks/copies of the run's own files; drop them
        # so the output folder holds only results.
        for d in (in_dir, mask_dir):
            shutil.rmtree(d, ignore_errors=True)

    metrics = build_metrics(row, bvtv_only=args.bvtv_only, spacing_um=args.spacing_um)
    log(f"[levidigit] TV={metrics.get('tv_voxels')} vox, BV={metrics.get('bv_voxels')} vox "
        f"-> BV/TV = {metrics.get('bv_tv_percent'):.3f} %")
    if metrics["digit_length_complete"]:
        log(f"[levidigit] digit length = {metrics['digit_length_mm']:.4f} mm")
    else:
        log(f"[levidigit] digit length: {metrics['digit_length_status']}")

    def _exists(p: Path):
        return str(p) if p.is_file() else None

    audit = None
    for ext in (".nii.gz", ".nii"):
        audit = audit or _exists(root / "visualization_labelmaps" / "audit" / f"{case}{ext}")
    result = {
        "case": case,
        "pipeline_version": args.pipeline,
        "metrics": metrics,
        # The audit labelmap: 1 TV below threshold, 2 BV, 3 digit length counted,
        # 4 largest concavity, 5 concavity centroid, 6 distal endpoint.
        "annotated_nii": audit,
        "csv_path": _exists(root / "measurements.csv"),
        "figure_png": _exists(root / "figures" / f"{case}_publication.png"),
        "environment": {
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy_version": np.__version__,
            "levidigit_commit": VENDOR_COMMIT,
            "total_seconds": round(time.time() - t0, 2),
        },
    }
    for mod in ("nibabel", "scipy", "skimage"):
        try:
            result["environment"][f"{mod}_version"] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            pass
    out_json = out / f"{case}_measurement.json"
    out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    log(f"[levidigit] wrote {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
