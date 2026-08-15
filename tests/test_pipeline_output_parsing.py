"""Tests for what measure_morphometry.py reads out of the vendored pipeline's stdout.

`run_pipeline.py` catches every per-stage exception and always exits 0, and it
reports what stage 0 discarded on stdout and nowhere else. Both facts mean its
printed output is load-bearing: it is the only honest signal available about
whether the run worked and what it measured. These patterns are therefore a
contract with a file we do not control, and re-vendoring perios can break them
silently. That is what this file guards.

Every sample line below is real output, taken from
USBFiles/results/R2__morph__m2/measure.log or from the vendored
digitpipe_v5/run_pipeline.py source at commit 88bedbaa.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "measure_morphometry.py"


@pytest.fixture(scope="module")
def mm():
    spec = importlib.util.spec_from_file_location("measure_morphometry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDownsampleDiscardCapture:
    # The exact line R2's run produced. 1,650,779 voxels is 16.85% of R2's
    # 9,795,280-voxel mask — a sixth of the segmentation, thrown away before any
    # measurement, previously recorded nowhere.
    REAL_LINE = ("  [1/1] R2: (1128, 1128, 459) -> (564, 564, 230) "
                 "(removed 1,650,779, kept 1 comp)")

    def test_captures_the_real_r2_line(self, mm):
        m = mm.DOWNSAMPLE_DISCARD_RE.search(self.REAL_LINE)
        assert m is not None
        assert int(m.group(1).replace(",", "")) == 1650779
        assert int(m.group(2)) == 1

    def test_discarded_share_matches_the_mask(self, mm):
        m = mm.DOWNSAMPLE_DISCARD_RE.search(self.REAL_LINE)
        removed = int(m.group(1).replace(",", ""))
        assert removed / 9795280 == pytest.approx(0.1685, abs=1e-4)

    def test_two_kept_components_parse(self, mm):
        # Stage 0 keeps a second component when it is within 0.05mm of the first.
        line = "  [3/9] Digit59_032: (463, 760, 758) -> (231, 380, 379) (removed 12,004, kept 2 comp)"
        m = mm.DOWNSAMPLE_DISCARD_RE.search(line)
        assert (int(m.group(1).replace(",", "")), int(m.group(2))) == (12004, 2)

    def test_clean_case_produces_no_match(self, mm):
        # When nothing is removed the pipeline omits the suffix entirely, so
        # absence of a match must mean "nothing discarded", not "parse failed".
        line = "  [1/1] Digit1_000: (237, 548, 368) -> (118, 274, 184)"
        assert mm.DOWNSAMPLE_DISCARD_RE.search(line) is None

    def test_unpunctuated_counts_parse(self, mm):
        # The pipeline formats with thousands separators, but a small count has
        # none — the pattern must not require a comma.
        m = mm.DOWNSAMPLE_DISCARD_RE.search("x: (removed 42, kept 1 comp)")
        assert int(m.group(1)) == 42


class TestFailureMarkers:
    """The strings that mean a stage did not do its job. run_pipeline.py exits 0
    regardless, so these are the difference between a failed measurement and a
    silently partial one."""

    def test_the_marker_set_is_pinned(self, mm):
        assert set(mm.PIPELINE_FAILURE_MARKERS) == {
            "[FAIL]", "ERROR in ", "SKIPPED - missing files",
            "Socket metrics not found",
        }

    @pytest.mark.parametrize("line", [
        "  [FAIL] Socket Detection: 0 items (12.4s)",
        "ERROR in Bone Length: list index out of range",
        "      SKIPPED - missing files",
        "Socket metrics not found - skipping",
    ])
    def test_real_failure_lines_are_detected(self, mm, line):
        assert any(mark in line.strip() for mark in mm.PIPELINE_FAILURE_MARKERS)

    @pytest.mark.parametrize("line", [
        "  [OK] Socket Detection: 1 items (692.9s)",
        "  [1/1] R2: (1128, 1128, 459) -> (564, 564, 230)",
        "      Socket: 1,234 voxels, radius=104.6",
        "Done. Output: /target/labelsSocketDetected_ds",
        "Total time: 0:13:45",
    ])
    def test_normal_lines_are_not_flagged(self, mm, line):
        assert not any(mark in line.strip() for mark in mm.PIPELINE_FAILURE_MARKERS)

    def test_skip_viz_does_not_look_like_a_failure(self, mm):
        # --skip-viz is an argparse flag, not a stage outcome; a normal fast run
        # must never be read as a failed one.
        assert not any(mark in "--skip-viz" for mark in mm.PIPELINE_FAILURE_MARKERS)


class TestSpacingDowngrade:
    """--allow-spacing-mismatch must downgrade the spacing gate, not delete it."""

    def test_downgrade_keeps_the_finding_and_marks_it(self, mm, tmp_path, monkeypatch):
        from microct_lab import maskqc

        fake_mask = tmp_path / "case.nii.gz"
        fake_mask.write_bytes(b"")

        def fake_check(mask_path, spacing_um=None, image_path=None):
            return list(maskqc.check_spacing(spacing_um)), {"foreground_voxels": 1}

        monkeypatch.setattr(maskqc, "check_mask_file", fake_check)

        record = mm.run_mask_qc(str(fake_mask), None, 10.0, True)
        assert record["checked"] is True
        assert record["spacing_mismatch_allowed"] is True
        assert "spacing_mismatch" in record["downgraded"]
        finding = next(f for f in record["findings"] if f["code"] == "spacing_mismatch")
        assert finding["severity"] == "warn"
        assert "downgraded by --allow-spacing-mismatch" in finding["message"]
