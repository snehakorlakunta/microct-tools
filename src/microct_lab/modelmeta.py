"""Read a trained nnU-Net model folder: metadata + a stable fingerprint of the weights."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _load(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return {}


def fingerprint_model(folder: str | Path) -> str:
    """Short hash of plans.json + dataset.json + each fold checkpoint's size.

    Cheap (no reading 250 MB checkpoints) but changes whenever the model bits or
    config change — enough to tie a run to the exact model revision that made it.
    """
    folder = Path(folder)
    h = hashlib.sha256()
    for name in ("plans.json", "dataset.json"):
        f = folder / name
        if f.exists():
            h.update(f.read_bytes())
    for ckpt in sorted(folder.glob("fold_*/checkpoint_final.pth")):
        try:
            h.update(f"{ckpt.parent.name}:{ckpt.stat().st_size}".encode())
        except OSError:
            pass
    return h.hexdigest()[:16]


def available_folds(folder: str | Path) -> list[int]:
    folds = []
    for d in sorted(Path(folder).glob("fold_*")):
        try:
            folds.append(int(d.name.split("_")[1]))
        except (IndexError, ValueError):
            pass
    return folds


def read_model_folder(folder: str | Path) -> dict:
    """Extract labels, channels, training spacing, cross-val Dice, etc."""
    folder = Path(folder)
    ds = _load(folder / "dataset.json")
    plans = _load(folder / "plans.json")
    summary = _load(folder / "crossval_results_folds_0_1_2_3_4" / "summary.json")

    config = folder.name.split("__")[-1] if "__" in folder.name else "3d_fullres"
    spacing = None
    try:
        spacing = plans["configurations"][config]["spacing"][0]
    except Exception:
        try:
            spacing = plans["configurations"]["3d_fullres"]["spacing"][0]
        except Exception:
            spacing = None

    dice = None
    fm = summary.get("foreground_mean", {})
    if isinstance(fm, dict):
        dice = fm.get("Dice")

    return {
        "configuration": config,
        "labels": ds.get("labels", {}),
        "channels": ds.get("channel_names", {}),
        "training_spacing_mm": spacing,
        "cross_val_dice": dice,
        "source_dataset": plans.get("dataset_name") or ds.get("name"),
        "num_training_cases": ds.get("numTraining"),
        "fingerprint": fingerprint_model(folder),
        "folds": available_folds(folder),
    }
