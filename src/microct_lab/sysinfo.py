"""Capture host/hardware info for a run debrief and a pre-run compute probe."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess


def host_info() -> dict:
    """CPU / RAM / OS / hostname — gathered in the worker's environment."""
    info = {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "os": platform.system(),
        "python": platform.python_version(),
        "cpu": platform.processor() or platform.machine(),
    }
    try:
        import psutil
        info["logical_cores"] = psutil.cpu_count(logical=True)
        info["physical_cores"] = psutil.cpu_count(logical=False)
        info["ram_total_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        info["logical_cores"] = os.cpu_count()
    return info


def _nvidia_gpus() -> list[dict]:
    """Query GPUs via nvidia-smi (no torch needed). Empty list if none/unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    gpus = []
    for i, line in enumerate(out.stdout.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            continue
        g = {"index": i, "name": parts[0]}
        try:
            g["memory_total_mb"] = float(parts[1])
            g["memory_used_mb"] = float(parts[2])
            g["utilization_pct"] = float(parts[3])
        except (IndexError, ValueError):
            pass
        gpus.append(g)
    return gpus


def _torch_cuda() -> dict:
    """If torch is importable in THIS process, report its CUDA view."""
    try:
        import torch
    except Exception:
        return {"available": None}  # torch not in this env — unknown, not "no GPU"
    try:
        avail = bool(torch.cuda.is_available())
        info = {"available": avail, "torch_version": torch.__version__,
                "cuda_version": getattr(torch.version, "cuda", None)}
        if avail:
            info["devices"] = [torch.cuda.get_device_name(i)
                               for i in range(torch.cuda.device_count())]
        return info
    except Exception:
        return {"available": None}


def compute_info() -> dict:
    """What compute is available on THIS machine, for the pre-run allocation UI.

    `recommended_device` is what an `auto` run would pick: cuda if a GPU is
    detected, otherwise cpu. GPUs are detected via nvidia-smi first (works even
    when torch lives only in the worker's env), then corroborated by torch if it
    happens to be importable here.
    """
    base = host_info()
    gpus = _nvidia_gpus()
    torch_view = _torch_cuda()
    # torch (in the worker env) is authoritative on usability; nvidia-smi gives specs.
    has_gpu = bool(gpus) or bool(torch_view.get("available"))
    return {
        **base,
        "gpus": gpus,
        "gpu_count": len(gpus),
        "torch": torch_view,
        "recommended_device": "cuda" if has_gpu else "cpu",
        "devices": (["auto", "cuda", "cpu"] if has_gpu else ["auto", "cpu"]),
    }
