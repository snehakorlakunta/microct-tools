"""Measurement endpoints: enqueue morphometry, list/filter, detail, cancel, stats,
and file serving (7-class annotated volume for NiiVue + the pipeline spreadsheet)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Measurement, Run
from ..registry import _safe, force_cancel, job_is_stuck, spacing_um_for
from ..schemas import MeasurementCreate, MeasurementOut, MeasurementPatch
from ..stats import describe

router = APIRouter(prefix="/api/measurements", tags=["measurements"])

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Numeric metric columns aggregated by GET /stats. Everything else the pipeline
# emits stays in the JSON `metrics` blob; these are the promoted headline columns.
METRIC_COLUMNS = [
    "socket_volume_voxels", "socket_volume_mm3",
    "socket_radius_voxels", "socket_radius_mm",
    "phalanx_volume_voxels", "phalanx_volume_mm3",
    "bone_length_voxels", "bone_length_mm",
    "euclidean_distance_voxels", "euclidean_distance_mm",
]


def _out(m: Measurement) -> MeasurementOut:
    o = MeasurementOut.model_validate(m)
    run = m.run
    o.run_status = run.status if run else None
    ds = run.dataset if run else None
    o.dataset_name = ds.name if ds else None
    # Derived per request from the clock, never stored — see registry.job_is_stuck.
    o.stuck = job_is_stuck(m)
    return o


def _anatomy_block_reason(run: Run) -> Optional[str]:
    """Why morphometry is refused for this run, or None if it is allowed.

    The vendored digitpipe_v5 pipeline is built for mouse terminal phalanx at
    ~4um. On any other anatomy it does not fail — it runs all eight stages, exits
    cleanly, and emits a full set of confident, plausible, meaningless numbers
    (morphqc.py catches some of those after the fact, but only as a warning on a
    result that already exists). So the dataset must be marked as the right
    anatomy before the job is allowed to start.
    """
    if not settings.morph_require_anatomy:
        return None
    wanted = settings.morph_anatomy_tag_list
    if not wanted:  # gate on, but nothing configured to match -> nothing to enforce
        return None
    ds = run.dataset
    if ds is None:
        return (f"Run {run.id} has no dataset record, so its anatomy cannot be "
                f"confirmed and morphometry is blocked. Set "
                f"MICROCT_MORPH_REQUIRE_ANATOMY=false to disable this check.")
    have = {str(t).strip().lower() for t in (ds.tags or [])}
    if have & set(wanted):
        return None
    quoted = " or ".join(repr(t) for t in wanted)
    label = wanted[0] if len(wanted) == 1 else " / ".join(wanted)
    return (f"Dataset {ds.name!r} is not marked as {label} anatomy, so morphometry "
            f"is blocked. This pipeline is built for mouse terminal phalanx and "
            f"returns plausible but meaningless numbers on other anatomy. Tag the "
            f"dataset {quoted} to allow it, or set "
            f"MICROCT_MORPH_REQUIRE_ANATOMY=false to disable this check.")


def _case_from_run(run: Run) -> str:
    """Case name a run's outputs are prefixed with (derived from the mask file, so
    it always matches the files actually on disk). Falls back to the dataset name."""
    if run.mask_nii:
        b = os.path.basename(run.mask_nii)
        for ext in (".nii.gz", ".nii"):
            if b.endswith(ext):
                return b[: -len(ext)]
    ds = run.dataset
    return _safe(ds.name) if ds else f"run{run.id}"


@router.get("", response_model=list[MeasurementOut])
def list_measurements(run_id: Optional[int] = None, dataset_id: Optional[int] = None,
                      status: Optional[str] = None, include_archived: bool = False,
                      db: Session = Depends(get_db)):
    stmt = select(Measurement)
    if run_id:
        stmt = stmt.where(Measurement.run_id == run_id)
    if dataset_id:
        stmt = stmt.where(Measurement.dataset_id == dataset_id)
    if status:
        stmt = stmt.where(Measurement.status == status)
    if not include_archived:
        stmt = stmt.where(Measurement.archived == False)  # noqa: E712
    stmt = stmt.order_by(Measurement.created_at.desc())
    return [_out(m) for m in db.scalars(stmt).all()]


# Declared BEFORE /{measurement_id} so "stats" isn't swallowed by the int path param.
@router.get("/stats")
def measurement_stats(dataset_id: Optional[int] = None, db: Session = Depends(get_db)):
    """mean / sd / min / max / n over the succeeded measurements' metric columns."""
    stmt = select(Measurement).where(Measurement.status == "succeeded",
                                     Measurement.archived == False)  # noqa: E712
    if dataset_id:
        stmt = stmt.where(Measurement.dataset_id == dataset_id)
    rows = db.scalars(stmt).all()
    metrics = {col: describe([getattr(m, col) for m in rows]) for col in METRIC_COLUMNS}
    return {"dataset_id": dataset_id, "measurements": len(rows), "metrics": metrics}


