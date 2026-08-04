"""Run endpoints: enqueue, list/filter, detail, review, cancel, and file serving for NiiVue."""
from __future__ import annotations

import math
import os
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Run
from ..registry import enqueue_runs, force_cancel, job_is_stuck
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
    # Derived per request from the clock, never stored — see registry.job_is_stuck.
    o.stuck = job_is_stuck(r)
    return o


@router.get("", response_model=list[RunOut])
def list_runs(status: Optional[str] = None, dataset_id: Optional[int] = None,
              model_id: Optional[int] = None, qc_status: Optional[str] = None,
              qc_tag: Optional[str] = None, flagged: Optional[bool] = None,
              include_archived: bool = False, archived: Optional[bool] = None,
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
    if archived is not None:
        stmt = stmt.where(Run.archived == archived)
    elif not include_archived:
        stmt = stmt.where(Run.archived == False)  # noqa: E712
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
def cancel_run(run_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Stop a run. Queued runs cancel immediately; a running run is flagged
    'canceling' and the worker kills its segmentation process within a few seconds.

    `?force=true` is the escape hatch for when that never happens — no worker was
    running, or one died between the flag and the kill — and the row is stranded
    in 'canceling' (`stuck` on this run says so). It resolves the row terminally:
    status 'canceled', with ended_at/duration_sec stamped exactly as the worker
    would have.

    Be clear about what forcing is: **a correction to the record, not a stop
    button.** It signals nothing and kills nothing. If a segmentation process for
    this run really is still alive on a worker machine, it stays alive after this
    call — still on the GPU, still writing into the output directory — and now
    without a running row to show for it. The `error` field records that. Check
    the worker host before treating the compute as stopped.
    """
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if force:
        if r.status not in ("canceling", "running"):
            raise HTTPException(
                409, f"cannot force-cancel a run that is {r.status} — forcing only "
                     f"applies to a run stranded in 'canceling' (or one that is "
                     f"'running'). A queued run cancels cleanly with plain "
                     f"POST /api/runs/{run_id}/cancel.")
        force_cancel(db, r, process_noun="segmentation")
        db.refresh(r)
        return _out(r)
    if r.status == "queued":
        r.status = "canceled"
    elif r.status == "running":
        r.status = "canceling"
    elif r.status == "canceling":
        pass  # already stopping — idempotent
    else:
        raise HTTPException(409, f"cannot stop a run that is {r.status}")
    db.commit()
    db.refresh(r)
    return _out(r)


@router.post("/{run_id}/archive", response_model=RunOut)
def archive_run(run_id: int, db: Session = Depends(get_db)):
    """Archive a terminal run — it disappears from default views but is never
    deleted (runs are the immutable provenance record). In-flight runs must be
    stopped first."""
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r.status in ("running", "queued", "canceling"):
        raise HTTPException(409, "run is in progress — stop it first")
    r.archived = True
    db.commit()
    db.refresh(r)
    return _out(r)


@router.post("/{run_id}/unarchive", response_model=RunOut)
def unarchive_run(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    r.archived = False
    db.commit()
    db.refresh(r)
    return _out(r)


@router.delete("/{run_id}")
def delete_run(run_id: int):
    """Runs are immutable provenance records and cannot be deleted — only
    archived. Kept as an explicit 405 so old clients get a clear message."""
    raise HTTPException(405, "runs cannot be deleted — archive it instead "
                             "(POST /api/runs/{id}/archive)")


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


def _case_from(run: Run) -> str:
    """Case name a run's outputs are prefixed with (derived from the mask file)."""
    if run.mask_nii:
        b = os.path.basename(run.mask_nii)
        for ext in (".nii.gz", ".nii"):
            if b.endswith(ext):
                return b[: -len(ext)]
    return "case"


def _bmp_dir(run: Run) -> str:
    base = run.output_dir or (os.path.dirname(run.mask_nii) if run.mask_nii else "")
    return os.path.join(base, f"{_case_from(run)}_mask_bmp")


def _bmp_summary(out_dir: str) -> Optional[dict]:
    if not out_dir or not os.path.isdir(out_dir):
        return None
    files = [f for f in os.listdir(out_dir) if f.lower().endswith(".bmp")]
    if not files:
        return None
    total = 0
    for f in files:
        try:
            total += os.path.getsize(os.path.join(out_dir, f))
        except OSError:
            pass
    return {"count": len(files), "bytes": total, "dir": out_dir}


@router.get("/{run_id}/bmp_status")
def bmp_status(run_id: int, db: Session = Depends(get_db)):
    """Whether the per-slice mask BMP stack already exists for this run."""
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    out_dir = _bmp_dir(r)
    summary = _bmp_summary(out_dir)
    if summary:
        return {"exists": True, **summary}
    return {"exists": False, "dir": out_dir,
            "can_export": bool(r.mask_nii and os.path.exists(r.mask_nii))}


@router.post("/{run_id}/export_bmp")
def export_bmp(run_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Write (or reuse) the per-slice mask BMP stack for an existing run.

    New runs generate this automatically; this endpoint back-fills older runs
    (like the seeded R2 result) and lets you regenerate with ?force=true.
    """
    from ..bmp_export import export_mask_bmp

    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if not r.mask_nii or not os.path.exists(r.mask_nii):
        raise HTTPException(404, "no mask available for this run")
    out_dir = _bmp_dir(r)
    if not force:
        summary = _bmp_summary(out_dir)
        if summary:
            return {"cached": True, **summary}
    ds = r.dataset
    info = export_mask_bmp(
        r.mask_nii, out_dir,
        ds.slices_path if ds else None,
        (ds.pattern if ds and ds.pattern else "*rec*.bmp"),
    )
    info["cached"] = False
    return info


# ---- live progress for an in-flight run -------------------------------------
# Unit of measure: nnU-Net segments a volume as a grid of overlapping
# sliding-window *patches*; the number of patches completed / total is the most
# faithful measure of compute done. We fold that into an overall percentage
# across the three phases the pipeline logs: converting the slice stack to a
# volume (~2-15%), sliding-window inference (~15-90%), and finalizing / writing
# the mask + preview + BMPs (~90-100%). When the patch count can't be parsed we
# still report the phase and drive an indeterminate (animated) bar.
def _parse_progress(text: str):
    """Return (phase, percent|None, determinate, detail) from run-log text."""
    if not text:
        return ("starting", 2, True, "")

    if "\nDONE" in text or text.rstrip().endswith("DONE"):
        return ("finalizing", 99, True, "")
    if "[bmp]" in text or "[result]" in text:
        return ("finalizing", 96, True, "writing mask / preview / BMPs")
    if "prediction done" in text:
        return ("finalizing", 92, True, "")

    if "predicting ===" in text or "model loaded" in text:
        seg = text.split("predicting ===", 1)[-1]
        cur_tot = None
        for m in re.finditer(r"\b(\d+)\s*/\s*(\d+)\b", seg):
            cur_tot = (int(m.group(1)), int(m.group(2)))
        pct = None
        for m in re.finditer(r"(\d{1,3})%\|", seg):
            pct = int(m.group(1))
        frac, detail = None, "sliding-window inference"
        if cur_tot and cur_tot[1] > 0:
            frac = cur_tot[0] / cur_tot[1]
            detail = f"patch {cur_tot[0]}/{cur_tot[1]}"
        elif pct is not None:
            frac = pct / 100.0
            detail = f"{pct}%"
        if frac is not None:
            return ("predicting", int(round(15 + 75 * min(max(frac, 0.0), 1.0))), True, detail)
        return ("predicting", None, False, detail)

    if "[convert] wrote" in text:
        return ("loading", 15, True, "loading model weights")

    conv_cur = None
    for m in re.finditer(r"\[convert\]\s+(\d+)/(\d+)", text):
        conv_cur = (int(m.group(1)), int(m.group(2)))
    if conv_cur and conv_cur[1] > 0:
        return ("converting", int(round(2 + 13 * conv_cur[0] / conv_cur[1])),
                True, f"slice {conv_cur[0]}/{conv_cur[1]}")
    tot = re.search(r"\[convert\]\s+(\d+)\s+slices", text)
    if tot:
        return ("converting", 3, True, f"0/{tot.group(1)} slices")
    return ("starting", 2, True, "")


@router.get("/{run_id}/progress")
def run_progress(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    if r.status in ("succeeded", "failed", "canceled"):
        return {"status": r.status, "phase": r.status,
                "percent": 100 if r.status == "succeeded" else None,
                "determinate": True, "detail": r.error or "",
                "elapsed_sec": r.duration_sec, "eta_sec": None}
    if r.status == "queued":
        return {"status": "queued", "phase": "queued", "percent": 0,
                "determinate": True, "detail": "waiting for a free worker",
                "elapsed_sec": 0, "eta_sec": None}
    if r.status == "canceling":
        return {"status": "canceling", "phase": "canceling", "percent": None,
                "determinate": False, "detail": "stopping the segmentation process…",
                "elapsed_sec": None, "eta_sec": None}

    text = ""
    if r.log_path and os.path.exists(r.log_path):
        try:
            with open(r.log_path, encoding="utf-8", errors="replace") as f:
                text = f.read()[-20000:]
        except OSError:
            pass
    phase, pct, det, detail = _parse_progress(text)
    elapsed = None
    if r.started_at:
        try:
            elapsed = max(0.0, (datetime.utcnow() - r.started_at).total_seconds())
        except (TypeError, ValueError):
            elapsed = None
    eta = None
    if det and pct and pct > 5 and elapsed:
        eta = max(0.0, elapsed * (100 - pct) / pct)
    return {"status": "running", "phase": phase, "percent": pct, "determinate": det,
            "detail": detail, "elapsed_sec": elapsed, "eta_sec": eta}


@router.get("/{run_id}/log.txt", response_class=PlainTextResponse)
def run_log(run_id: int, db: Session = Depends(get_db)):
    r = db.get(Run, run_id)
    if not r or not r.log_path or not os.path.exists(r.log_path):
        return PlainTextResponse("(no log yet)")
    with open(r.log_path, encoding="utf-8", errors="replace") as f:
        return PlainTextResponse(f.read())
