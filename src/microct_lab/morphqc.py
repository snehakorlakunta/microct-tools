"""Plausibility checks for morphometry results.

WHY THIS EXISTS
---------------
The vendored `digitpipe_v5` pipeline is built for one specific thing: a **mouse
terminal phalanx** at ~4 um isotropic. Point it at some other anatomy and it does
not fail — it runs all eight stages, exits cleanly, and emits a full set of
numbers that look entirely ordinary. Stage 4 will happily label whatever
concavity it finds as "the basal socket".

That was confirmed empirically. Running it on R2 produced `status=succeeded` with
a socket volume 24x the reference mean — 68 standard deviations out — split
across 10 disconnected components. Nothing in the pipeline or the worker flagged
it.

A correction to what this file used to say about that result. It attributed R2's
numbers to the segmentation having come from "a glioblastoma model". That was
wrong, and the name is the reason: the checkpoint's folder is
`Dataset501_Glioblastoma` and its `dataset.json` calls itself
`AureliusAnalytics`, but neither describes it. Its own cross-validation summary
lists 55 training cases, every one of them named `Digit<N>_<idx>`, all at 0.004mm
isotropic, binary background/ROI, mean Dice 0.968. It IS a digit/phalanx bone
model — the same checkpoint the sibling `perios2` project validated to 0.942
Dice on true holdouts. `Dataset501_Glioblastoma` is leftover scaffolding from an
nnU-Net tutorial project folder that was reused without renaming.

So the model was never the problem, and the conclusion is unchanged but rests on
better evidence: R2's `phalanx_volume` came out at 0.789 mm3 against a reference
mean of 0.184 — the object is 4.3x too large to be a mouse terminal phalanx —
and its mask carries 72 connected components with only 83% of the foreground in
the largest, so a sixth of it is discarded by stage 0 before anything is
measured. The specimen and the mask explain the numbers. See `maskqc.py`, which
now catches both of those before the pipeline runs at all.

So these checks are not defensive paranoia about crashes; they are the only thing
standing between a wrong-anatomy run and a number that reaches a figure. They are
advisory: a measurement that trips them is still recorded, because the reviewer,
not the software, decides what is real.

REFERENCE POPULATION
--------------------
Mean +/- SD over the n=20 real mouse terminal phalanx samples reported by the
perios authors (see `scripts/perios/PROVENANCE.md` for the vendored commit; the
figures come from that project's own workflow documentation). These are
species-, anatomy-, and resolution-specific. If this app is ever pointed at a
different structure with a purpose-built pipeline, these constants must be
replaced, not stretched.
"""
from __future__ import annotations

from typing import Any

# metric key -> (mean, sd) over the perios reference cohort (n=20), in mm / mm^3.
REFERENCE = {
    "socket_radius_mm": (0.1436, 0.0147),
    "bone_length_mm": (1.2820, 0.1476),
    "socket_volume_mm3": (0.012795, 0.004336),
    "phalanx_volume_mm3": (0.183885, 0.031703),
}

# How far out of the reference distribution before we say something. 5 SD is
# deliberately loose: real biological variation, a different mouse age, or a
# genuinely unusual specimen should pass quietly. What we are trying to catch is
# the wrong-anatomy case, which misses by one to two orders of magnitude.
SD_WARN = 5.0

# Socket volume as a fraction of phalanx volume. The reference cohort sits at
# 0.070 (0.012795 / 0.183885). A socket that is a third of the whole bone is not
# a socket.
SOCKET_FRACTION_RANGE = (0.01, 0.25)

# PCA variance explained by the first component. A terminal phalanx is markedly
# elongated, so a well-formed axis explains almost all of it. A low value means
# the object is not elongated and "proximal-distal axis" is not meaningful for it.
MIN_AXIS_VARIANCE = 0.90


def _add(out: list[dict[str, Any]], severity: str, code: str, message: str) -> None:
    out.append({"severity": severity, "code": code, "message": message})


def evaluate(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Return advisory warnings about a measurement's plausibility.

    An empty list means nothing looked wrong — NOT that the result is verified.
    Severity is "high" for things that are near-certainly wrong anatomy, "medium"
    for things worth a human glance.
    """
    warnings: list[dict[str, Any]] = []
    if not metrics:
        return warnings

    def num(key: str) -> float | None:
        v = metrics.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    # 1. A basal socket is one concave region. Several disconnected blobs means
    #    stage 4 latched onto surface noise rather than a socket.
    comps = num("socket_num_components")
    if comps is not None and comps > 1:
        _add(warnings, "high" if comps > 3 else "medium", "socket_fragmented",
             f"Socket detected as {int(comps)} disconnected components; a basal socket "
             f"should be a single region. Stage 4 likely latched onto surface "
             f"concavities rather than a true socket.")

    # 2. Socket as a fraction of the bone.
    sv, pv = num("socket_volume_mm3"), num("phalanx_volume_mm3")
    if sv is not None and pv:
        frac = sv / pv
        lo, hi = SOCKET_FRACTION_RANGE
        if not (lo <= frac <= hi):
            _add(warnings, "high", "socket_fraction",
                 f"Socket is {frac:.1%} of phalanx volume (reference ~7%, expected "
                 f"{lo:.0%}-{hi:.0%}). The detected region is not socket-shaped.")

    # 3. Absolute magnitudes against the reference cohort.
    for key, (mean, sd) in REFERENCE.items():
        v = num(key)
        if v is None or sd <= 0:
            continue
        z = (v - mean) / sd
        if abs(z) > SD_WARN:
            _add(warnings, "high" if abs(z) > 20 else "medium", f"outlier_{key}",
                 f"{key} = {v:.5g} is {z:+.0f} SD from the reference phalanx cohort "
                 f"(mean {mean:.5g}, SD {sd:.5g}, n=20).")

    # 4. Is the object even elongated enough for a major axis to mean anything?
    var = num("major_axis_explained_variance")
    if var is not None and var < MIN_AXIS_VARIANCE:
        _add(warnings, "medium", "weak_major_axis",
             f"Major axis explains only {var:.1%} of variance (expected >"
             f"{MIN_AXIS_VARIANCE:.0%}). The specimen may not be elongated, making "
             f"the proximal-distal axis — and the bone length measured along it — "
             f"poorly defined.")

    # 5. Nothing found at all. Legitimate (a specimen genuinely without a
    #    detectable socket), but it should never be mistaken for a measurement.
    if sv is not None and sv == 0:
        _add(warnings, "medium", "no_socket",
             "No socket was detected. This is a valid result, not an error, but the "
             "socket and bone-length metrics are undefined for this sample.")

    return warnings


def summarize(warnings: list[dict[str, Any]]) -> str | None:
    """One-line summary for a list view, or None if the result looked plausible."""
    if not warnings:
        return None
    high = sum(1 for w in warnings if w["severity"] == "high")
    if high:
        return f"{high} implausible metric{'s' if high != 1 else ''} — review before use"
    return f"{len(warnings)} metric{'s' if len(warnings) != 1 else ''} worth checking"
