"""Dataset catalog endpoints: search / filter / sort, detail, thumbnail, edit, organize."""
from __future__ import annotations

import glob
import math
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Dataset, DatasetSet, Experiment, Run
from ..registry import resolve_nas
from ..schemas import DatasetDetail, DatasetOut, DatasetPatch

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

SORTABLE = {"created_at", "name", "voxel_size_um", "slices", "scan_date", "study",
            "size_bytes", "type", "organism"}


def _run_counts(db: Session) -> dict:
    return dict(db.execute(select(Run.dataset_id, func.count()).group_by(Run.dataset_id)).all())


def _out(d: Dataset, counts: dict) -> DatasetOut:
    o = DatasetOut.model_validate(d)
    o.run_count = counts.get(d.id, 0)
    return o


@router.get("", response_model=list[DatasetOut])
def list_datasets(q: Optional[str] = None, study: Optional[str] = None,
                  scanner: Optional[str] = None, type: Optional[str] = None,
                  organism: Optional[str] = None, tag: Optional[str] = None,
                  set_id: Optional[int] = None, experiment_id: Optional[int] = None,
                  unassigned: Optional[bool] = None, flagged: Optional[bool] = None,
                  include_archived: bool = False,
                  sort: str = "created_at", order: str = "desc",
                  db: Session = Depends(get_db)):
    stmt = select(Dataset)
    if q:
        stmt = stmt.where(Dataset.name.ilike(f"%{q}%"))
    if study:
        stmt = stmt.where(Dataset.study == study)
    if scanner:
        stmt = stmt.where(Dataset.scanner == scanner)
    if type:
        stmt = stmt.where(Dataset.type == type)
    if organism:
        stmt = stmt.where(Dataset.organism == organism)
    if set_id is not None:
        stmt = stmt.where(Dataset.set_id == set_id)
    if experiment_id is not None:
        stmt = stmt.where(Dataset.experiment_id == experiment_id)
    if unassigned:
        stmt = stmt.where(Dataset.experiment_id.is_(None), Dataset.set_id.is_(None))
    if flagged is not None:
        stmt = stmt.where(Dataset.flagged == flagged)
    if not include_archived:
        stmt = stmt.where(Dataset.archived == False)  # noqa: E712
    col = getattr(Dataset, sort if sort in SORTABLE else "created_at")
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())

    counts = _run_counts(db)
    rows = db.scalars(stmt).all()
    if tag:  # JSON list membership — filter in Python (small volumes)
        rows = [d for d in rows if tag in (d.tags or [])]
    return [_out(d, counts) for d in rows]


@router.get("/facets")
def facets(db: Session = Depends(get_db)):
    def distinct(col):
        return [v for (v,) in db.execute(
            select(col).distinct().where(col.isnot(None))).all()]
    tags: set = set()
    for (tl,) in db.execute(select(Dataset.tags)).all():
        for t in (tl or []):
            tags.add(t)
    return {"studies": sorted(distinct(Dataset.study)),
            "scanners": sorted(distinct(Dataset.scanner)),
            "types": sorted(distinct(Dataset.type)),
            "organisms": sorted(distinct(Dataset.organism)),
            "tags": sorted(tags)}


@router.get("/taxonomy")
def taxonomy(db: Session = Depends(get_db)):
    """Browse tree grouped by type > organism, for the Datasets navigator."""
    counts = _run_counts(db)
    rows = db.scalars(select(Dataset).where(Dataset.archived == False)).all()  # noqa: E712
    tree: dict = {}
    for d in rows:
        t = d.type or "other"
        org = d.organism or "—"
        tree.setdefault(t, {}).setdefault(org, []).append({
            "id": d.id, "name": d.name, "slices": d.slices,
            "voxel_size_um": d.voxel_size_um, "run_count": counts.get(d.id, 0),
            "set_id": d.set_id, "tags": d.tags or []})
    return {"types": [{"type": t, "organisms": [{"organism": o, "datasets": ds}
                       for o, ds in sorted(orgs.items())]}
                      for t, orgs in sorted(tree.items())]}


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")
    o = DatasetDetail.model_validate(d)
    o.run_count = len(d.runs)
    return o


