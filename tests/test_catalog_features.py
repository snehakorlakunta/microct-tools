"""Tests for the Linux-review feature groundwork.

Covers the pure logic added across the phases: digit-ID parsing and mouse
grouping (normalization depends on both), the *_rec_[Tra/Cor/Sag].log ingest
fallback, the per-scan HU->grey threshold conversion, crop-box clamping in the
segmentation script, and the masked-image BMP export.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "src"))

from microct_lab.bvtv import resolve_threshold, CS_MIN_KEY, CS_MAX_KEY  # noqa: E402
from microct_lab.registry import _pick_rec_log, mouse_key, parse_digit_id  # noqa: E402


# ------------------------------------------------------------- digit naming --

@pytest.mark.parametrize("name,expect", [
    ("BDNF-R-T3", "R3"),          # the real convention in this lab's data
    ("BDNF-L-T2", "L2"),
    ("R2", "R2"),
    ("l4", "L4"),
    ("Mouse7_L3_scan", "L3"),
    ("BDNF-R-T3_rec", "R3"),
    ("CTRL2", None),              # L inside a word must not fire
    ("X-L2-R3", None),            # two different digits -> refuse to guess
    ("plainname", None),
    ("R5", None),                 # digit number out of range
])
def test_parse_digit_id(name, expect):
    assert parse_digit_id(name) == expect


def test_mouse_key_groups_same_animal():
    # The L3 reference and the R3/L2 scans of the same mouse must share a key.
    assert mouse_key("BDNF-R-T3") == mouse_key("BDNF-L-T3") == mouse_key("BDNF-L-T2")
    # Distinct animals stay distinct.
    assert mouse_key("BDNF-R-T3") != mouse_key("CTRL-R-T3")
    # A name with no digit token is its own group, never empty.
    assert mouse_key("R2") == "R2"


# ---------------------------------------------------------- log fallback -----

def test_pick_rec_log_prefers_plain(tmp_path):
    plain = tmp_path / "case_rec.log"
    tra = tmp_path / "case_rec_Tra.log"
    sag = tmp_path / "case_rec_Sag.log"
    for f in (plain, tra, sag):
        f.write_text("x")
    assert _pick_rec_log([sag, tra, plain]) == plain
    # Without the plain log, orientation order is Tra > Cor > Sag.
    assert _pick_rec_log([sag, tra]) == tra
    assert _pick_rec_log([sag]) == sag


# ---------------------------------------------------------- HU -> grey -------

def test_hu_to_grey_matches_r2_window():
    """800 HU through the real R2 reconstruction window lands at the empirical
    grey ~77 (perios uses 80) — the calibration described in bvtv.py."""
    log = {CS_MIN_KEY: "0.000000", CS_MAX_KEY: "0.132622"}
    out = resolve_threshold(log, 800.0)
    assert out["source"] == "log_window"
    assert out["threshold_grey"] == pytest.approx(76.8, abs=0.5)
    # The whole conversion is recorded for auditability.
    assert out["cs_max"] == pytest.approx(0.132622)
    assert out["mu_water"] > 0


def test_hu_to_grey_falls_back_without_window():
    out = resolve_threshold({}, 800.0)
    assert out["source"] == "fallback"
    assert out["threshold_grey"] == 80.0
    out2 = resolve_threshold(None, 800.0)
    assert out2["source"] == "fallback"


def test_grey_clamped_to_byte_range():
    log = {CS_MIN_KEY: "0.0", CS_MAX_KEY: "0.01"}  # narrow window -> huge grey
    out = resolve_threshold(log, 800.0)
    assert 0.0 <= out["threshold_grey"] <= 255.0


# ---------------------------------------------------------- crop clamping ----

@pytest.fixture(scope="module")
def seg():
    spec = importlib.util.spec_from_file_location(
        "segment_microct", REPO_ROOT / "scripts" / "segment_microct.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clamp_crop(seg):
    assert seg.clamp_crop([0, 10, 0, 20, 0, 30], 10, 20, 30) == (0, 10, 0, 20, 0, 30)
    # Overshooting bounds clamps instead of failing.
    assert seg.clamp_crop([-5, 99, 5, 99, 5, 99], 10, 20, 30) == (0, 10, 5, 20, 5, 30)
    # A collapsed box is refused (None), not silently emptied.
    assert seg.clamp_crop([8, 3, 0, 20, 0, 30], 10, 20, 30) is None


# ---------------------------------------------------------- masked BMPs ------

def test_masked_image_bmp_keeps_pixels_inside_mask(tmp_path):
    np = pytest.importorskip("numpy")
    PIL_Image = pytest.importorskip("PIL.Image")
    from microct_lab.bmp_export import bmp_dir_for, mask_array_to_bmp

    rng = np.random.default_rng(7)
    image = rng.integers(0, 255, size=(3, 8, 8), dtype=np.uint8)
    mask = np.zeros((3, 8, 8), dtype=np.uint8)
    mask[1, 2:6, 2:6] = 1

    info = mask_array_to_bmp(mask, str(tmp_path), image=image)
    assert info["mode"] == "masked_image"
    assert info["count"] == 3

    files = sorted(tmp_path.glob("*.bmp"))
    got = np.stack([np.array(PIL_Image.open(f)) for f in files])
    # Inside the mask: the original image pixels, bit for bit.
    assert (got[1, 2:6, 2:6] == image[1, 2:6, 2:6]).all()
    # Everywhere else: black.
    outside = got.copy()
    outside[1, 2:6, 2:6] = 0
    assert outside.sum() == 0

    # The two modes get distinct folders — they must never overwrite each other.
    assert bmp_dir_for("/out", "case") != bmp_dir_for("/out", "case", "masked_image")


def test_bvtv_driver_on_synthetic_volume(tmp_path):
    """Run scripts/measure_bvtv.py end to end on a volume with a known fraction."""
    np = pytest.importorskip("numpy")
    sitk = pytest.importorskip("SimpleITK")
    import subprocess

    mask = np.zeros((4, 10, 10), dtype=np.uint8)
    mask[1:3, :, :] = 1                       # TV = 200 voxels
    image = np.zeros((4, 10, 10), dtype=np.uint8)
    image[1, :, :] = 200                      # 100 masked voxels above threshold
    image[3, :, :] = 250                      # bright but OUTSIDE the mask: no BV

    mask_p, img_p = tmp_path / "m.nii.gz", tmp_path / "i.nii.gz"
    sitk.WriteImage(sitk.GetImageFromArray(mask), str(mask_p))
    sitk.WriteImage(sitk.GetImageFromArray(image), str(img_p))

    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "measure_bvtv.py"),
         "--mask", str(mask_p), "--image", str(img_p), "--case", "syn",
         "--out", str(out), "--threshold-grey", "80", "--spacing-um", "4.0"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads((out / "syn_measurement.json").read_text())
    m = result["metrics"]
    assert m["tv_voxels"] == 200
    assert m["bv_voxels"] == 100
    assert m["bv_tv"] == pytest.approx(0.5)
