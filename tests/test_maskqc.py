"""Regression tests for the pre-measurement mask checks.

These pin the thresholds to the data they were derived from. The point is not
that the numbers are sacred — it is that changing one should be a deliberate act
with a reason, not a side effect of tuning something else. Every constant here
traces to either R2 (this project's own measured dataset) or the 15 validated
`Digit*` cases from the sibling `perios2` project.
"""
from __future__ import annotations

import numpy as np
import pytest

from microct_lab import maskqc


def codes(findings):
    return {f["code"] for f in findings}


def by_code(findings, code):
    return next(f for f in findings if f["code"] == code)


# --------------------------------------------------------------------- spacing
class TestSpacing:
    def test_exact_pipeline_spacing_passes(self):
        assert maskqc.check_spacing(4.0) == []

    def test_real_scanner_spacing_passes(self):
        # R2/R4 report 4.000059 um. Scanner metadata is never exactly round, and
        # a check that cannot tolerate that is useless in practice.
        assert maskqc.check_spacing(4.000059) == []

    @pytest.mark.parametrize("spacing_um", [10.0, 2.0, 4.5, 0.5])
    def test_wrong_spacing_is_blocking(self, spacing_um):
        findings = maskqc.check_spacing(spacing_um)
        assert "spacing_mismatch" in codes(findings)
        assert by_code(findings, "spacing_mismatch")["severity"] == "fail"

    def test_edge_of_tolerance(self):
        # 1% is the tolerance; just inside passes, just outside fails.
        assert maskqc.check_spacing(4.0 * 1.009) == []
        assert "spacing_mismatch" in codes(maskqc.check_spacing(4.0 * 1.011))

    def test_missing_spacing_is_blocking(self):
        findings = maskqc.check_spacing(None)
        assert by_code(findings, "spacing_unknown")["severity"] == "fail"

    def test_header_disagreement_warns_once_not_per_axis(self):
        # Declared 4um, header says 8um on all three axes. One finding, not three.
        findings = maskqc.check_spacing(4.0, header_zooms_mm=(0.008, 0.008, 0.008))
        disagreements = [f for f in findings if f["code"] == "spacing_header_disagrees"]
        assert len(disagreements) == 1
        assert disagreements[0]["severity"] == "warn"
        for axis in "xyz":
            assert f"{axis}=8um" in disagreements[0]["message"]

    def test_header_agreement_is_silent(self):
        assert maskqc.check_spacing(4.000059, header_zooms_mm=(0.004, 0.004, 0.004)) == []


# ------------------------------------------------------------ foreground bands
class TestForegroundFraction:
    def _cube(self, fg_fraction, shape=(60, 60, 60)):
        """A mask with one solid connected block covering `fg_fraction`."""
        m = np.zeros(shape, dtype=np.uint8)
        n_fg = int(round(fg_fraction * m.size))
        flat = m.reshape(-1)
        flat[:n_fg] = 1
        return m

    def test_empty_mask_is_blocking(self):
        findings, stats = maskqc.check_mask_array(np.zeros((20, 20, 20)), spacing_um=4.0)
        assert by_code(findings, "empty_mask")["severity"] == "fail"
        assert stats["foreground_voxels"] == 0

    def test_near_total_foreground_is_blocking(self):
        # perios2's corrupted Digit46_021 ground-truth label was 0.999.
        findings, _ = maskqc.check_mask_array(self._cube(0.999), spacing_um=4.0)
        assert by_code(findings, "mask_oversegmented")["severity"] == "fail"

    def test_r2_real_fraction_does_not_warn(self):
        # R2 sits at 0.01677 — BELOW perios2's own observed floor of 0.021, because
        # this app ingests whole SkyScan reconstructions rather than tightly-cropped
        # Digit volumes. Adopting perios2's band unchanged would have failed a real,
        # successfully-measured dataset. This test is why the band was widened.
        findings, stats = maskqc.check_mask_array(self._cube(0.01677), spacing_um=4.0)
        assert stats["foreground_fraction"] == pytest.approx(0.01677, abs=1e-4)
        assert "foreground_fraction_unusual" not in codes(findings)
        assert maskqc.failures(findings) == []

    @pytest.mark.parametrize("frac", [0.021, 0.08, 0.160])
    def test_perios2_observed_range_does_not_warn(self, frac):
        findings, _ = maskqc.check_mask_array(self._cube(frac), spacing_um=4.0)
        assert "foreground_fraction_unusual" not in codes(findings)

    def test_unusual_but_not_catastrophic_only_warns(self):
        findings, _ = maskqc.check_mask_array(self._cube(0.30), spacing_um=4.0)
        assert by_code(findings, "foreground_fraction_unusual")["severity"] == "warn"
        assert maskqc.failures(findings) == []


