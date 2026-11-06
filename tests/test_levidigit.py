"""LeviDigit as the official BV/TV + digit-length measurement.

Covers, end to end on synthetic phantoms (no real scans needed):
  * scripts/measure_levidigit.py — runs the vendored LeviDigit code and writes
    the envelope the worker parses; BV/TV is checked against a hand count.
  * the SK / LeviDigit tagging (pipelines.py) and the API surface: enqueue,
    source filters, official-only stats, normalization using official values
    only, and file serving — through the real worker code path.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
DRIVER = REPO_ROOT / "scripts" / "measure_levidigit.py"

np = pytest.importorskip("numpy")
nib = pytest.importorskip("nibabel")
pytest.importorskip("scipy")
pytest.importorskip("skimage")


def make_phantom(folder: Path, *, spacing_mm: float = 0.004, big: bool = True,
                 case: str = "ph") -> tuple[Path, Path, int, int]:
    """A cylinder with a socket scooped out of its proximal end and a bright
    cortical shell. `big` is large enough for LeviDigit's concavity to survive
    its 5 erosions; the small one is not, so its digit length fails."""
    Z, R, C = (200, 30, 22) if big else (120, 16, 11)
    n = 2 * R + 8
    z, y, x = np.mgrid[0:Z, 0:n, 0:n]
    c = n // 2
    r = np.sqrt((y - c) ** 2 + (x - c) ** 2)
    mask = (r <= R) & (z >= 6) & (z < Z - 6)
    mask &= ~(np.sqrt((y - c) ** 2 + (x - c) ** 2 + (z - 6) ** 2) <= C)
    img = np.full(mask.shape, 10, np.uint8)
    img[mask] = 40
    img[mask & (r >= R - 6)] = 120
    aff = np.diag([spacing_mm, spacing_mm, spacing_mm, 1.0])
    folder.mkdir(parents=True, exist_ok=True)
    image_p, mask_p = folder / f"{case}_0000.nii.gz", folder / f"{case}.nii.gz"
    nib.save(nib.Nifti1Image(img, aff), str(image_p))
    nib.save(nib.Nifti1Image(mask.astype(np.uint8), aff), str(mask_p))
    return image_p, mask_p, int(mask.sum()), int((mask & (img >= 60)).sum())


def run_driver(image: Path, mask: Path, out: Path, *extra: str, case: str = "ph"):
    cmd = [sys.executable, str(DRIVER), "--mask", str(mask), "--image", str(image),
           "--case", case, "--out", str(out), "--bone-threshold", "60",
           "--spacing-um", "4", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True)
    res = out / f"{case}_measurement.json"
    return p, (json.loads(res.read_text()) if res.exists() else None)


# ------------------------------------------------------------------ driver ---

@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("ld_full")
    image, mask, tv, bv = make_phantom(d / "in")
    p, res = run_driver(image, mask, d / "out", "--skip-viz")
    return p, res, tv, bv, d / "out"


def test_driver_bvtv_matches_hand_count(full_run):
    p, res, tv, bv, _ = full_run
    assert p.returncode == 0, p.stdout + p.stderr
    m = res["metrics"]
    assert m["tv_voxels"] == tv and m["bv_voxels"] == bv
    assert m["bv_tv_percent"] == pytest.approx(100.0 * bv / tv)
    assert m["bv_tv"] == pytest.approx(bv / tv, abs=1e-8)
    assert m["tv_mm3"] == pytest.approx(tv * 0.004 ** 3)
    assert m["bone_threshold"] == 60.0
    assert m["source_tag"] == "LeviDigit"
    assert res["pipeline_version"] == "levidigit_v1"


def test_driver_measures_digit_length(full_run):
    p, res, *_ = full_run
    m = res["metrics"]
    assert m["digit_length_status"] == "complete" and m["digit_length_complete"] is True
    # Socket centroid to the distal tip of a 188-voxel-long phantom at 4 um:
    # well under its full 0.75 mm length, well over zero.
    assert 0.4 < m["digit_length_mm"] < 0.76
    assert m["digit_length_um"] == pytest.approx(m["digit_length_mm"] * 1000)
    assert res["annotated_nii"] and Path(res["annotated_nii"]).is_file()
    assert res["csv_path"] and Path(res["csv_path"]).is_file()
    assert res["figure_png"] is None            # --skip-viz


def test_driver_cleans_up_staged_inputs(full_run):
    *_, out = full_run
    assert not (out / "levidigit" / "input").exists()
    assert not (out / "levidigit" / "masks").exists()


def test_failed_length_is_none_not_zero(tmp_path):
    image, mask, tv, bv = make_phantom(tmp_path / "in", big=False)
    p, res = run_driver(image, mask, tmp_path / "out", "--skip-viz")
    assert p.returncode == 0, p.stdout + p.stderr
    m = res["metrics"]
    assert m["digit_length_status"].startswith("failed:")
    assert m["digit_length_mm"] is None and m["digit_length_complete"] is False
    assert m["bv_voxels"] == bv          # BV/TV still reported


def test_bvtv_only_skips_length(tmp_path):
    image, mask, tv, bv = make_phantom(tmp_path / "in", big=False)
    p, res = run_driver(image, mask, tmp_path / "out", "--bvtv-only")
    assert p.returncode == 0, p.stdout + p.stderr
    m = res["metrics"]
    assert m["digit_length_mm"] is None
    assert m["digit_length_status"].startswith("not measured")
    assert m["bv_tv_percent"] == pytest.approx(100.0 * bv / tv)


def test_wrong_spacing_is_refused(tmp_path):
    image, mask, *_ = make_phantom(tmp_path / "in", spacing_mm=0.005, big=False)
    p, res = run_driver(image, mask, tmp_path / "out", "--bvtv-only")
    assert p.returncode == 2 and res is None
    assert "Unexpected voxel spacing" in p.stdout


# -------------------------------------------------------------------- tags ---

def test_source_tags():
    from microct_lab.pipelines import matches_source, source_tag
    assert source_tag("levidigit_v1") == "LeviDigit"
    assert source_tag("bvtv_thresh_v1") == "SK"
    assert source_tag("digitpipe_v5") == "SK"
    assert matches_source("levidigit_v1", "official")
    assert not matches_source("digitpipe_v5", "official")
    assert matches_source("bvtv_thresh_v1", "sk")
    assert matches_source("digitpipe_v5", "all")


# --------------------------------------------------------------------- API ---

@pytest.fixture()
def api(tmp_path, monkeypatch):
    """The measurements + analyses routers over a throwaway in-memory registry,
    so nothing touches the real state dir."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from microct_lab import worker
    from microct_lab.config import settings
    from microct_lab.database import Base, get_db
    from microct_lab.models import Dataset, Measurement, Model, Run
    from microct_lab.routers import analyses, measurements

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(settings, "results_root", tmp_path / "results")
    # The worker's cancel poll opens the REAL SessionLocal; point it away.
    monkeypatch.setattr(worker, "_cancel_requested", lambda *a, **k: False)

    app = FastAPI()
    app.include_router(measurements.router)
    app.include_router(analyses.router)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = _db

    db = Session()
    model = Model(name="LeviDigit", path=str(tmp_path / "model"))
    db.add(model)
    db.flush()

    def add_run(name: str, subtype: str | None, big: bool = True):
        folder = tmp_path / "runs" / name
        image, mask, tv, bv = make_phantom(folder, big=big, case=name)
        ds = Dataset(name=name, slices_path=str(folder / "slices"), type="uct",
                     subtype=subtype)
        db.add(ds)
        db.flush()
        run = Run(dataset_id=ds.id, model_id=model.id, status="succeeded",
                  input_nii=str(image), mask_nii=str(mask), params={"spacing_um": 4.0})
        db.add(run)
        db.commit()
        return ds, run, tv, bv

    def work(mid: int):
        """Execute one queued measurement with the real worker code."""
        with Session() as s:
            m = s.get(Measurement, mid)
            m.status = "running"
            s.commit()
            worker._execute_measurement(s, m)
            return m.status, m.error

    yield TestClient(app), db, add_run, work
    db.close()