@router.get("/{dataset_id}/thumbnail")
def thumbnail(dataset_id: int, db: Session = Depends(get_db)):
    d = db.get(Dataset, dataset_id)
    if not d or not d.thumbnail or not os.path.exists(d.thumbnail):
        raise HTTPException(404, "no thumbnail")
    return FileResponse(d.thumbnail, media_type="image/png")


def _build_dataset_view(d: Dataset) -> Optional[str]:
    """Build (and cache) a downsampled NIfTI of the raw slice stack for the viewer."""
    cache = os.path.join(settings.state_dir, "cache")
    os.makedirs(cache, exist_ok=True)
    out = os.path.join(cache, f"dataset_{d.id}_view.nii.gz")
    if os.path.exists(out):
        return out
    files = sorted(p for p in glob.glob(os.path.join(d.slices_path, d.pattern))
                   if "_spr" not in os.path.basename(p).lower()
                   and "_pp" not in os.path.basename(p).lower())
    if not files:
        return None
    import numpy as np
    import SimpleITK as sitk
    from PIL import Image
    H, W = np.array(Image.open(files[0]).convert("L")).shape
    f = max(1, math.ceil(max(len(files), H, W) / 512))   # keep max axis <= ~512
    arr = [np.array(Image.open(fp).convert("L"))[::f, ::f] for fp in files[::f]]
    vol = np.stack(arr, 0).astype("uint8")
    img = sitk.GetImageFromArray(vol)
    sp = ((d.voxel_size_um or 4.0) / 1000.0) * f
    img.SetSpacing((sp, sp, sp))
    sitk.WriteImage(img, out, useCompression=True)
    return out


@router.get("/{dataset_id}/view_volume.nii.gz")
def dataset_view_volume(dataset_id: int, db: Session = Depends(get_db)):
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")
    path = _build_dataset_view(d)
    if not path:
        raise HTTPException(404, "no slices to view")
    return FileResponse(path, media_type="application/gzip", filename=os.path.basename(path))


@router.patch("/{dataset_id}", response_model=DatasetOut)
def patch_dataset(dataset_id: int, body: DatasetPatch, db: Session = Depends(get_db)):
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")

    # Validate hierarchy moves and keep experiment_id consistent with the set.
    if body.set_id is not None:
        s = db.get(DatasetSet, body.set_id)
        if not s:
            raise HTTPException(404, "target set not found")
        d.set_id = s.id
        d.experiment_id = s.experiment_id
    if body.experiment_id is not None:
        if not db.get(Experiment, body.experiment_id):
            raise HTTPException(404, "target experiment not found")
        d.experiment_id = body.experiment_id
    if body.clear_set:
        d.set_id = None
    if body.clear_experiment:
        d.experiment_id = None
        d.set_id = None

    for field in ("name", "type", "organism", "tags", "notes", "flagged",
                  "archived", "study"):
        val = getattr(body, field)
        if val is not None:
            setattr(d, field, val)

    db.commit()
    db.refresh(d)
    o = DatasetOut.model_validate(d)
    o.run_count = len(d.runs)
    return o


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """Delete a dataset. A dataset that has ANY runs is *archived* instead of
    removed — a run is an immutable provenance record and must survive its
    dataset, so we never hard-delete a dataset that runs point at. Only a
    run-free dataset is actually removed from the registry (files untouched)."""
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")
    active = [r for r in d.runs if r.status in ("running", "queued", "canceling")]
    if active:
        raise HTTPException(409, "dataset has active runs; stop them first")
    if d.runs:
        d.archived = True
        db.commit()
        return {"archived": dataset_id, "reason": "has runs (kept for provenance)"}
    db.delete(d)
    db.commit()
    return {"deleted": dataset_id}


@router.get("/{dataset_id}/local-path")
def local_path(dataset_id: int, db: Session = Depends(get_db)):
    """Resolve where this dataset lives on THIS machine (for 'open folder')."""
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")
    resolved = resolve_nas(d.nas_relpath)
    path = str(resolved) if resolved else d.slices_path
    return {"nas_relpath": d.nas_relpath, "path": path, "exists": os.path.isdir(path)}
