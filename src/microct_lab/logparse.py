"""Parse SkyScan / Bruker `*_rec.log` reconstruction logs into structured metadata."""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def parse_rec_log(text: str) -> dict:
    """Flat key -> value dict from the INI-style SkyScan log (sections ignored/flattened)."""
    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip()
    return data


def _f(data: dict, key: str) -> Optional[float]:
    try:
        return float(data[key])
    except (KeyError, ValueError, TypeError):
        return None


def _i(data: dict, key: str) -> Optional[int]:
    v = _f(data, key)
    return int(v) if v is not None else None


def extract_metadata(data: dict) -> dict:
    """Pick the fields we surface in the registry from a parsed log dict."""
    return {
        "scanner": data.get("Scanner"),
        "voxel_size_um": _f(data, "Image Pixel Size (um)"),
        "width": _i(data, "Result Image Width (pixels)"),
        "height": _i(data, "Result Image Height (pixels)"),
        "slices": _i(data, "Sections Count"),
        "bit_depth": _i(data, "Depth (bits)"),
        "source_voltage_kv": _f(data, "Source Voltage (kV)"),
        "source_current_ua": _f(data, "Source Current (uA)"),
        "filter": data.get("Filter"),
        "scan_date": data.get("Study Date and Time"),
        "study": data.get("Filename Prefix") or data.get("Dataset Prefix"),
    }


def read_log(path: str | Path) -> tuple[dict, dict]:
    """Return (metadata, raw_dict) for a *_rec.log file path."""
    text = Path(path).read_text(errors="replace")
    raw = parse_rec_log(text)
    return extract_metadata(raw), raw