def test_enqueue_defaults_to_levidigit_and_freezes_threshold(api):
    client, db, add_run, work = api
    ds, run, tv, bv = add_run("BDNF-R-T3", "digit")
    r = client.post("/api/measurements", json={"run_ids": [run.id], "skip_viz": True})
    assert r.status_code == 200, r.text
    m = r.json()[0]
    assert m["pipeline_version"] == "levidigit_v1"
    assert m["source_tag"] == "LeviDigit" and m["official"] is True
    assert m["params"]["bone_threshold"] == 60.0
    assert m["params"]["bvtv_only"] is False
    assert m["output_dir"].endswith(f"__levidigit__m{m['id']}")

    status, error = work(m["id"])
    assert status == "succeeded", error
    got = client.get(f"/api/measurements/{m['id']}").json()
    assert got["metrics"]["bv_tv_percent"] == pytest.approx(100.0 * bv / tv)
    assert got["metrics"]["digit_length_status"] == "complete"
    assert client.get(f"/api/measurements/{m['id']}/annotated.nii.gz").status_code == 200
    csv = client.get(f"/api/measurements/{m['id']}/export.csv")
    assert csv.status_code == 200 and "bv_tv_percent" in csv.text
    assert client.get(f"/api/measurements/{m['id']}/figure.png").status_code == 404