@router.post("", response_model=list[MeasurementOut])
def create_measurements(body: MeasurementCreate, db: Session = Depends(get_db)):
    """Enqueue one Measurement per run_id. Every run must have succeeded and still
    have its mask on disk — a measurement over a missing/failed segmentation would
    only fail slowly in the worker, so reject it up front.

    Its dataset must also be tagged as the anatomy this pipeline is built for
    (MICROCT_MORPH_ANATOMY_TAGS, default 'phalanx'), unless
    MICROCT_MORPH_REQUIRE_ANATOMY is false — see _anatomy_block_reason."""
    if not body.run_ids:
        raise HTTPException(400, "run_ids is empty")
    if not _safe(body.pipeline_version) == body.pipeline_version:
        raise HTTPException(400, f"invalid pipeline_version: {body.pipeline_version!r}")

    runs: list[Run] = []
    for rid in body.run_ids:
        run = db.get(Run, rid)
        if run is None:
            raise HTTPException(400, f"run {rid} not found")
        if run.status != "succeeded":
            raise HTTPException(400, f"run {rid} is {run.status} — only succeeded runs "
                                     f"can be measured")
        if not run.mask_nii or not os.path.isfile(run.mask_nii):
            raise HTTPException(400, f"run {rid} has no mask on disk "
                                     f"({run.mask_nii or 'mask_nii unset'})")
        if not run.input_nii or not os.path.isfile(run.input_nii):
            raise HTTPException(400, f"run {rid} has no input volume on disk "
                                     f"({run.input_nii or 'input_nii unset'})")
        blocked = _anatomy_block_reason(run)
        if blocked:
            raise HTTPException(400, blocked)
        runs.append(run)

    made: list[Measurement] = []
    for run in runs:
        ds = run.dataset
        spacing_um = spacing_um_for(run, ds)
        params = {"skip_viz": bool(body.skip_viz), "spacing_um": spacing_um,
                  "mask_nii": run.mask_nii, "input_nii": run.input_nii,
                  "case": _case_from_run(run)}
        m = Measurement(run_id=run.id, dataset_id=run.dataset_id, status="queued",
                        pipeline_version=body.pipeline_version, params=params)
        db.add(m)
        db.flush()  # get m.id
        # Same convention as registry.enqueue_runs: <results_root>/<case>__<what>__<id>
        m.output_dir = str(Path(settings.results_root) /
                           f"{_safe(params['case'])}__morph__m{m.id}")
        made.append(m)
    db.commit()
    for m in made:
        db.refresh(m)
    return [_out(m) for m in made]


@router.get("/{measurement_id}", response_model=MeasurementOut)
def get_measurement(measurement_id: int, db: Session = Depends(get_db)):
    m = db.get(Measurement, measurement_id)
    if not m:
        raise HTTPException(404, "measurement not found")
    return _out(m)


@router.patch("/{measurement_id}", response_model=MeasurementOut)
def patch_measurement(measurement_id: int, body: MeasurementPatch,
                      db: Session = Depends(get_db)):
    m = db.get(Measurement, measurement_id)
    if not m:
        raise HTTPException(404, "measurement not found")
    if body.notes is not None:
        m.notes = body.notes
    if body.flagged is not None:
        m.flagged = body.flagged
    if body.archived is not None:
        m.archived = body.archived
    db.commit()
    db.refresh(m)
    return _out(m)


