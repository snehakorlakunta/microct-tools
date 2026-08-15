"""Regression tests for the post-measurement plausibility checks.

Two things are pinned here:

1. **R2's recorded result still produces the warnings it produced when the
   feature was built.** Those six warnings are the reason morphqc exists; if a
   refactor stops producing one of them, that is a silent loss of the only thing
   standing between a wrong-anatomy run and a figure.

2. **Genuine phalanx measurements do NOT trip the checks.** This is the half
   that is easy to forget. A QC rule that flags everything is as useless as one
   that flags nothing, and the reference cohort's own numbers are the sharpest
   available test of false-positive behaviour — they come from the same
   pipeline, on manually-corrected masks, on real specimens.
"""
from __future__ import annotations

import pytest

from microct_lab import morphqc


def codes(warnings):
    return {w["code"] for w in warnings}


# --- R2's real recorded metrics ---------------------------------------------
# Verbatim from USBFiles/results/R2__morph__m2/R2_measurement.json, the run that
# first surfaced the wrong-anatomy problem. Kept as literals rather than read
# from disk so the test survives the results directory being cleared.
R2_METRICS = {
    "socket_volume_mm3": 0.30713702400000004,
    "socket_radius_mm": 0.41855048950888724,
    "bone_length_mm": 0.9872029552641761,
    "phalanx_volume_mm3": 0.789370368,
    "socket_num_components": 10.0,
    "major_axis_explained_variance": 0.8627876809773803,
}

# The six warnings that run actually produced, recorded in its qc_warnings field.
R2_EXPECTED_CODES = {
    "socket_fragmented",
    "socket_fraction",
    "outlier_socket_radius_mm",
    "outlier_socket_volume_mm3",
    "outlier_phalanx_volume_mm3",
    "weak_major_axis",
}


class TestR2Regression:
    def test_r2_produces_exactly_the_warnings_it_did_before(self):
        assert codes(morphqc.evaluate(R2_METRICS)) == R2_EXPECTED_CODES

    def test_socket_volume_is_still_the_most_extreme_outlier(self):
        # +68 SD. This is the headline number in DECISIONS.md 1.2; if the
        # reference constants drift, this assertion is where it shows up.
        mean, sd = morphqc.REFERENCE["socket_volume_mm3"]
        z = (R2_METRICS["socket_volume_mm3"] - mean) / sd
        assert z == pytest.approx(68, abs=1.0)

    def test_phalanx_is_flagged_as_oversized(self):
        # 4.3x the reference mean — the actual reason R2's numbers are wrong,
        # and the correction to the model-identity claim made 2026-08-15.
        mean, _ = morphqc.REFERENCE["phalanx_volume_mm3"]
        assert R2_METRICS["phalanx_volume_mm3"] / mean == pytest.approx(4.3, abs=0.1)

    def test_high_severity_warnings_are_present(self):
        warnings = morphqc.evaluate(R2_METRICS)
        assert any(w["severity"] == "high" for w in warnings)
        assert morphqc.summarize(warnings).endswith("review before use")


