"""DB-backed job worker: polls for queued jobs and executes them.

Two job kinds share this loop:
  * segmentation — a queued Run, executed via scripts/segment_microct.py (GPU).
    Up to `parallel_gpu_runs` of these execute CONCURRENTLY, each thread's
    subprocess pinned to its own GPU with CUDA_VISIBLE_DEVICES (see
    _gpu_pin). The limit is read live from the app_settings table each loop,
    so the UI can raise it without a worker restart; it is clamped to the
    number of GPUs actually present.
  * measurement  — a queued Measurement, executed via
    scripts/measure_morphometry.py (digitpipe) or scripts/measure_bvtv.py
    (interim threshold BV/TV). One at a time, in its own thread, so a long
    morphometry never blocks segmentation claims.

Cross-platform (Windows/Linux), no Redis/broker. Run alongside the web server:
    microct-worker                      # both kinds (default)
    microct-worker --kind segmentation  # GPU box
    microct-measure-worker              # CPU box, measurements only
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .config import DEFAULT_SCRIPTS_DIR, settings
from .database import SessionLocal, init_db
from .models import Dataset, Measurement, Model, Run
from .registry import get_app_setting, spacing_um_for
from .sysinfo import _nvidia_gpus, host_info

# The measurement driver. Read through getattr so that if `measure_script` is ever
# added to Settings it wins, without this module requiring it to exist today.
MEASURE_SCRIPT = Path(getattr(settings, "measure_script",
                              DEFAULT_SCRIPTS_DIR / "measure_morphometry.py"))
BVTV_SCRIPT = DEFAULT_SCRIPTS_DIR / "measure_bvtv.py"


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


def _run_subprocess(cmd, log_path, run_id, model_cls=Run, env=None) -> tuple[int, bool]:
    """Run cmd, streaming output to log_path, while polling for an API cancel
    request. Returns (returncode, canceled). `env` (if given) REPLACES the
    subprocess environment — build it from os.environ plus overrides."""
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write("$ " + " ".join(cmd) + "\n\n")
        lf.flush()
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env)
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


def _gpu_pin(device: str, gpu_slot: int | None, gpu_count: int) -> tuple[str, str | None]:
    """Resolve (device_arg, CUDA_VISIBLE_DEVICES) for a segmentation subprocess.

    * "cuda:N" pins to GPU N explicitly (the script itself only knows
      auto/cuda/cpu, so the pin happens via the env var).
    * "auto"/"cuda" with several GPUs and a slot assignment pins to the slot's
      GPU so parallel runs don't fight over device 0.
    * one GPU / cpu: no env change.
    """
    if device.startswith("cuda:"):
        return "cuda", device.split(":", 1)[1]
    if device in ("auto", "cuda") and gpu_slot is not None and gpu_count > 1:
        return device, str(gpu_slot % gpu_count)
    return device, None


def _execute(db, run: Run, gpu_slot: int | None = None, gpu_count: int = 0) -> None:
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

    p = run.params
    device_arg, cuda_visible = _gpu_pin(str(p.get("device", "auto")), gpu_slot, gpu_count)
    if gpu_slot is not None:
        run.env = {**run.env, "gpu_slot": gpu_slot,
                   **({"cuda_visible_devices": cuda_visible} if cuda_visible else {})}
    db.commit()

    cmd = [
        settings.python, str(settings.segment_script),
        "--slices", ds.slices_path,
        "--model", model.path,
        "--case", _case(ds.name),
        "--out", str(out),
        "--folds", str(p.get("folds", "0")),
        "--step", str(p.get("step", 0.5)),
        "--device", device_arg,
        "--spacing", str(p.get("spacing_mm", 0.004)),
        "--pattern", str(p.get("pattern", "*rec*.bmp")),
    ]
    if p.get("tta"):
        cmd.append("--tta")
    crop = p.get("crop_box")
    if crop and len(crop) == 6:
        cmd += ["--crop", *[str(int(v)) for v in crop]]

    env = None
    if cuda_visible is not None:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": cuda_visible}

    returncode, canceled = _run_subprocess(cmd, log_path, run.id, env=env)
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
    is_bvtv = str(m.pipeline_version or "").startswith("bvtv")
    driver = BVTV_SCRIPT if is_bvtv else MEASURE_SCRIPT
    if not mask or not os.path.exists(mask):
        _finish(db, m, "failed", f"mask not found: {mask}")
        return
    if not image or not os.path.exists(image):
        _finish(db, m, "failed", f"input volume not found: {image}")
        return
    if not driver.exists():
        _finish(db, m, "failed", f"measurement driver not found: {driver}")
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
    if is_bvtv:
        # Interim threshold BV/TV: cheap voxel counting, no digitpipe, no mask
        # QC (nothing here depends on the mask being one clean component).
        cmd = [
            settings.python, str(BVTV_SCRIPT),
            "--mask", str(mask),
            "--image", str(image),
            "--case", str(case),
            "--out", str(out),
            "--pipeline", str(m.pipeline_version),
            "--spacing-um", str(spacing_um),
            "--threshold-grey", str(p.get("threshold_grey", 80.0)),
        ]
    else:
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
        # Mask QC settings are server-wide policy, not per-job — a caller should
        # not be able to opt out of the checks by crafting a request body. Both
        # are read from settings only.
        if not settings.morph_mask_qc:
            cmd.append("--skip-mask-qc")
        elif settings.morph_allow_spacing_mismatch:
            cmd.append("--allow-spacing-mismatch")

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


def _seg_parallel_limit(db, gpu_count: int) -> int:
    """How many segmentation runs may execute at once, right now.

    The UI writes 'parallel_gpu_runs' into app_settings; the .env value is only
    the default. Clamped to the GPUs actually present — parallel CPU inference
    would just thrash, so no GPUs means serial."""
    try:
        v = int(get_app_setting(db, "parallel_gpu_runs", settings.parallel_gpu_runs) or 1)
    except (TypeError, ValueError):
        v = 1
    return max(1, min(v, gpu_count if gpu_count >= 1 else 1))


def _seg_thread(run_id: int, gpu_slot: int, gpu_count: int) -> None:
    """One segmentation, in its own thread with its own DB session."""
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        if run is None:
            return
        tag = f"[worker:g{gpu_slot}]"
        print(f"{tag} run {run.id}: dataset={run.dataset_id} model={run.model_id} "
              f"({run.model_version})", flush=True)
        try:
            _execute(db, run, gpu_slot=gpu_slot, gpu_count=gpu_count)
            print(f"{tag} run {run.id}: {run.status}", flush=True)
        except Exception as e:  # noqa: BLE001
            _finish(db, run, "failed", f"{type(e).__name__}: {e}")
            print(f"{tag} run {run.id} failed: {e}", flush=True)


def _measure_thread(measurement_id: int) -> None:
    with SessionLocal() as db:
        m = db.get(Measurement, measurement_id)
        if m is None:
            return
        print(f"[worker:m] measurement {m.id}: run={m.run_id} "
              f"dataset={m.dataset_id} ({m.pipeline_version})", flush=True)
        try:
            _execute_measurement(db, m)
            print(f"[worker:m] measurement {m.id}: {m.status}", flush=True)
        except Exception as e:  # noqa: BLE001
            _finish(db, m, "failed", f"{type(e).__name__}: {e}")
            print(f"[worker:m] measurement {m.id} failed: {e}", flush=True)


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

    gpus = _nvidia_gpus()
    print(f"[worker] ready ({kind}). polling every {settings.poll_seconds}s; "
          f"{len(gpus)} GPU(s) detected, parallel limit "
          f"{_seg_parallel_limit(db, len(gpus))}. "
          f"results -> {settings.results_root}", flush=True)

    # Threaded slot pool: seg_slots maps a GPU slot index -> live thread; one
    # extra thread runs measurements. Each thread opens its own session — the
    # loop's `db` is only for claiming. SQLite in WAL mode with busy_timeout
    # serializes the commits.
    seg_slots: dict[int, threading.Thread] = {}
    measure_thread: threading.Thread | None = None
    try:
        while True:
            for slot in [s for s, t in seg_slots.items() if not t.is_alive()]:
                del seg_slots[slot]
            if measure_thread is not None and not measure_thread.is_alive():
                measure_thread = None

            claimed = False
            if do_seg:
                limit = _seg_parallel_limit(db, len(gpus))
                while len(seg_slots) < limit:
                    run = _claim_next(db)
                    if run is None:
                        break
                    slot = next(i for i in range(limit) if i not in seg_slots)
                    t = threading.Thread(target=_seg_thread, daemon=True,
                                         args=(run.id, slot, len(gpus)))
                    seg_slots[slot] = t
                    t.start()
                    claimed = True

            if do_measure and measure_thread is None:
                meas = _claim_next_measurement(db)
                if meas is not None:
                    measure_thread = threading.Thread(
                        target=_measure_thread, daemon=True, args=(meas.id,))
                    measure_thread.start()
                    claimed = True

            if not claimed:
                time.sleep(settings.poll_seconds)
    except KeyboardInterrupt:
        print("[worker] stopping.", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    run_worker()
