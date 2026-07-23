"""Cross-dataset statistics and experiment/project export bundles.

Measurements come from each dataset's latest *successful* run (ROI volume in mm^3).
Groups are the sets inside an experiment, so a two-arm study (treated vs control)
becomes a two-group comparison. Stats use scipy when available and fall back to a
dependency-free Welch's t-test with a normal-approximation p-value otherwise.
"""
from __future__ import annotations

import io
import json
import math
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Analysis, Dataset, DatasetSet, Experiment, Project, Run


# --------------------------------------------------------------------------- math
def describe(values: list[float]) -> dict:
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None, "sem": None}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    return {"n": n, "mean": mean, "sd": sd, "min": min(vals), "max": max(vals),
            "sem": (sd / math.sqrt(n)) if n else None}


def _normal_p_two_sided(z: float) -> float:
    return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))


def compare_two(a: list[float], b: list[float]) -> dict:
    """Welch's t-test between two groups. Uses scipy if present for an exact p-value
    (t-distribution + a Mann-Whitney U), else a normal-approximation fallback."""
    a = [float(v) for v in a if v is not None]
    b = [float(v) for v in b if v is not None]
    da, db_ = describe(a), describe(b)
    out = {"group_a": da, "group_b": db_,
           "mean_diff": (da["mean"] - db_["mean"]) if (da["n"] and db_["n"]) else None}
    if da["n"] < 2 or db_["n"] < 2:
        out["note"] = "need >=2 measurements per group for a test"
        return out

    va, vb = da["sd"] ** 2, db_["sd"] ** 2
    na, nb = da["n"], db_["n"]
    se = math.sqrt(va / na + vb / nb)
    t = (da["mean"] - db_["mean"]) / se if se > 0 else 0.0
    out["welch_t"] = t

    try:
        from scipy import stats as sstats  # type: ignore
        tt = sstats.ttest_ind(a, b, equal_var=False)
        out["p_value"] = float(tt.pvalue)
        out["test"] = "welch_t (scipy)"
        try:
            mw = sstats.mannwhitneyu(a, b, alternative="two-sided")
            out["mannwhitney_p"] = float(mw.pvalue)
        except ValueError:
            pass
    except Exception:
        out["p_value"] = _normal_p_two_sided(t)
        out["test"] = "welch_t (normal approx)"
    return out


# ------------------------------------------------------------------ measurements
def _latest_success_run(db: Session, dataset_id: int) -> Optional[Run]:
    return db.scalar(
        select(Run).where(Run.dataset_id == dataset_id, Run.status == "succeeded")
        .order_by(Run.created_at.desc()))


def dataset_measurement(db: Session, ds: Dataset) -> dict:
    run = _latest_success_run(db, ds.id)
    return {
        "dataset_id": ds.id, "dataset": ds.name,
        "roi_mm3": run.roi_mm3 if run else None,
        "roi_um3": run.roi_um3 if run else None,
        "run_id": run.id if run else None,
        "model": (run.model_snapshot or {}).get("name") if run else None,
    }


def experiment_stats(db: Session, experiment_id: int) -> dict:
    """Descriptive stats per set + a pairwise comparison of the first two sets."""
    exp = db.get(Experiment, experiment_id)
    if not exp:
        raise ValueError("experiment not found")
    groups = []
    for s in exp.sets:
        measures = [dataset_measurement(db, d) for d in s.datasets]
        values = [m["roi_mm3"] for m in measures if m["roi_mm3"] is not None]
        groups.append({"set_id": s.id, "set": s.name,
                       "measurements": measures, "stats": describe(values),
                       "values": values})
    result = {"experiment_id": exp.id, "experiment": exp.name, "type": exp.type,
              "metric": "roi_mm3", "groups": groups}
    if len(groups) >= 2:
        result["comparison"] = {
            "a": groups[0]["set"], "b": groups[1]["set"],
            **compare_two(groups[0]["values"], groups[1]["values"]),
        }
    return result


def compare_sets(db: Session, set_id_a: int, set_id_b: int) -> dict:
    """Explicit two-set comparison (e.g. R13 treated vs CTL)."""
    sa, sb = db.get(DatasetSet, set_id_a), db.get(DatasetSet, set_id_b)
    if not sa or not sb:
        raise ValueError("set not found")
    ma = [dataset_measurement(db, d) for d in sa.datasets]
    mb = [dataset_measurement(db, d) for d in sb.datasets]
    va = [m["roi_mm3"] for m in ma if m["roi_mm3"] is not None]
    vb = [m["roi_mm3"] for m in mb if m["roi_mm3"] is not None]
    return {"metric": "roi_mm3",
            "a": {"set_id": sa.id, "set": sa.name, "measurements": ma},
            "b": {"set_id": sb.id, "set": sb.name, "measurements": mb},
            "comparison": compare_two(va, vb)}


# ------------------------------------------------------------------------ export
def _add_file(zf: zipfile.ZipFile, path: Optional[str], arcname: str) -> bool:
    if path and os.path.isfile(path):
        try:
            zf.write(path, arcname)
            return True
        except OSError:
            return False
    return False


def build_experiment_export(db: Session, experiment_id: int,
                            include_masks: bool = False) -> Path:
    """Bundle an experiment: manifest.json + per-run previews/results/logs (and
    optionally full masks) + a stats.json. Returns the path to the written zip."""
    exp = db.get(Experiment, experiment_id)
    if not exp:
        raise ValueError("experiment not found")

    exports = Path(settings.results_root) / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    zip_path = exports / f"experiment_{exp.id}_{stamp}.zip"

    manifest = {"experiment": {"id": exp.id, "name": exp.name, "type": exp.type,
                               "project_id": exp.project_id},
                "generated_at": datetime.utcnow().isoformat(), "sets": []}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in exp.sets:
            s_entry = {"id": s.id, "name": s.name, "datasets": []}
            for d in s.datasets:
                d_entry = {"id": d.id, "name": d.name, "type": d.type,
                           "voxel_size_um": d.voxel_size_um, "runs": []}
                for run in db.scalars(select(Run).where(
                        Run.dataset_id == d.id, Run.status == "succeeded")).all():
                    prefix = f"sets/{s.name}/{d.name}/run{run.id}"
                    _add_file(zf, run.preview_png, f"{prefix}/preview.png")
                    _add_file(zf, run.log_path, f"{prefix}/run.log")
                    if run.output_dir:
                        for rj in Path(run.output_dir).glob("*_result.json"):
                            _add_file(zf, str(rj), f"{prefix}/{rj.name}")
                    if include_masks:
                        _add_file(zf, run.mask_nii, f"{prefix}/mask.nii.gz")
                    d_entry["runs"].append({"id": run.id, "roi_mm3": run.roi_mm3,
                                            "model": (run.model_snapshot or {}).get("name")})
                s_entry["datasets"].append(d_entry)
            manifest["sets"].append(s_entry)

        # Analyses attached to this experiment: index their figure files.
        analyses = db.scalars(select(Analysis).where(
            Analysis.experiment_id == exp.id)).all()
        manifest["analyses"] = [{"id": a.id, "title": a.title, "type": a.type,
                                 "files_relpath": a.files_relpath} for a in analyses]

        try:
            stats = experiment_stats(db, exp.id)
        except ValueError:
            stats = {}
        zf.writestr("stats.json", json.dumps(stats, indent=2, default=str))
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

    return zip_path
