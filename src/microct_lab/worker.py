"""DB-backed job worker: polls for queued jobs and executes them, one at a time.

Two job kinds share this loop:
  * segmentation — a queued Run, executed via scripts/segment_microct.py (GPU)
  * measurement  — a queued Measurement, executed via scripts/measure_morphometry.py
                   (CPU-only morphometry over a finished segmentation)

Cross-platform (Windows/Linux), no Redis/broker. Run alongside the web server:
    microct-worker                      # both kinds (default)
    microct-worker --kind segmentation  # GPU box
    microct-measure-worker              # CPU box, measurements only
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .config import DEFAULT_SCRIPTS_DIR, settings
from .database import SessionLocal, init_db
from .models import Dataset, Measurement, Model, Run
from .registry import spacing_um_for
from .sysinfo import host_info

# The measurement driver. Read through getattr so that if `measure_script` is ever
# added to Settings it wins, without this module requiring it to exist today.
MEASURE_SCRIPT = Path(getattr(settings, "measure_script",
                              DEFAULT_SCRIPTS_DIR / "measure_morphometry.py"))


def _claim_next(db) -> Run | None:
    run = db.scalar(select(Run).where(Run.status == "queued").order_by(Run.created_at))
    if run is None:
        return None
    run.status = "running"
    run.started_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run


def _claim_next_measurement(db) -> Measurement | None:
    m = db.scalar(select(Measurement).where(Measurement.status == "queued")
                  .order_by(Measurement.created_at))
    if m is None:
        return None
    m.status = "running"
    m.started_at = datetime.utcnow()
    db.commit()
    db.refresh(m)
    return m


def _finish(db, run, status: str, error: str | None = None) -> None:
    """Terminate a job row (Run or Measurement — they share these columns)."""
    run.status = status
    run.error = error
    run.ended_at = datetime.utcnow()
    if run.started_at:
        run.duration_sec = (run.ended_at - run.started_at).total_seconds()
    db.commit()


def _kill_tree(pid: int) -> None:
    """Best-effort kill of the segmentation subprocess and any children it spawned."""
    try:
        import psutil
        parent = psutil.Process(pid)
        for pr in parent.children(recursive=True) + [parent]:
            try:
                pr.kill()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — psutil missing or process already gone
        try:
            import signal
            os.kill(pid, getattr(signal, "SIGTERM", 15))
        except Exception:  # noqa: BLE001
            pass


def _cancel_requested(run_id: int, model_cls=Run) -> bool:
    """Fresh read on its own session so a status change committed by the API is seen.

    Parameterized on the job's model class so the same poll serves Runs and
    Measurements (both carry a `status` that the API flips to 'canceling')."""
    try:
        with SessionLocal() as s:
            return s.scalar(select(model_cls.status)
                            .where(model_cls.id == run_id)) == "canceling"
    except Exception:  # noqa: BLE001
        return False


def _run_subprocess(cmd, log_path, run_id, model_cls=Run) -> tuple[int, bool]:
    """Run cmd, streaming output to log_path, while polling for an API cancel
    request. Returns (returncode, canceled)."""
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write("$ " + " ".join(cmd) + "\n\n")
        lf.flush()
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT)
        while True:
            try:
                proc.wait(timeout=2.0)
                return proc.returncode, False
            except subprocess.TimeoutExpired:
                pass
            if _cancel_requested(run_id, model_cls):
                _kill_tree(proc.pid)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                return (proc.returncode if proc.returncode is not None else -1), True


def _execute(db, run: Run) -> None:
    ds = db.get(Dataset, run.dataset_id)
    model = db.get(Model, run.model_id)
    if ds is None or model is None:
        _finish(db, run, "failed", "dataset or model missing")
        return

    out = Path(run.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "run.log"
    run.log_path = str(log_path)
    # Record where this run executed up front, so even a failed run is traceable.
    run.env = host_info()
    run.host = run.env.get("host")
    db.commit()

    p = run.params
    cmd = [
        settings.python, str(settings.segment_script),
        "--slices", ds.slices_path,
        "--model", model.path,
        "--case", _case(ds.name),
        "--out", str(out),
        "--folds", str(p.get("folds", "0")),
        "--step", str(p.get("step", 0.5)),
        "--device", str(p.get("device", "auto")),
        "--spacing", str(p.get("spacing_mm", 0.004)),
        "--pattern", str(p.get("pattern", "*rec*.bmp")),
    ]
    if p.get("tta"):
        cmd.append("--tta")

    returncode, canceled = _run_subprocess(cmd, log_path, run.id)
    if canceled:
        _finish(db, run, "canceled", "aborted by user")
        return

    case = _case(ds.name)
    result_json = out / f"{case}_result.json"
    if returncode == 0 and result_json.exists():
        r = json.loads(result_json.read_text())
        run.roi_voxels = r.get("roi_voxels")
        run.roi_mm3 = r.get("roi_mm3")
        run.roi_um3 = r.get("roi_um3")
        run.best_slice = r.get("best_slice")
        run.device_used = r.get("device")
        # Merge the compute-side debrief (GPU, versions, peak memory, timings).
        rec = dict(run.env or {})
        rec.update(r.get("environment", {}))
        run.env = rec
        run.gpu = rec.get("gpu")
        run.peak_ram_mb = rec.get("peak_ram_mb")
        run.peak_gpu_mb = rec.get("peak_gpu_mb")
        run.torch_version = rec.get("torch_version")
        run.input_nii = str(out / f"{case}_0000.nii.gz")
        run.mask_nii = str(out / f"{case}.nii.gz")
        run.preview_png = str(out / f"{case}_preview.png")
        _finish(db, run, "succeeded")
    else:
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-8:])
        except OSError:
            pass
        _finish(db, run, "failed", f"exit code {returncode}\n{tail}")


def _execute_measurement(db, m: Measurement) -> None:
    """Run the morphometry pipeline over a finished segmentation.

    Mirrors _execute: stamp the environment up front so even a failure is
    traceable, stream the driver's stdout to a log, then fold
    <case>_measurement.json back into the row.
    """
    run = db.get(Run, m.run_id)
    if run is None:
        _finish(db, m, "failed", "run missing")
        return

    p = dict(m.params or {})
    case = p.get("case") or _case_from_run(run)
    mask = p.get("mask_nii") or run.mask_nii
    image = p.get("input_nii") or run.input_nii
    if not mask or not os.path.exists(mask):
        _finish(db, m, "failed", f"mask not found: {mask}")
        return
    if not image or not os.path.exists(image):
        _finish(db, m, "failed", f"input volume not found: {image}")
        return
    if not MEASURE_SCRIPT.exists():
        _finish(db, m, "failed", f"measurement driver not found: {MEASURE_SCRIPT}")
        return

    out = Path(m.output_dir or (Path(settings.results_root) / f"{case}__morph__m{m.id}"))
    out.mkdir(parents=True, exist_ok=True)
    m.output_dir = str(out)
    log_path = out / "measure.log"
    m.log_path = str(log_path)
    m.env = host_info()
    m.host = m.env.get("host")
    db.commit()

    # Fall back to the RUN's own spacing, never the dataset's current value — see
    # registry.spacing_um_for for why the two can legitimately disagree.
    spacing_um = p.get("spacing_um") or spacing_um_for(run, db.get(Dataset, m.dataset_id))
    cmd = [
        settings.python, str(MEASURE_SCRIPT),
        "--mask", str(mask),
        "--image", str(image),
        "--case", str(case),
        "--out", str(out),
        "--pipeline", str(m.pipeline_version or "digitpipe_v5"),
        "--spacing-um", str(spacing_um),
    ]
    if p.get("skip_viz"):
        cmd.append("--skip-viz")

    returncode, canceled = _run_subprocess(cmd, log_path, m.id, Measurement)
    if canceled:
        _finish(db, m, "canceled", "aborted by user")
        return

    result_json = out / f"{case}_measurement.json"
    if returncode == 0 and result_json.exists():
        r = json.loads(result_json.read_text(encoding="utf-8"))
        metrics = r.get("metrics") or {}
        m.metrics = metrics
        for col in ("socket_volume_voxels", "socket_volume_mm3", "socket_radius_voxels",
                    "socket_radius_mm", "phalanx_volume_voxels", "phalanx_volume_mm3",
                    "bone_length_voxels", "bone_length_mm",
                    "euclidean_distance_voxels", "euclidean_distance_mm"):
            v = metrics.get(col)
            setattr(m, col, float(v) if isinstance(v, (int, float)) else None)
        centroid = metrics.get("socket_centroid")
        m.socket_centroid = centroid if isinstance(centroid, list) else None
        m.annotated_nii = r.get("annotated_nii")
        m.xlsx_path = r.get("xlsx_path")
        if r.get("pipeline_version"):
            m.pipeline_version = r["pipeline_version"]
        # Merge the compute-side debrief (versions, peak memory, timings).
        rec = dict(m.env or {})
        rec.update(r.get("environment", {}))
        m.env = rec
        m.host = rec.get("host") or m.host
        _finish(db, m, "succeeded")
    else:
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-8:])
        except OSError:
            pass
        _finish(db, m, "failed", f"exit code {returncode}\n{tail}")


def _case(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "case"


def _case_from_run(run: Run) -> str:
    """Case name a run's outputs are prefixed with (derived from the mask file, so
    it always matches the files on disk). Falls back to the dataset name."""
    if run.mask_nii:
        b = os.path.basename(run.mask_nii)
        for ext in (".nii.gz", ".nii"):
            if b.endswith(ext):
                return b[: -len(ext)]
    return _case(run.dataset.name) if run.dataset else f"run{run.id}"


KINDS = ("all", "segmentation", "measurement")


def run_worker(kind: str = "all") -> None:
    if kind not in KINDS:
        raise SystemExit(f"unknown --kind {kind!r} (expected one of {', '.join(KINDS)})")
    do_seg = kind in ("all", "segmentation")
    do_measure = kind in ("all", "measurement")

    init_db()
    db = SessionLocal()
    # Recover jobs left mid-flight by a previous crash — but only of the kinds THIS
    # worker handles, so a CPU measurement worker never touches the GPU worker's
    # in-flight runs.
    if do_seg:
        for stale in db.scalars(select(Run).where(Run.status.in_(["running", "canceling"]))).all():
            if stale.status == "canceling":
                _finish(db, stale, "canceled", "aborted by user")
            else:
                _finish(db, stale, "failed", "worker restarted while this run was in progress")
    if do_measure:
        for stale in db.scalars(select(Measurement).where(
                Measurement.status.in_(["running", "canceling"]))).all():
            if stale.status == "canceling":
                _finish(db, stale, "canceled", "aborted by user")
            else:
                _finish(db, stale, "failed",
                        "worker restarted while this measurement was in progress")
    print(f"[worker] ready ({kind}). polling every {settings.poll_seconds}s. "
          f"results -> {settings.results_root}", flush=True)
    try:
        while True:
            run = _claim_next(db) if do_seg else None
            if run is not None:
                print(f"[worker] run {run.id}: dataset={run.dataset_id} model={run.model_id} "
                      f"({run.model_version})", flush=True)
                try:
                    _execute(db, run)
                    print(f"[worker] run {run.id}: {run.status}", flush=True)
                except Exception as e:  # noqa: BLE001
                    _finish(db, run, "failed", f"{type(e).__name__}: {e}")
                    print(f"[worker] run {run.id} failed: {e}", flush=True)
                continue

            meas = _claim_next_measurement(db) if do_measure else None
            if meas is not None:
                print(f"[worker] measurement {meas.id}: run={meas.run_id} "
                      f"dataset={meas.dataset_id} ({meas.pipeline_version})", flush=True)
                try:
                    _execute_measurement(db, meas)
                    print(f"[worker] measurement {meas.id}: {meas.status}", flush=True)
                except Exception as e:  # noqa: BLE001
                    _finish(db, meas, "failed", f"{type(e).__name__}: {e}")
                    print(f"[worker] measurement {meas.id} failed: {e}", flush=True)
                continue

            time.sleep(settings.poll_seconds)
    except KeyboardInterrupt:
        print("[worker] stopping.", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    run_worker()
