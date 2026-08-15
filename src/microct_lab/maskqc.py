"""Ground-truth-free plausibility checks on a segmentation mask, BEFORE measuring.

WHY THIS EXISTS, AND HOW IT DIFFERS FROM morphqc
------------------------------------------------
`morphqc.py` judges the *numbers the pipeline produced*. This judges the *mask
that goes in*, and it runs first. The difference matters for two reasons:

1. **Cost.** Socket detection is ~90% of the pipeline's runtime — roughly 25
   minutes of CPU for a single case. A mask that was never going to yield a
   meaningful measurement should be caught in the seconds it takes to count
   voxels, not after the pipeline has finished being confidently wrong.
2. **Attribution.** When morphqc flags a result, it cannot say whether the
   cause was a bad mask or bad anatomy. Checking the input separately splits
   those apart, so "the segmentation is fragmented" and "this specimen is not
   a phalanx" stop looking identical in the record.

Ported from the sibling `perios2` project's `qc.py` (see the perios2 section of
`scripts/perios/PROVENANCE.md`), whose thresholds were derived from 15 validated
`Digit*` cases. Two deliberate changes were made on the way in, both because
this app's inputs are framed differently from perios2's:

* **Two severity tiers instead of one.** perios2 fails a case outside its
  observed foreground band. Its band (0.021-0.160) came from tightly-cropped
  `Digit*` volumes; this app ingests whole SkyScan reconstructions, where the
  specimen occupies far less of the volume. R2 — a real, successfully-measured
  dataset here — sits at 0.0168, *below* perios2's observed floor. Failing it
  would be wrong. So the empirically-observed band only WARNs, and FAIL is
  reserved for the framing-independent catastrophes: an empty mask, or one
  where nearly the whole volume is foreground (perios2's corrupted
  `Digit46_021` label was 0.999).
* **Component count is reported, not just the largest-component ratio.** Stage
  0 of the vendored pipeline keeps only the largest connected component (plus
  the second if it is within 0.05mm), and silently discards the rest. R2's mask
  has 72 components with 83% of the foreground in the largest — meaning ~17% of
  the segmentation is thrown away before a single measurement is taken. That
  discard is invisible in the output today, so it is surfaced here.

SEVERITY CONTRACT
-----------------
"fail" means the caller should refuse to measure. "warn" means record it and
let the reviewer decide — the same advisory philosophy morphqc follows. Nothing
here mutates the mask or the run.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# The vendored digitpipe_v5 hard-codes VOXEL_SIZE_MM = 0.004 in utils.py and uses
# it for every voxels->mm conversion. measure_morphometry.py recomputes the mm
# values from --spacing-um so the *units* come out right at any spacing, but the
# pipeline's GEOMETRY is not rescaled with them: the downsample factor, the
# shrink-wrap percentiles, and socket detection's erosion/dilation radii are all
# expressed in voxels and tuned for a 4um grid. At a materially different
# spacing those operate over the wrong physical distance, so the numbers come
# out correctly scaled from a structurally wrong segmentation — the worst kind
# of wrong, because it looks fine. Hence a hard gate rather than a warning.
PIPELINE_SPACING_UM = 4.0

# Fractional tolerance on every spacing comparison. R2/R4 report 4.000059 um
# (0.0015% off), so this is loose enough for real scanner metadata and tight
# enough to catch an actual resolution change.
SPACING_RTOL = 0.01

# --- Foreground fraction -----------------------------------------------------
# FAIL band: framing-independent catastrophe only.
MIN_FOREGROUND_FRACTION_FAIL = 0.0001   # 1 voxel in 10,000 — below this there is
                                        # nothing to measure at any framing.
MAX_FOREGROUND_FRACTION_FAIL = 0.35     # perios2's corrupted label was 0.999.

# WARN band: the empirically observed range. Lower bound is R2's own 0.0168
# rounded down, not perios2's 0.021, because R2 is a legitimate whole-scan case
# here. Upper bound is perios2's observed 0.160 with headroom.
OBSERVED_FOREGROUND_FRACTION = (0.015, 0.25)

# --- Connected components ----------------------------------------------------
# Fraction of foreground in the single largest component. perios2 observed
# 0.845-1.0 across 15 cases; R2 sits at 0.8315. Below this the mask is
# fragmented enough that stage 0's largest-component keep discards a
# meaningful share of the segmentation.
MIN_LARGEST_COMPONENT_FRACTION = 0.80

# Stage 0 keeps at most 2 components. Past this many, the mask is speckled
# rather than merely imperfect — worth saying out loud even when the largest
# component still dominates.
MAX_PLAUSIBLE_COMPONENTS = 20


def _add(out: list[dict[str, Any]], severity: str, code: str, message: str) -> None:
    out.append({"severity": severity, "code": code, "message": message})


def check_spacing(spacing_um: Optional[float],
                  header_zooms_mm: Optional[tuple] = None) -> list[dict[str, Any]]:
    """Check the declared voxel spacing against what the pipeline assumes.

    `spacing_um` is the authoritative value — it comes from the run's own
    params, not from Dataset.voxel_size_um, which is rewritten on re-ingest.
    `header_zooms_mm` is the NIfTI header's own spacing, checked only for
    agreement with the declared value.
    """
    findings: list[dict[str, Any]] = []
    if spacing_um is None:
        _add(findings, "fail", "spacing_unknown",
             "No voxel spacing was supplied, so the mm measurements cannot be "
             "computed and the pipeline's 4um geometry assumption cannot be checked.")
        return findings

    dev = abs(spacing_um - PIPELINE_SPACING_UM) / PIPELINE_SPACING_UM
    if dev > SPACING_RTOL:
        _add(findings, "fail", "spacing_mismatch",
             f"Voxel spacing is {spacing_um:g} um, but digitpipe_v5's geometry is "
             f"built for {PIPELINE_SPACING_UM:g} um ({dev:.1%} off, tolerance "
             f"{SPACING_RTOL:.0%}). Its downsample factor, shrink-wrap percentiles "
             f"and socket erosion radii are voxel counts tuned for a 4um grid, so "
             f"at this spacing they span the wrong physical distance. The mm values "
             f"would still be scaled correctly from a structurally wrong "
             f"segmentation. Pass --allow-spacing-mismatch to measure anyway.")

    if header_zooms_mm:
        declared_mm = spacing_um / 1000.0
        # One finding for the whole header, not one per axis — three copies of the
        # same sentence buries whatever else the report has to say.
        off = []
        for axis, z in zip("xyz", header_zooms_mm):
            try:
                z = float(z)
            except (TypeError, ValueError):
                continue
            if z <= 0:
                continue
            if abs(z - declared_mm) / declared_mm > SPACING_RTOL:
                off.append(f"{axis}={z * 1000:g}um")
        if off:
            _add(findings, "warn", "spacing_header_disagrees",
                 f"NIfTI header spacing ({', '.join(off)}) disagrees with the "
                 f"{spacing_um:g} um this run declares. The declared value is what "
                 f"the mm conversions use; if the header is the correct one, this "
                 f"measurement is scaled wrong.")
    return findings


def check_mask_array(mask, spacing_um: Optional[float] = None,
                     header_zooms_mm: Optional[tuple] = None) -> tuple[list[dict[str, Any]], dict]:
    """Evaluate an already-loaded mask. Returns (findings, stats).

    Split out from `check_mask_file` so callers that already hold the array —
    segment_microct.py right after inference — can reuse it without a reload.
    """
    import numpy as np

    findings = check_spacing(spacing_um, header_zooms_mm)
    stats: dict[str, Any] = {}

    binary = np.asarray(mask) > 0
    total = int(binary.size)
    fg = int(binary.sum())
    frac = (fg / total) if total else 0.0
    stats.update({"total_voxels": total, "foreground_voxels": fg,
                  "foreground_fraction": round(frac, 6)})

    if fg == 0:
        _add(findings, "fail", "empty_mask",
             "The mask is empty — no foreground voxels at all. There is nothing "
             "to measure.")
        return findings, stats

    if frac < MIN_FOREGROUND_FRACTION_FAIL:
        _add(findings, "fail", "mask_degenerate",
             f"Foreground is {frac:.2%} of the volume ({fg:,} voxels), below the "
             f"{MIN_FOREGROUND_FRACTION_FAIL:.2%} floor. The segmentation found "
             f"essentially nothing.")
    elif frac > MAX_FOREGROUND_FRACTION_FAIL:
        _add(findings, "fail", "mask_oversegmented",
             f"Foreground is {frac:.1%} of the volume, above the "
             f"{MAX_FOREGROUND_FRACTION_FAIL:.0%} ceiling. This is the signature of "
             f"a corrupted or placeholder mask rather than a segmentation "
             f"(a known bad label in the perios reference set was 99.9%).")
    else:
        lo, hi = OBSERVED_FOREGROUND_FRACTION
        if not (lo <= frac <= hi):
            _add(findings, "warn", "foreground_fraction_unusual",
                 f"Foreground is {frac:.2%} of the volume, outside the "
                 f"{lo:.1%}-{hi:.1%} range seen on validated phalanx cases. Not "
                 f"necessarily wrong — a wider field of view lowers this — but "
                 f"worth confirming the specimen is framed as expected.")

    # Connected components. Only reached when there is foreground to label.
    try:
        from scipy import ndimage
    except ImportError:
        stats["components"] = None
        _add(findings, "warn", "components_unchecked",
             "scipy is not installed, so mask fragmentation was not checked. "
             "Install the 'morph' extra to enable it.")
        return findings, stats

    labeled, n_components = ndimage.label(binary)
    stats["components"] = int(n_components)
    if n_components > 0:
        sizes = ndimage.sum(binary, labeled, range(1, n_components + 1))
        total_fg = float(sizes.sum())
        largest_frac = float(sizes.max() / total_fg) if total_fg else 0.0
        stats["largest_component_fraction"] = round(largest_frac, 4)
        # Stage 0 keeps the largest component and, at most, one more. Everything
        # else is discarded before any measurement happens.
        stats["discarded_fraction_estimate"] = round(1.0 - largest_frac, 4)

        if largest_frac < MIN_LARGEST_COMPONENT_FRACTION:
            _add(findings, "warn", "mask_fragmented",
                 f"The largest connected component holds {largest_frac:.1%} of the "
                 f"foreground across {n_components:,} components. Stage 0 keeps only "
                 f"the largest (plus a second within 0.05mm) and discards the rest, "
                 f"so roughly {1 - largest_frac:.0%} of this segmentation will not "
                 f"reach the measurement.")
        elif n_components > MAX_PLAUSIBLE_COMPONENTS:
            _add(findings, "warn", "mask_speckled",
                 f"The mask has {n_components:,} connected components (largest holds "
                 f"{largest_frac:.1%}). The dominant object is intact, but the "
                 f"speckle around it is discarded by stage 0 and suggests the "
                 f"segmentation is noisier than the validated cases.")
    return findings, stats


def check_mask_file(mask_path: str, spacing_um: Optional[float] = None,
                    image_path: Optional[str] = None) -> tuple[list[dict[str, Any]], dict]:
    """Load a mask (and optionally its paired grayscale) from disk and check it.

    The header spacing is read from the grayscale volume when one is given,
    since that is the file that carries the scanner's own metadata; the mask is
    written by nnU-Net and inherits it.
    """
    findings: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}

    if not os.path.isfile(mask_path):
        _add(findings, "fail", "mask_missing", f"Mask not found: {mask_path}")
        return findings, stats

    try:
        import nibabel as nib
        import numpy as np
    except ImportError as e:
        _add(findings, "warn", "qc_unavailable",
             f"Mask QC skipped — {e}. Install the 'morph' extra to enable it.")
        return findings, stats

    zooms = None
    header_source = image_path if (image_path and os.path.isfile(image_path)) else mask_path
    try:
        zooms = tuple(nib.load(header_source).header.get_zooms()[:3])
        stats["header_spacing_um"] = [round(float(z) * 1000, 4) for z in zooms]
    except Exception as e:  # noqa: BLE001 — a header we cannot read is a warning, not a stop
        _add(findings, "warn", "header_unreadable",
             f"Could not read voxel spacing from {header_source}: "
             f"{type(e).__name__}: {e}")

    try:
        data = np.asanyarray(nib.load(mask_path).dataobj)
    except Exception as e:  # noqa: BLE001
        _add(findings, "fail", "mask_unreadable",
             f"Could not read the mask at {mask_path}: {type(e).__name__}: {e}")
        return findings, stats

    array_findings, array_stats = check_mask_array(data, spacing_um, zooms)
    findings.extend(array_findings)
    stats.update(array_stats)
    return findings, stats


def failures(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset that should block a measurement."""
    return [f for f in findings if f.get("severity") == "fail"]


def summarize(findings: list[dict[str, Any]]) -> Optional[str]:
    """One-line summary for a list view, or None if the mask looked fine."""
    if not findings:
        return None
    n_fail = len(failures(findings))
    if n_fail:
        return f"{n_fail} blocking mask problem{'s' if n_fail != 1 else ''}"
    n = len(findings)
    return f"{n} mask warning{'s' if n != 1 else ''} — review before use"