class TestReferenceCohortDoesNotFalsePositive:
    """perios's own manually-corrected reference measurements, from the
    `voxel_to_metric_output` sheet of `phalanx_analysis_complete.xlsx` as
    reproduced in perios2's `comparison_15case.csv` (repo
    `snehakorlakunta/perios2`, commit 7f10f5ee, 2026-08-05).

    These are real mouse terminal phalanges measured by this same pipeline, so
    every one of them must pass morphqc's bone-length check. If a constant is
    ever retuned and these start failing, the constant is wrong, not the data.
    """

    # sample -> (our nnU-Net-mask bone length mm, perios's manual-mask reference mm)
    REFERENCE_BONE_LENGTHS = {
        "Digit100_001": (1.016319837718491, 1.025591),
        "Digit106_005": (1.327001651330092, 1.323188),
        "Digit11_006": (1.341751484888615, 1.352095),
        "Digit1_000": (1.029823376490863, 1.054794),
        "Digit24_008": (1.159953763266062, 1.158797),
        "Digit33_014": (0.951850385132631, 0.960321),
        "Digit35_015": (1.057550180954531, 1.072279),
        "Digit39_017": (1.306315359829077, 1.321358),
        "Digit3_012": (1.059078210486802, 1.075964),
    }

    @pytest.mark.parametrize("sample", sorted(REFERENCE_BONE_LENGTHS))
    def test_real_phalanx_bone_length_is_not_flagged(self, sample):
        ours, _ = self.REFERENCE_BONE_LENGTHS[sample]
        warnings = morphqc.evaluate({"bone_length_mm": ours})
        assert "outlier_bone_length_mm" not in codes(warnings), (
            f"{sample} is a genuine phalanx measured by this pipeline; flagging it "
            f"means the reference constants or SD_WARN are mistuned.")

    @pytest.mark.parametrize("sample", sorted(REFERENCE_BONE_LENGTHS))
    def test_manual_reference_bone_length_is_not_flagged(self, sample):
        _, ref = self.REFERENCE_BONE_LENGTHS[sample]
        assert "outlier_bone_length_mm" not in codes(
            morphqc.evaluate({"bone_length_mm": ref}))

    def test_model_mask_reproduces_the_manual_measurement(self):
        """The bound perios2 validated: swapping a manually-corrected mask for an
        nnU-Net prediction moves bone length by ~1%. This is what makes the whole
        automated path trustworthy, so it is worth a standing assertion — a
        re-vendor or model change that breaks it should not pass quietly."""
        diffs = [abs(ours - ref) / ref
                 for ours, ref in self.REFERENCE_BONE_LENGTHS.values()]
        mean_pct = 100 * sum(diffs) / len(diffs)
        assert mean_pct == pytest.approx(1.04, abs=0.05), (
            f"mean absolute bone-length drift is {mean_pct:.2f}%, not the 1.04% "
            f"perios2 validated over these 9 cases")
        assert max(diffs) * 100 < 2.5  # worst case was Digit1_000 at 2.37%


class TestConstantsArePinned:
    """The reference cohort is n=20 from the perios authors' own workflow
    documentation. Changing any of these changes what counts as implausible
    across every measurement this app has ever made, so it should never happen
    as a side effect."""

    def test_reference_cohort_constants(self):
        assert morphqc.REFERENCE == {
            "socket_radius_mm": (0.1436, 0.0147),
            "bone_length_mm": (1.2820, 0.1476),
            "socket_volume_mm3": (0.012795, 0.004336),
            "phalanx_volume_mm3": (0.183885, 0.031703),
        }

    def test_thresholds(self):
        assert morphqc.SD_WARN == 5.0
        assert morphqc.SOCKET_FRACTION_RANGE == (0.01, 0.25)
        assert morphqc.MIN_AXIS_VARIANCE == 0.90

    def test_socket_fraction_of_the_reference_cohort_is_inside_its_own_range(self):
        sv, _ = morphqc.REFERENCE["socket_volume_mm3"]
        pv, _ = morphqc.REFERENCE["phalanx_volume_mm3"]
        lo, hi = morphqc.SOCKET_FRACTION_RANGE
        assert lo <= sv / pv <= hi


class TestBehaviour:
    def test_empty_metrics_yields_nothing(self):
        assert morphqc.evaluate({}) == []
        assert morphqc.summarize([]) is None

    def test_a_plausible_measurement_is_silent(self):
        clean = {
            "socket_radius_mm": 0.1436,
            "bone_length_mm": 1.2820,
            "socket_volume_mm3": 0.012795,
            "phalanx_volume_mm3": 0.183885,
            "socket_num_components": 1,
            "major_axis_explained_variance": 0.98,
        }
        assert morphqc.evaluate(clean) == []

    def test_no_socket_is_reported_but_not_high_severity(self):
        # A specimen genuinely without a socket is a real result, not an error.
        warnings = morphqc.evaluate({"socket_volume_mm3": 0.0,
                                     "phalanx_volume_mm3": 0.183885})
        assert "no_socket" in codes(warnings)
        assert all(w["severity"] != "high" for w in warnings if w["code"] == "no_socket")

    def test_booleans_are_not_treated_as_numbers(self):
        assert morphqc.evaluate({"socket_num_components": True}) == []