# --------------------------------------------------------- connected components
class TestComponents:
    def _blocks(self, sizes, pad=2):
        """A mask of cubic blocks, each separated from the others so they stay
        distinct connected components.

        Blocks are laid out on a 3D grid of equal cells, one block per cell,
        with the volume sized to fit them all. Sizing the volume to the layout
        (rather than the other way round) is what keeps a long list of small
        blocks from running off the end and silently merging.
        """
        sides = [max(1, int(round(n ** (1 / 3)))) for n in sizes]
        cell = max(sides) + pad
        per_axis = int(np.ceil(len(sizes) ** (1 / 3)))
        grid_dim = max(per_axis, 1) * cell + pad
        # Then grow the volume until the foreground lands in the plausible band.
        # A fixture built only to fit its blocks ends up ~58% foreground, which
        # trips the over-segmentation gate and makes a component test fail for a
        # reason that has nothing to do with components.
        total_fg = sum(s ** 3 for s in sides)
        want_dim = int(np.ceil((total_fg / 0.08) ** (1 / 3)))
        dim = max(grid_dim, want_dim)
        m = np.zeros((dim, dim, dim), dtype=np.uint8)
        for i, side in enumerate(sides):
            gz, rem = divmod(i, per_axis * per_axis)
            gy, gx = divmod(rem, per_axis)
            z, y, x = gz * cell + pad, gy * cell + pad, gx * cell + pad
            m[z:z + side, y:y + side, x:x + side] = 1
        return m

    def test_single_component_is_clean(self):
        findings, stats = maskqc.check_mask_array(self._blocks([8000]), spacing_um=4.0)
        assert stats["components"] == 1
        assert stats["largest_component_fraction"] == 1.0
        assert stats["discarded_fraction_estimate"] == 0.0
        assert findings == []  # a single clean blob should say nothing at all

    def test_fragmented_mask_warns_and_is_not_blocking(self):
        # Two near-equal blocks: the largest holds ~50%, well under the 0.80 floor.
        findings, stats = maskqc.check_mask_array(self._blocks([8000, 8000]), spacing_um=4.0)
        assert stats["largest_component_fraction"] < maskqc.MIN_LARGEST_COMPONENT_FRACTION
        assert by_code(findings, "mask_fragmented")["severity"] == "warn"
        assert maskqc.failures(findings) == []

    def test_discard_estimate_is_the_complement_of_the_largest(self):
        _, stats = maskqc.check_mask_array(self._blocks([8000, 1000]), spacing_um=4.0)
        assert stats["discarded_fraction_estimate"] == pytest.approx(
            1.0 - stats["largest_component_fraction"], abs=1e-6)

    def test_speckle_warns_even_when_the_largest_dominates(self):
        # R2's shape: one dominant object plus many small fragments. The largest
        # still holds >80%, so mask_fragmented does not fire — but 72 components
        # is worth saying, because stage 0 keeps at most 2 of them.
        sizes = [27000] + [27] * 25
        findings, stats = maskqc.check_mask_array(self._blocks(sizes), spacing_um=4.0)
        assert stats["components"] > maskqc.MAX_PLAUSIBLE_COMPONENTS
        assert stats["largest_component_fraction"] > maskqc.MIN_LARGEST_COMPONENT_FRACTION
        assert by_code(findings, "mask_speckled")["severity"] == "warn"
        assert maskqc.failures(findings) == []


