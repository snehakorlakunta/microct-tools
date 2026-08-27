"""Interim threshold BV/TV: converting a Hounsfield threshold to a grey value.

TEMPORARY by design — this exists until the official BV/TV calculation is
available. The measurement itself is deliberately simple: within the
segmentation mask (TV), count the voxels whose intensity clears a bone
threshold (BV); BV/TV is their ratio. The subtlety is the threshold.

The scans here are 8-bit BMP reconstructions with `HU Calibration=OFF` in the
SkyScan log — the pixel values are NOT Hounsfield units. What the log does
record is the reconstruction window: `Minimum/Maximum for CS to Image
Conversion`, the linear-attenuation range (1/mm) mapped onto grey 0..255. So a
threshold given in HU (the lab convention: 800 HU for bone) is converted per
scan:

    mu(HU)  = mu_water * (1 + HU/1000)          # HU definition, solved for mu
    grey    = 255 * (mu - CS_min) / (CS_max - CS_min)

This makes the SAME physical threshold land on the right grey value even when
two scans were reconstructed with different windows — the failure mode a fixed
grey threshold silently suffers. `mu_water` comes from settings
(MICROCT_MU_WATER); its default is calibrated so 800 HU ≈ grey 77 on the R2
window, matching the empirical grey-80 threshold the vendored perios pipeline
uses. Both the HU and the resolved grey are stored on every measurement, so
each number is auditable.
"""
from __future__ import annotations

from typing import Any, Optional

from .config import settings

# The _rec.log keys carrying the reconstruction window (see logparse.parse_rec_log —
# the raw dict is stored on Dataset.log at ingest).
CS_MIN_KEY = "Minimum for CS to Image Conversion"
CS_MAX_KEY = "Maximum for CS to Image Conversion"


def hu_to_attenuation(hu: float, mu_water: float) -> float:
    """Linear attenuation (1/mm) at a given HU: HU = 1000*(mu-mu_water)/mu_water."""
    return mu_water * (1.0 + hu / 1000.0)


def attenuation_to_grey(mu: float, cs_min: float, cs_max: float) -> float:
    """Map an attenuation value through the scan's CS window onto grey 0..255."""
    if cs_max <= cs_min:
        raise ValueError(f"degenerate CS window: [{cs_min}, {cs_max}]")
    g = 255.0 * (mu - cs_min) / (cs_max - cs_min)
    return max(0.0, min(255.0, g))


def _log_float(log: dict, key: str) -> Optional[float]:
    try:
        return float(log[key])
    except (KeyError, TypeError, ValueError):
        return None


def resolve_threshold(log: Optional[dict], threshold_hu: Optional[float] = None,
                      mu_water: Optional[float] = None) -> dict[str, Any]:
    """Resolve the effective grey threshold for one dataset.

    Returns a dict with everything the measurement records:
      threshold_hu, threshold_grey, cs_min, cs_max, mu_water, source
    `source` says how the grey was obtained: "log_window" when the scan's own
    CS window was used, "fallback" when the log lacks the window and the grey
    falls back to the perios empirical 80.
    """
    hu = float(threshold_hu if threshold_hu is not None else settings.bvtv_threshold_hu)
    mw = float(mu_water if mu_water is not None else settings.mu_water)
    cs_min = _log_float(log or {}, CS_MIN_KEY)
    cs_max = _log_float(log or {}, CS_MAX_KEY)
    if cs_min is not None and cs_max is not None and cs_max > cs_min:
        grey = attenuation_to_grey(hu_to_attenuation(hu, mw), cs_min, cs_max)
        source = "log_window"
    else:
        grey = 80.0  # perios digitpipe INTENSITY_THRESHOLD — the empirical fallback
        source = "fallback"
    return {"threshold_hu": hu, "threshold_grey": round(grey, 2),
            "cs_min": cs_min, "cs_max": cs_max, "mu_water": mw, "source": source}
