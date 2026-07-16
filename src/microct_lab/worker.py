"""DB-backed job worker: polls for queued runs and executes segmentation, one at a time.

Cross-platform (Windows/Linux), no Redis/broker. Run alongside the web server:
    microct-worker
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .config import settings
from .database import SessionLocal, init_db
from .models import Dataset, Model, Run
from .sysinfo import host_info


def _claim_next(db) -> Run | None:
    run = db.scalar(select(Run).where(Run.status == "queued").order_by(Run.created_at))
    if run is None:
        return None
    run.status = "running"
    run.started_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run


def _finish(db, run: Run, status: str, error: str | None = None) -> None:
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


def _cancel_requested(run_id: int) -> bool:
    """Fresh read on its own session so a status change committed by the API is seen."""
    try:
        with SessionLocal() as s:
            return s.scalar(select(Run.status).where(Run.id == run_id)) == "canceling"
    except Exception:  # noqa: BLE001
        return False


def _run_subprocess(cmd, log_path, run_id) -> tuple[int, bool]:
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
            if _cancel_requested(run_id):
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


def _case(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "case"


def run_worker() -> None:
    init_db()
    db = SessionLocal()
    # Recover runs left mid-flight by a previous crash.
    for stale in db.scalars(select(Run).where(Run.status.in_(["running", "canceling"]))).all():
        if stale.status == "canceling":
            _finish(db, stale, "canceled", "aborted by user")
        else:
            _finish(db, stale, "failed", "worker restarted while this run was in progress")
    print(f"[worker] ready. polling every {settings.poll_seconds}s. "
          f"results -> {settings.results_root}", flush=True)
    try:
        while True:
            run = _claim_next(db)
            if run is None:
                time.sleep(settings.poll_seconds)
                continue
            print(f"[worker] run {run.id}: dataset={run.dataset_id} model={run.model_id} "
                  f"({run.model_version})", flush=True)
            try:
                _execute(db, run)
                print(f"[worker] run {run.id}: {run.status}", flush=True)
            except Exception as e:  # noqa: BLE001
                _finish(db, run, "failed", f"{type(e).__name__}: {e}")
                print(f"[worker] run {run.id} failed: {e}", flush=True)
    except KeyboardInterrupt:
        print("[worker] stopping.", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    run_worker()
