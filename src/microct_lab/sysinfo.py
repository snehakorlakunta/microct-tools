"""Capture host/hardware info for a run debrief (no torch required)."""
from __future__ import annotations

import platform
import socket


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
        import os
        info["logical_cores"] = os.cpu_count()
    return info