def test_non_digit_dataset_gets_bvtv_only(api):
    client, db, add_run, work = api
    ds, run, tv, bv = add_run("HO-1", "ho", big=False)
    r = client.post("/api/measurements", json={"run_ids": [run.id], "skip_viz": True})
    assert r.status_code == 200, r.text
    m = r.json()[0]
    assert m["params"]["bvtv_only"] is True
    status, error = work(m["id"])
    assert status == "succeeded", error
    got = client.get(f"/api/measurements/{m['id']}").json()
    assert got["metrics"]["digit_length_mm"] is None
    assert got["metrics"]["bv_voxels"] == bv


def test_threshold_out_of_range_rejected(api):
    client, db, add_run, work = api
    ds, run, *_ = add_run("R2", "digit", big=False)
    r = client.post("/api/measurements", json={"run_ids": [run.id], "bone_threshold": 300})
    assert r.status_code == 400


def test_sk_rows_tagged_and_filtered(api):
    client, db, add_run, work = api
    from microct_lab.models import Measurement
    ds, run, *_ = add_run("BDNF-L-T2", "digit", big=False)
    db.add(Measurement(run_id=run.id, dataset_id=ds.id, status="succeeded",
                       pipeline_version="bvtv_thresh_v1", metrics={"bv_tv": 0.9}))
    db.add(Measurement(run_id=run.id, dataset_id=ds.id, status="succeeded",
                       pipeline_version="digitpipe_v5", bone_length_mm=1.3, metrics={}))
    db.commit()
    made = client.post("/api/measurements",
                       json={"run_ids": [run.id], "skip_viz": True}).json()[0]
    work(made["id"])

    every = client.get("/api/measurements", params={"run_id": run.id}).json()
    assert sorted(m["source_tag"] for m in every) == ["LeviDigit", "SK", "SK"]
    official = client.get("/api/measurements",
                          params={"run_id": run.id, "source": "official"}).json()
    assert [m["pipeline_version"] for m in official] == ["levidigit_v1"]
    sk = client.get("/api/measurements", params={"run_id": run.id, "source": "sk"}).json()
    assert {m["pipeline_version"] for m in sk} == {"bvtv_thresh_v1", "digitpipe_v5"}
    assert client.get("/api/measurements", params={"source": "x"}).status_code == 400

    stats = client.get("/api/measurements/stats").json()
    assert stats["source"] == "official" and stats["measurements"] == 1
    assert "bv_tv_percent" in stats["metrics"] and "bone_length_mm" not in stats["metrics"]
    sk_stats = client.get("/api/measurements/stats", params={"source": "sk"}).json()
    assert sk_stats["measurements"] == 1          # digitpipe only, not interim BV/TV
    assert sk_stats["metrics"]["bone_length_mm"]["n"] == 1


def test_normalization_uses_official_bvtv_only(api):
    client, db, add_run, work = api
    from microct_lab.models import DatasetSet, Experiment, Measurement, Project
    proj = Project(name="P")
    db.add(proj)
    db.flush()
    exp = Experiment(project_id=proj.id, name="E")
    db.add(exp)
    db.flush()
    st = DatasetSet(experiment_id=exp.id, name="S")
    db.add(st)
    db.flush()
    ds_a, run_a, tv, bv = add_run("M1-R-T3", "digit", big=False)
    ds_b, run_b, *_ = add_run("M1-L-T3", "digit", big=False)
    for d in (ds_a, ds_b):
        d.set_id = st.id
    # ds_b has ONLY an SK value — it must not be used.
    db.add(Measurement(run_id=run_b.id, dataset_id=ds_b.id, status="succeeded",
                       pipeline_version="bvtv_thresh_v1", metrics={"bv_tv": 0.5}))
    db.commit()
    made = client.post("/api/measurements",
                       json={"run_ids": [run_a.id], "skip_viz": True}).json()[0]
    work(made["id"])

    r = client.post("/api/analyses/normalize-bvtv",
                    json={"set_ids": [st.id], "mode": "per_set",
                          "reference_dataset_ids": [ds_a.id]})
    assert r.status_code == 200, r.text
    rows = {row["name"]: row for row in r.json()["rows"]}
    assert rows["M1-R-T3"]["source"] == "levidigit"
    assert rows["M1-R-T3"]["bvtv"] == pytest.approx(bv / tv, abs=1e-8)
    assert rows["M1-L-T3"]["bvtv"] is None
    assert "LeviDigit" in rows["M1-L-T3"]["note"]