# ------------------------------------------------------------------- R2 profile
class TestR2KnownProfile:
    """R2's real measured profile, from the mask at USBFiles/results/R2.nii.gz.

    Recorded 2026-08-15 by loading the file directly:
        shape (1128, 1128, 459) = 584,024,256 voxels
        foreground              = 9,795,280 (0.016772)
        connected components    = 72
        largest component holds = 0.8315
    The pipeline's own stage 0 then reported "removed 1,650,779, kept 1 comp",
    which is 0.168528 of the foreground — matching the 0.1685 estimated here to
    four decimal places. That agreement is the reason the estimate is trusted
    enough to report.
    """

    R2_FOREGROUND_FRACTION = 0.016772
    R2_LARGEST_COMPONENT_FRACTION = 0.8315
    R2_COMPONENTS = 72
    R2_PIPELINE_REMOVED_FRACTION = 1650779 / 9795280

    def test_estimate_matches_what_the_pipeline_actually_discarded(self):
        assert (1.0 - self.R2_LARGEST_COMPONENT_FRACTION) == pytest.approx(
            self.R2_PIPELINE_REMOVED_FRACTION, abs=1e-4)

    def test_r2_passes_every_blocking_gate(self):
        # R2 is a real dataset that was measured successfully. If a threshold
        # change ever starts blocking it, that is a decision to make consciously.
        assert self.R2_FOREGROUND_FRACTION > maskqc.MIN_FOREGROUND_FRACTION_FAIL
        assert self.R2_FOREGROUND_FRACTION < maskqc.MAX_FOREGROUND_FRACTION_FAIL
        assert maskqc.check_spacing(4.000059) == []

    def test_r2_is_inside_the_warn_band_for_foreground(self):
        lo, hi = maskqc.OBSERVED_FOREGROUND_FRACTION
        assert lo <= self.R2_FOREGROUND_FRACTION <= hi

    def test_r2_trips_the_speckle_warning(self):
        # 72 components with the largest at 0.8315: above the fragmentation floor,
        # above the component ceiling. Exactly one warning, and it is not blocking.
        assert self.R2_LARGEST_COMPONENT_FRACTION > maskqc.MIN_LARGEST_COMPONENT_FRACTION
        assert self.R2_COMPONENTS > maskqc.MAX_PLAUSIBLE_COMPONENTS


# ----------------------------------------------------------------- report shape
class TestReportShape:
    def test_summarize_is_none_when_clean(self):
        assert maskqc.summarize([]) is None

    def test_summarize_leads_with_blocking_count(self):
        findings = [{"severity": "fail", "code": "a", "message": ""},
                    {"severity": "warn", "code": "b", "message": ""}]
        assert maskqc.summarize(findings) == "1 blocking mask problem"

    def test_summarize_reports_warnings_when_nothing_blocks(self):
        findings = [{"severity": "warn", "code": "b", "message": ""},
                    {"severity": "warn", "code": "c", "message": ""}]
        assert "2 mask warnings" in maskqc.summarize(findings)

    def test_every_finding_carries_severity_code_and_message(self):
        findings, _ = maskqc.check_mask_array(np.zeros((10, 10, 10)), spacing_um=99.0)
        assert findings
        for f in findings:
            assert f["severity"] in ("fail", "warn")
            assert f["code"] and f["message"]

    def test_missing_file_blocks_rather_than_raising(self):
        findings, _ = maskqc.check_mask_file("/nonexistent/mask.nii.gz", spacing_um=4.0)
        assert by_code(findings, "mask_missing")["severity"] == "fail"
