"""Local nnU-Net model-package handling."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from .constants import MODEL_CHECKPOINT, MODEL_CONFIGURATION


def safe_extract_zip(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"Unsafe path in model archive: {entry.filename}")
        archive.extractall(destination)


def find_model_folder(root: Path) -> Path:
    root = root.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Model path not found: {root}")
    candidates = []
    if root.is_dir():
        candidates.append(root)
        candidates.extend(path for path in root.rglob("*") if path.is_dir())
    for candidate in candidates:
        if not (candidate / "plans.json").is_file() or not (candidate / "dataset.json").is_file():
            continue
        fold_dirs = sorted(path for path in candidate.glob("fold_*") if path.is_dir())
        if fold_dirs and all((fold / MODEL_CHECKPOINT).is_file() for fold in fold_dirs):
            return candidate
    raise FileNotFoundError(
        f"Could not find a trained nnU-Net model folder with plans.json, dataset.json, and fold checkpoints under {root}"
    )


def validate_model_folder(model_folder: Path) -> list[int]:
    plans = json.loads((model_folder / "plans.json").read_text(encoding="utf-8"))
    if MODEL_CONFIGURATION not in plans.get("configurations", {}):
        raise ValueError(f"Model package does not contain the {MODEL_CONFIGURATION} configuration")
    dataset = json.loads((model_folder / "dataset.json").read_text(encoding="utf-8"))
    labels = dataset.get("labels", {})
    if labels.get("background") != 0 or sorted(labels.values()) != [0, 1]:
        raise ValueError(f"Expected a binary segmentation model; labels are {labels}")
    folds = sorted(
        int(path.name.removeprefix("fold_"))
        for path in model_folder.glob("fold_*")
        if path.is_dir() and path.name.removeprefix("fold_").isdigit()
    )
    if not folds:
        raise FileNotFoundError(f"No fold checkpoints found in {model_folder}")
    return folds


def prepare_model(model_path: Path, extraction_dir: Path) -> tuple[Path, bool]:
    model_path = model_path.expanduser().resolve()
    if model_path.is_file():
        if model_path.suffix.lower() != ".zip":
            raise ValueError(f"Model file must be a .zip archive: {model_path}")
        safe_extract_zip(model_path, extraction_dir)
        model_folder = find_model_folder(extraction_dir)
        extracted = True
    else:
        model_folder = find_model_folder(model_path)
        extracted = False
    validate_model_folder(model_folder)
    return model_folder, extracted


def remove_temporary_model(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)

