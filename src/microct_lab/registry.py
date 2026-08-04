"""Service layer: register models, ingest datasets, enqueue runs. Used by API + CLI."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .logparse import read_log
from .models import Dataset, Model, Run
from .modelmeta import read_model_folder

SLICE_EXTS = ("*rec*.bmp", "*rec*.tif", "*rec*.tiff")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "item"


def spacing_um_for(run, ds=None) -> float:
    """Physical voxel size (um) to convert a run's mask into millimetres.

    Comes from the RUN, not the dataset. `run.params["spacing_mm"]` — always set by
    enqueue_runs below — is the spacing the segmentation actually baked into the mask
    NIfTI geometry and used for the run's own roi_mm3, and it is an immutable snapshot.
    `Dataset.voxel_size_um` is neither of those things: an operator can override the
    spacing per run for a binned reconstruction, and re-ingesting a dataset rewrites
    voxel_size_um in place. Deriving mm from the dataset would therefore describe a
    mask at one scale using a spacing from another — every volume off by the cube of
    the ratio and every length by the ratio, while the voxel-unit values stay perfectly
    correct and nothing raises. The dataset is only a fallback for runs predating this.
    """
    spacing_mm = (getattr(run, "params", None) or {}).get("spacing_mm")
    if spacing_mm:
        return float(spacing_mm) * 1000.0
    if ds is None:
        ds = getattr(run, "dataset", None)
    if ds is not None and ds.voxel_size_um:
        return float(ds.voxel_size_um)
    return 4.0


# --------------------------------------------------------------------------- NAS paths
def nas_relpath_for(abs_path: str | Path) -> Optional[str]:
    """Path of `abs_path` relative to the NAS base ("Ultron"), using forward
    slashes so it round-trips across OSes. Returns None if it's outside the base."""
    try:
        rel = Path(abs_path).resolve().relative_to(settings.nas_base.resolve())
        return rel.as_posix()
    except (ValueError, OSError):
        return None


def resolve_nas(relpath: Optional[str]) -> Optional[Path]:
    """Resolve a stored NAS-relative path to an absolute path on THIS machine."""
    if not relpath:
        return None
    return (settings.nas_base / relpath).resolve()


