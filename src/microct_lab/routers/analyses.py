"""Analyses: index R code + figures living in the shared Analyses folder."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Analysis, Dataset, DatasetSet, Experiment, Measurement
from ..registry import mouse_key
from ..schemas import AnalysisIn, AnalysisOut, AnalysisPatch, NormalizeBvtvRequest

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".tif", ".tiff", ".pdf"}
CODE_EXTS = {".r", ".rmd", ".py", ".ipynb", ".sh", ".txt", ".md", ".csv", ".tsv", ".json"}


# ------------------------------------------------------- BV/TV normalization
def _bvtv_for(db: Session, dataset_ids: list[int]) -> dict[int, dict]:
    """Latest usable BV/TV per dataset: prefers the interim threshold
    measurement (metrics.bv_tv), falls back to digitpipe's BV_TV_primary."""
    out: dict[int, dict] = {}
    if not dataset_ids:
        return out
    rows = db.scalars(
        select(Measurement)
        .where(Measurement.dataset_id.in_(dataset_ids),
               Measurement.status == "succeeded",
               Measurement.archived == False)  # noqa: E712
        .order_by(Measurement.created_at.desc())).all()
    for m in rows:
        if m.dataset_id in out:
            continue
        metrics = m.metrics or {}
        v = metrics.get("bv_tv")
        source = "bvtv_thresh"
        if not isinstance(v, (int, float)):
            v = metrics.get("bv_tv_primary")
            source = "digitpipe"
        if isinstance(v, (int, float)):
            out[m.dataset_id] = {"value": float(v), "source": source,
                                 "measurement_id": m.id}
    return out


def _mean(vals: list[float]):
    return sum(vals) / len(vals) if vals else None


def _sd(vals: list[float]):
    if len(vals) < 2:
        return None
    mu = _mean(vals)
    return (sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


@router.post("/normalize-bvtv")
def normalize_bvtv(body: NormalizeBvtvRequest, db: Session = Depends(get_db)):
    """Normalized BV/TV over the digit datasets of a set or experiment.

    Each dataset's BV/TV is divided by an unamputated-reference value chosen by
    `mode` (per_leg / per_mouse / per_set — see the schema). The ticked
    reference set is persisted onto the datasets' `unamputated` flag. With
    save=true, the full result table is stored as an Analysis record so it
    shows up in the project tree.
    """
    if body.mode not in ("per_leg", "per_mouse", "per_set"):
        raise HTTPException(400, "mode must be per_leg | per_mouse | per_set")

    # ---- scope ----
    if body.experiment_id:
        if not db.get(Experiment, body.experiment_id):
            raise HTTPException(404, "experiment not found")
        scope = db.scalars(select(Dataset).where(
            Dataset.experiment_id == body.experiment_id)).all()
        set_ids = sorted({d.set_id for d in scope if d.set_id})
    elif body.set_ids:
        for sid in body.set_ids:
            if not db.get(DatasetSet, sid):
                raise HTTPException(404, f"set {sid} not found")
        scope = db.scalars(select(Dataset).where(
            Dataset.set_id.in_(body.set_ids))).all()
        set_ids = list(body.set_ids)
    else:
        raise HTTPException(400, "give set_ids or experiment_id")

    # Digit datasets only — this normalization is defined for digit uCT.
    digits = [d for d in scope if (d.type or "uct") == "uct"
              and (d.subtype == "digit" or d.digit_id)]
    if not digits:
        raise HTTPException(400, "no digit uct datasets in scope (set subtype "
                                 "'digit' or a digit ID on the datasets first)")

    # ---- persist the reference choice ----
    ref_ids = set(body.reference_dataset_ids)
    for d in digits:
        want = d.id in ref_ids
        if d.unamputated != want:
            d.unamputated = want
    db.commit()

    values = _bvtv_for(db, [d.id for d in digits])
    refs = [d for d in digits if d.id in ref_ids and d.id in values]

    def side(d: Dataset):
        return d.digit_id[0] if d.digit_id else None

    # Pre-group the references by the keys the modes use.
    by_leg: dict[tuple, list[float]] = {}
    by_mouse: dict[str, list[float]] = {}
    by_set: dict[int | None, list[float]] = {}
    all_ref_vals: list[float] = []
    for r in refs:
        v = values[r.id]["value"]
        all_ref_vals.append(v)
        by_mouse.setdefault(mouse_key(r.name), []).append(v)
        by_set.setdefault(r.set_id, []).append(v)
        if side(r):
            by_leg.setdefault((mouse_key(r.name), side(r)), []).append(v)

    rows = []
    for d in sorted(digits, key=lambda x: x.name.lower()):
        got = values.get(d.id)
        row = {"dataset_id": d.id, "name": d.name, "set_id": d.set_id,
               "digit_id": d.digit_id, "mouse": mouse_key(d.name),
               "is_reference": d.id in ref_ids,
               "bvtv": got["value"] if got else None,
               "source": got["source"] if got else None,
               "measurement_id": got["measurement_id"] if got else None,
               "reference": None, "normalized": None, "note": None}
        if not got:
            row["note"] = "no BV/TV measurement — run BV/TV (threshold) first"
            rows.append(row)
            continue
        if body.mode == "per_leg":
            ref, why = _mean(by_leg.get((row["mouse"], side(d)), [])), \
                f"mean of {row['mouse']} {side(d) or '?'} references"
        elif body.mode == "per_mouse":
            ref, why = _mean(by_mouse.get(row["mouse"], [])), \
                f"mean of {row['mouse']} references (L+R)"
        else:
            vals = by_set.get(d.set_id) or all_ref_vals
            ref, why = _mean(vals), ("mean of set references" if by_set.get(d.set_id)
                                     else "mean of all references in scope")
        if ref:
            row["reference"] = round(ref, 6)
            row["normalized"] = round(got["value"] / ref, 6)
            row["reference_desc"] = why
        else:
            row["note"] = "no matching unamputated reference with a BV/TV value"
        rows.append(row)

    # Per-set summary over the non-reference, normalized rows.
    set_names = {s.id: s.name for s in db.scalars(
        select(DatasetSet).where(DatasetSet.id.in_(set_ids))).all()} if set_ids else {}
    groups = []
    for sid in set_ids or [None]:
        vals = [r["normalized"] for r in rows
                if r["set_id"] == sid and r["normalized"] is not None
                and not r["is_reference"]]
        groups.append({"set_id": sid, "set_name": set_names.get(sid),
                       "n": len(vals), "mean": round(_mean(vals), 6) if vals else None,
                       "sd": round(_sd(vals), 6) if _sd(vals) is not None else None})

    result = {"mode": body.mode, "rows": rows, "groups": groups,
              "reference_dataset_ids": sorted(ref_ids),
              "n_datasets": len(digits), "n_with_value": len(values),
              "n_references": len(refs)}

    if body.save:
        exp_id = body.experiment_id
        if exp_id is None and set_ids:
            s = db.get(DatasetSet, set_ids[0])
            exp_id = s.experiment_id if s else None
        proj_id = None
        if exp_id:
            e = db.get(Experiment, exp_id)
            proj_id = e.project_id if e else None
        a = Analysis(
            title=body.title or f"Normalized BV/TV ({body.mode.replace('_', ' ')})",
            type="bvtv_normalization", project_id=proj_id, experiment_id=exp_id,
            dataset_ids=[d.id for d in digits], set_ids=set_ids,
            data=result, tags=["bvtv", "normalization"])
        db.add(a)
        db.commit()
        db.refresh(a)
        result["analysis_id"] = a.id

    return result


def _folder(a: Analysis) -> Path:
    """Absolute folder for this analysis on THIS machine.

    `files_relpath` is user-supplied, so the resolved folder is confined to the
    Analyses root — an absolute or '..'-laden value that escapes the root is
    rejected (prevents arbitrary-directory listing / file read)."""
    base = settings.analyses_dir.resolve()
    rel = a.files_relpath or f"analysis_{a.id}"
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "analysis folder escapes the Analyses root")
    return target