@router.post("/{measurement_id}/cancel", response_model=MeasurementOut)
def cancel_measurement(measurement_id: int, force: bool = False,
                       db: Session = Depends(get_db)):
    """Stop a measurement. Queued ones cancel immediately; a running one is flagged
    'canceling' and the worker kills its pipeline process within a few seconds.

    `?force=true` is the escape hatch for when that never happens — no measurement
    worker was running, or one died between the flag and the kill — and the row is
    stranded in 'canceling' (`stuck` on this measurement says so). It resolves the
    row terminally: status 'canceled', with ended_at/duration_sec stamped exactly
    as the worker would have.

    Forcing corrects the record; it does not stop anything. No signal is sent and
    no process is killed, so a morphometry pipeline that is genuinely still
    running on a worker machine keeps running — and keeps writing into the output
    directory — with no in-flight row left to show for it. The `error` field
    records that. Check the worker host before treating the compute as stopped.
    """
    m = db.get(Measurement, measurement_id)
    if not m:
        raise HTTPException(404, "measurement not found")
    if force:
        if m.status not in ("canceling", "running"):
            raise HTTPException(
                409, f"cannot force-cancel a measurement that is {m.status} — "
                     f"forcing only applies to one stranded in 'canceling' (or one "
                     f"that is 'running'). A queued measurement cancels cleanly "
                     f"with plain POST /api/measurements/{measurement_id}/cancel.")
        force_cancel(db, m, process_noun="morphometry")
        db.refresh(m)
        return _out(m)
    if m.status == "queued":
        m.status = "canceled"
    elif m.status == "running":
        m.status = "canceling"
    elif m.status == "canceling":
        pass  # already stopping — idempotent
    else:
        raise HTTPException(409, f"cannot stop a measurement that is {m.status}")
    db.commit()
    db.refresh(m)
    return _out(m)


@router.post("/{measurement_id}/archive", response_model=MeasurementOut)
def archive_measurement(measurement_id: int, db: Session = Depends(get_db)):
    """Archive a terminal measurement — hidden from default views, never deleted.
    In-flight measurements must be stopped first."""
    m = db.get(Measurement, measurement_id)
    if not m:
        raise HTTPException(404, "measurement not found")
    if m.status in ("running", "queued", "canceling"):
        raise HTTPException(409, "measurement is in progress — stop it first")
    m.archived = True
    db.commit()
    db.refresh(m)
    return _out(m)


@router.get("/{measurement_id}/log.txt", response_class=PlainTextResponse)
def measurement_log(measurement_id: int, db: Session = Depends(get_db)):
    m = db.get(Measurement, measurement_id)
    if not m or not m.log_path or not os.path.exists(m.log_path):
        return PlainTextResponse("(no log yet)")
    with open(m.log_path, encoding="utf-8", errors="replace") as f:
        return PlainTextResponse(f.read())


@router.get("/{measurement_id}/metrics.json")
def measurement_metrics(measurement_id: int, db: Session = Depends(get_db)):
    m = db.get(Measurement, measurement_id)
    if not m:
        raise HTTPException(404, "measurement not found")
    if not m.metrics:
        raise HTTPException(404, "no metrics available for this measurement")
    return m.metrics


# ---- files for the viewer ----------------------------------------------------
def _serve(m: Optional[Measurement], path: Optional[str], media: str):
    """Serve a pipeline output file.

    Path-traversal guard: the file must resolve INSIDE this measurement's own
    output_dir. The stored paths are written by the worker, not by a client, but
    the containment check means a tampered/stale DB row still can't turn these
    endpoints into an arbitrary-file reader.
    """
    if not m or not path:
        raise HTTPException(404, "file not available")
    try:
        target = Path(path).resolve(strict=True)
    except OSError:
        raise HTTPException(404, "file not available")
    if not target.is_file():
        raise HTTPException(404, "file not available")
    base_raw = m.output_dir or (Path(settings.results_root))
    try:
        base = Path(base_raw).resolve()
        target.relative_to(base)
    except (OSError, ValueError):
        raise HTTPException(404, "file not available")
    return FileResponse(str(target), media_type=media, filename=target.name)


@router.get("/{measurement_id}/annotated.nii.gz")
def measurement_annotated(measurement_id: int, db: Session = Depends(get_db)):
    """The 7-class annotated label volume (1 bone, 2 socket, 3 line outside bone,
    4 line inside bone, 5 furthest point, 6 socket COM, 7 first intersection)."""
    m = db.get(Measurement, measurement_id)
    return _serve(m, m.annotated_nii if m else None, "application/gzip")


@router.get("/{measurement_id}/export.xlsx")
def measurement_xlsx(measurement_id: int, db: Session = Depends(get_db)):
    m = db.get(Measurement, measurement_id)
    return _serve(m, m.xlsx_path if m else None, XLSX_MEDIA)
