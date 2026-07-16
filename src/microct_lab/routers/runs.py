"""Run endpoints: enqueue, list/filter, detail, review, cancel, and file serving for NiiVue."""
from __future__ import annotations

import math
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Run
from ..registry import enqueue_runs
from ..schemas import RunCreate, RunOut, RunReview

router = APIRouter(prefix="/api/runs", tags=["runs"])

# Curated-but-extensible failure-mode vocabulary for microCT segmentation QC.
FAILURE_MODES = [
    {"key": "false_positive_outside", "label": "False positive outside specimen"},
    {"key": "boundary_leak", "label": "Boundary leak / bleed"},
    {"key": "under_segmentation", "label": "Under-segmentation (missed part)"},
    {"key": "over_segmentation", "label": "Over-segmentation (extra tissue)"},
    {"key": "missed_roi", "label": "ROI entirely missed"},
    {"key": "fragmented", "label": "Fragmented / split ROI"},
    {"key": "artifact_confusion", "label": "Ring/beam-hardening artifact segmented"},
    {"key": "holder_mounting", "label": "Sample holder / mounting segmented"},
    {"key": "low_contrast", "label": "Low-contrast region"},
    {"key": "noisy_recon", "label": "Noisy reconstruction"},
    {"key": "wrong_resolution", "label": "Resolution / spacing mismatch"},
]


def _out(r: Run) -> RunOut:
    o = RunOut.model_validate(r)
    o.dataset_name = r.dataset.name if r.dataset else None
    o.model_name = r.model.name if r.model else None
    return o


@router.get("", response_model=list[RunOut])
def list_runs(status: Optional[str] = None, dataset_id: Optional[int] = None,
              model_id: Optional[int] = None, qc_status: Optional[str] = None,
              qc_tag: Optional[str] = None, flagged: Optional[bool] = None,
              db: Session = Depends(get_db)):
    stmt = select(Run)
    if status:
        stmt = stmt.where(Run.status == status)
    if dataset_id:
        stmt = stmt.where(Run.dataset_id == dataset_id)
    if model_id:
        stmt = stmt.where(Run.model_id == model_id)
    if qc_status:
        stmt = stmt.where(Run.qc_status == qc_status)
    if flagged is not None:
        stmt = stmt.where(Run.flagged == flagged)
    stmt = stmt.order_by(Run.created_at.desc())
    rows = db.scalars(stmt).all()
    if qc_tag:  # JSON list membership — filter in Python (small volumes)
        rows = [r for r in rows if qc_tag in (r.qc_tags or [])]
    return [_out(r) for r in rows]


@router.get("/vocab")
def vocab():
    """Failure-mode vocabulary for the QC UI."""
    return {"failure_modes": FAILURE_MODES}


@router.get("/facets")
def run_facets(db: Session = Depends(get_db)):
    """Aggregate failure modes and QC status across all runs (for the insights view)."""
    from collections import Counter
    tag_counts: Counter = Counter()
    status_counts: Counter = Counter()
    flagged = 0
    for r in db.scalars(select(Run)).all():
        for t in (r.qc_tags or []):
            tag_counts[t] += 1
        status_counts[r.qc_status or "unreviewed"] += 1
        if r.flagged:
            flagged += 1
    label = {m["key"]: m["label"] for m in FAILURE_MODES}
    modes = [{"key": k, "label": label.get(k, k), "count": c}
             for k, c in tag_counts.most_common()]
    return {"failure_modes": modes, "qc_status": dict(status_counts), "flagged": flagged}


@router.post("", response_model=list[RunOut])
def create_runs(body: RunCreate, db: Session = Depends(get_db)):
    try:
        runs = enqueue_runs(db, body.dataset_ids, body.model_id, folds=body.folds,
                            tta=body.tta, step=body.step, device=body.device,
                            spacing_mm=body.spacing_mm)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return [_out(r) for r in runs]


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return _out(r)


@router.post("/{run_id}/review", response_model=RunOut)
def review_run(run_id: int, body: RunReview, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if body.qc_status is not None:
        r.qc_status = body.qc_status
    if body.qc_tags is not None:
        r.qc_tags = body.qc_tags
    if body.rating is not None:
        r.rating = body.rating
    if body.flagged is not None:
        r.flagged = body.flagged
    if body.review_note is not None:
        r.review_note = body.review_note
    db.commit()
    db.refresh(r)
    return _out(r)


@router.post("/{run_id}/cancel", response_model=RunOut)
def cancel_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r.status != "queued":
        raise HTTPException(409, f"can only cancel queued runs (this one is {r.status})")
    r.status = "canceled"
    db.commit()
    db.refresh(r)
    return _out(r)


@router.delete("/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r.status == "running":
        raise HTTPException(409, "run is in progress")
    db.delete(r)
    db.commit()
    return {"deleted": run_id}


# ---- files for the viewer (explicit extensions so NiiVue detects the format) ----
def _file(run: Run | None, path: Optional[str], media: str):
    if not run or not path or not os.path.exists(path):
        raise HTTPException(404, "file not available")
    return FileResponse(path, media_type=media, filename=os.path.basename(path))


@router.get("/{run_id}/input.nii.gz")
def run_input(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    return _file(r, r.input_nii if r else None, "application/gzip")


@router.get("/{run_id}/mask.nii.gz")
def run_mask(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    return _file(r, r.mask_nii if r else None, "application/gzip")


@router.get("/{run_id}/preview.png")
def run_preview(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    return _file(r, r.preview_png if r else None, "image/png")


def _downsampled(run: Run, src: Optional[str], kind: str, target: int = 512):
    """Serve a downsampled NIfTI for the in-browser viewer (big microCT volumes blow
    the browser's WebGL/ArrayBuffer limits). Cached next to the run outputs. The
    full-resolution mask/input remain available via the plain endpoints."""
    if not src or not os.path.exists(src):
        raise HTTPException(404, "file not available")
    base = run.output_dir or os.path.dirname(src)
    out = os.path.join(base, f"view_{kind}.nii.gz")
    if not os.path.exists(out):
        import SimpleITK as sitk
        img = sitk.ReadImage(src)
        f = max(1, math.ceil(max(img.GetSize()) / target))
        if f > 1:
            img = sitk.Shrink(img, [f, f, f])
        sitk.WriteImage(img, out, useCompression=True)
    return FileResponse(out, media_type="application/gzip", filename=os.path.basename(out))


@router.get("/{run_id}/view_input.nii.gz")
def run_view_input(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return _downsampled(r, r.input_nii, "input")


@router.get("/{run_id}/view_mask.nii.gz")
def run_view_mask(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return _downsampled(r, r.mask_nii, "mask")


@router.get("/{run_id}/log.txt", response_class=PlainTextResponse)
def run_log(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r or not r.log_path or not os.path.exists(r.log_path):
        return PlainTextResponse("(no log yet)")
    with open(r.log_path, encoding="utf-8", errors="replace") as f:
        return PlainTextResponse(f.read())