def _safe_join(base: Path, name: str) -> Path:
    """Join and confirm the result stays inside base (no path traversal)."""
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(400, "invalid path")
    return target


@router.get("", response_model=list[AnalysisOut])
def list_analyses(project_id: int | None = None, experiment_id: int | None = None,
                  db: Session = Depends(get_db)):
    stmt = select(Analysis).order_by(Analysis.created_at.desc())
    if project_id:
        stmt = stmt.where(Analysis.project_id == project_id)
    if experiment_id:
        stmt = stmt.where(Analysis.experiment_id == experiment_id)
    return db.scalars(stmt).all()


@router.post("", response_model=AnalysisOut)
def create_analysis(body: AnalysisIn, db: Session = Depends(get_db)):
    a = Analysis(
        title=body.title, project_id=body.project_id, experiment_id=body.experiment_id,
        type=body.type, description=body.description, files_relpath=body.files_relpath,
        dataset_ids=body.dataset_ids or [], set_ids=body.set_ids or [],
        run_ids=body.run_ids or [], tags=body.tags or [],
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.get("/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    return a


@router.patch("/{analysis_id}", response_model=AnalysisOut)
def patch_analysis(analysis_id: int, body: AnalysisPatch, db: Session = Depends(get_db)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    for field in ("title", "project_id", "experiment_id", "type", "description",
                  "files_relpath", "dataset_ids", "set_ids", "run_ids", "tags"):
        val = getattr(body, field)
        if val is not None:
            setattr(a, field, val)
    db.commit()
    db.refresh(a)
    return a


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: int, db: Session = Depends(get_db)):
    """Delete the analysis *record* only. The files on the NAS are never touched."""
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    db.delete(a)
    db.commit()
    return {"deleted": analysis_id}


@router.get("/{analysis_id}/files")
def analysis_files(analysis_id: int, db: Session = Depends(get_db)):
    """List files in this analysis's folder, split into figures vs code/data."""
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    folder = _folder(a)
    if not folder.is_dir():
        return {"folder": str(folder), "exists": False, "figures": [], "files": []}
    figures, files = [], []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        ext = p.suffix.lower()
        entry = {"name": rel, "size": p.stat().st_size, "ext": ext}
        if ext in IMAGE_EXTS:
            entry["url"] = f"/api/analyses/{analysis_id}/file?name={quote(rel)}"
            figures.append(entry)
        else:
            files.append(entry)
    return {"folder": str(folder), "exists": True, "figures": figures, "files": files}


@router.get("/{analysis_id}/file")
def analysis_file(analysis_id: int, name: str, db: Session = Depends(get_db)):
    """Serve a single file (image inline, others as download) from the analysis folder."""
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    folder = _folder(a)
    target = _safe_join(folder, name)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    media = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=media, filename=os.path.basename(name))