# --------------------------------------------------------------------------- models
def register_model(db: Session, path: str, name: Optional[str] = None,
                   family: Optional[str] = None, version: Optional[str] = None) -> Model:
    folder = Path(path)
    if not folder.exists():
        raise FileNotFoundError(f"Model folder not found: {folder}")
    meta = read_model_folder(folder)

    family = family or meta.get("source_dataset") or folder.parent.name
    if version is None:
        n = db.scalar(select(func.count()).select_from(Model).where(Model.family == family)) or 0
        version = f"v{n + 1}"
    name = name or f"{family} {version}"

    existing = db.scalar(select(Model).where(Model.fingerprint == meta["fingerprint"]))
    if existing:
        return existing  # same weights already registered

    m = Model(
        name=name, family=family, version=version, path=str(folder),
        configuration=meta["configuration"], labels=meta["labels"],
        channels=meta["channels"], training_spacing_mm=meta["training_spacing_mm"],
        cross_val_dice=meta["cross_val_dice"], source_dataset=meta["source_dataset"],
        num_training_cases=meta["num_training_cases"], fingerprint=meta["fingerprint"],
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


# --------------------------------------------------------------------------- datasets
def _find_slices(folder: Path) -> list[Path]:
    for pat in SLICE_EXTS:
        files = [p for p in folder.glob(pat)
                 if "_spr" not in p.name.lower() and "_pp" not in p.name.lower()]
        if files:
            return sorted(files)
    return []


def _thumbnail(name: str, slices: list[Path]) -> Optional[str]:
    try:
        from PIL import Image
        mid = slices[len(slices) // 2]
        im = Image.open(mid).convert("L")
        im.thumbnail((320, 320))
        out = settings.thumbs_dir / f"{_safe(name)}.png"
        im.save(out)
        return str(out)
    except Exception:
        return None


def ingest_root(db: Session, root: Optional[str] = None) -> dict:
    root = Path(root or settings.data_root)
    created, updated, skipped = [], [], 0
    for logf in root.rglob("*_rec.log"):
        folder = logf.parent
        slices = _find_slices(folder)
        if not slices:
            skipped += 1
            continue
        meta, raw = read_log(logf)
        pattern = "*rec*.bmp" if slices[0].suffix.lower() == ".bmp" else f"*rec*{slices[0].suffix}"
        name = folder.name
        size = sum((p.stat().st_size for p in slices), 0)

        ds = db.scalar(select(Dataset).where(Dataset.slices_path == str(folder)))
        is_new = ds is None
        if is_new:
            ds = Dataset(name=name, slices_path=str(folder))
            db.add(ds)
        ds.pattern = pattern
        ds.scanner = meta["scanner"]
        ds.voxel_size_um = meta["voxel_size_um"]
        ds.width, ds.height, ds.slices = meta["width"], meta["height"], len(slices)
        ds.bit_depth = meta["bit_depth"]
        ds.source_voltage_kv = meta["source_voltage_kv"]
        ds.source_current_ua = meta["source_current_ua"]
        ds.filter = meta["filter"]
        ds.scan_date = meta["scan_date"]
        ds.study = meta["study"]
        ds.log = raw
        ds.size_bytes = size
        ds.thumbnail = _thumbnail(name, slices)
        ds.nas_relpath = nas_relpath_for(folder)
        db.commit()
        db.refresh(ds)
        (created if is_new else updated).append(ds.name)
    return {"created": created, "updated": updated, "skipped_no_slices": skipped,
            "root": str(root)}


# --------------------------------------------------------------------------- runs
def enqueue_runs(db: Session, dataset_ids: list[int], model_id: int, *,
                 folds: str = "0", tta: bool = False, step: float = 0.5,
                 device: str = "auto", spacing_mm: Optional[float] = None) -> list[Run]:
    model = db.get(Model, model_id)
    if model is None:
        raise ValueError(f"Model {model_id} not found")
    runs: list[Run] = []
    for did in dataset_ids:
        ds = db.get(Dataset, did)
        if ds is None:
            continue
        spacing = spacing_mm or (ds.voxel_size_um / 1000.0 if ds.voxel_size_um else 0.004)
        params = {"folds": folds, "tta": tta, "step": step,
                  "device": device or settings.default_device, "spacing_mm": spacing,
                  "pattern": ds.pattern}
        snapshot = {"id": model.id, "name": model.name, "family": model.family,
                    "version": model.version, "fingerprint": model.fingerprint,
                    "path": model.path, "configuration": model.configuration,
                    "cross_val_dice": model.cross_val_dice}
        run = Run(dataset_id=ds.id, model_id=model.id, status="queued", params=params,
                  model_version=model.version, model_snapshot=snapshot)
        db.add(run)
        db.flush()  # get run.id
        run.output_dir = str(Path(settings.results_root) /
                             f"{_safe(ds.name)}__{_safe(model.name)}__run{run.id}")
        runs.append(run)
    db.commit()
    for r in runs:
        db.refresh(r)
    return runs


# ----------------------------------------------------------------- model discovery
def _looks_like_model(folder: Path) -> bool:
    """A trained nnU-Net model folder has a plans.json and at least one fold."""
    return (folder / "plans.json").exists() and any(folder.glob("fold_*"))


def discover_models(db: Session, root: Optional[str] = None) -> dict:
    """Scan MICROCT_MODELS_ROOT for trained model folders and register any new ones.

    This is the counterpart to dataset ingest: it wires the models root from .env
    into the registry so models don't have to be added one path at a time.
    """
    base = Path(root or settings.models_root)
    registered, skipped = [], 0
    if not base.exists():
        return {"root": str(base), "registered": registered, "skipped": skipped,
                "error": "models root does not exist"}
    # A model folder itself, or any subfolder, may be the trained model.
    candidates = [base] if _looks_like_model(base) else []
    for sub in sorted(base.rglob("plans.json")):
        folder = sub.parent
        if _looks_like_model(folder):
            candidates.append(folder)
    seen: set[str] = set()
    for folder in candidates:
        key = str(folder.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            m = register_model(db, str(folder))
            registered.append({"id": m.id, "name": m.name, "family": m.family,
                               "version": m.version})
        except Exception:  # noqa: BLE001 — skip unreadable/partial model folders
            # A failed commit (e.g. a duplicate Model.name) poisons the session;
            # roll back so the remaining candidates can still be registered.
            db.rollback()
            skipped += 1
    return {"root": str(base), "registered": registered, "skipped": skipped}
