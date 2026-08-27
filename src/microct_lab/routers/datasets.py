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
from ..models import Dataset, DatasetSet, Experiment, Project, Run
from ..registry import resolve_nas
from ..schemas import (DatasetBulkRequest, DatasetBulkResult, DatasetBulkRowResult,
                       DatasetDetail, DatasetOut, DatasetPatch)

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

SORTABLE = {"created_at", "name", "voxel_size_um", "slices", "scan_date", "study",
            "size_bytes", "type", "subtype", "organism", "digit_id"}

# Fields whose manual edits are remembered in Dataset.edited_fields, so that
# set-detail propagation ("unedited only") and re-ingest never overwrite a value
# a person typed. Hierarchy moves and QC flags are deliberately not tracked.
TRACKED_EDITS = ("name", "type", "subtype", "organism", "digit_id",
                 "unamputated", "study")


def _run_counts(db: Session) -> dict:
    return dict(db.execute(select(Run.dataset_id, func.count()).group_by(Run.dataset_id)).all())


def _out(d: Dataset, counts: dict) -> DatasetOut:
    o = DatasetOut.model_validate(d)
    o.run_count = counts.get(d.id, 0)
    return o


@router.get("", response_model=list[DatasetOut])
def list_datasets(q: Optional[str] = None, study: Optional[str] = None,
                  scanner: Optional[str] = None, type: Optional[str] = None,
                  subtype: Optional[str] = None,
                  organism: Optional[str] = None, tag: Optional[str] = None,
                  digit_id: Optional[str] = None,
                  project_id: Optional[int] = None,
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
    if subtype:
        stmt = stmt.where(Dataset.subtype == subtype)
    if organism:
        stmt = stmt.where(Dataset.organism == organism)
    if digit_id:
        stmt = stmt.where(Dataset.digit_id == digit_id)
    if project_id is not None:
        stmt = stmt.where(Dataset.project_id == project_id)
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
            "subtypes": sorted(distinct(Dataset.subtype)),
            "organisms": sorted(distinct(Dataset.organism)),
            "digit_ids": sorted(distinct(Dataset.digit_id)),
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
            "set_id": d.set_id, "subtype": d.subtype, "digit_id": d.digit_id,
            "tags": d.tags or []})
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


def _mark_edited(d: Dataset, fields: list[str]) -> None:
    """Record manual edits. Reassigns the list — SQLAlchemy does not track
    in-place mutation of a plain JSON column."""
    if fields:
        d.edited_fields = sorted(set(list(d.edited_fields or []) + fields))


def _inherit_set_details(d: Dataset, s: DatasetSet) -> None:
    """Autofill set details onto a newly assigned member — but never over a
    field the user set by hand (see Dataset.edited_fields)."""
    edited = set(d.edited_fields or [])
    if s.organism and "organism" not in edited:
        d.organism = s.organism
    if s.subtype and "subtype" not in edited:
        d.subtype = s.subtype


def _apply_hierarchy(db: Session, d: Dataset, body) -> None:
    """Membership moves with the consistency rules:
    set ⇒ its experiment + project; experiment ⇒ its project (dropping a set
    that belongs elsewhere); project alone touches nothing deeper unless the
    current experiment contradicts it. Clears cascade downward only."""
    if body.set_id is not None:
        s = db.get(DatasetSet, body.set_id)
        if not s:
            raise HTTPException(404, "target set not found")
        d.set_id = s.id
        d.experiment_id = s.experiment_id
        e = db.get(Experiment, s.experiment_id)
        if e:
            d.project_id = e.project_id
        _inherit_set_details(d, s)
    if body.experiment_id is not None:
        e = db.get(Experiment, body.experiment_id)
        if not e:
            raise HTTPException(404, "target experiment not found")
        d.experiment_id = e.id
        d.project_id = e.project_id
        if d.set_id:
            s = db.get(DatasetSet, d.set_id)
            if s and s.experiment_id != e.id:
                d.set_id = None
    if body.project_id is not None:
        p = db.get(Project, body.project_id)
        if not p:
            raise HTTPException(404, "target project not found")
        d.project_id = p.id
        if d.experiment_id:
            e = db.get(Experiment, d.experiment_id)
            if e and e.project_id != p.id:
                d.experiment_id = None
                d.set_id = None
    if body.clear_set:
        d.set_id = None
    if body.clear_experiment:
        d.experiment_id = None
        d.set_id = None
    if body.clear_project:
        d.project_id = None
        d.experiment_id = None
        d.set_id = None


def _apply_patch(db: Session, d: Dataset, body: DatasetPatch) -> None:
    """Shared by single PATCH and the bulk endpoint."""
    _apply_hierarchy(db, d, body)

    edited: list[str] = []
    for field in TRACKED_EDITS:
        val = getattr(body, field)
        if val is not None:
            setattr(d, field, val)
            edited.append(field)
    for field in ("tags", "notes", "flagged", "archived"):
        val = getattr(body, field)
        if val is not None:
            setattr(d, field, val)

    if body.crop_box is not None:
        box = [int(v) for v in body.crop_box]
        if len(box) != 6 or any(box[i] >= box[i + 1] for i in (0, 2, 4)) or min(box) < 0:
            raise HTTPException(400, "crop_box must be [z0,z1,y0,y1,x0,x1] with "
                                     "each min < max and all values >= 0")
        d.crop_box = box
    if body.clear_crop:
        d.crop_box = None
    if body.clear_digit_id:
        d.digit_id = None
        edited.append("digit_id")  # an explicit clear is an edit: re-ingest must not refill it
    _mark_edited(d, edited)


@router.patch("/{dataset_id}", response_model=DatasetOut)
def patch_dataset(dataset_id: int, body: DatasetPatch, db: Session = Depends(get_db)):
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")
    _apply_patch(db, d, body)
    db.commit()
    db.refresh(d)
    o = DatasetOut.model_validate(d)
    o.run_count = len(d.runs)
    return o


@router.post("/bulk", response_model=DatasetBulkResult)
def bulk_update(body: DatasetBulkRequest, db: Session = Depends(get_db)):
    """Apply assignment / tag / rename changes to many datasets in one request.

    Each dataset is settled independently — a bad row is reported, not allowed
    to abandon the rest. Renames are final names computed by the client (its
    regex preview IS the semantics); the server only applies them and refuses
    collisions with any name that would exist after the batch."""
    if not body.ids:
        raise HTTPException(400, "ids is empty")

    # Collision check across the post-batch namespace: names of untouched
    # datasets + the proposed new names.
    if body.renames:
        taken = {n for (n,) in db.execute(select(Dataset.name).where(
            Dataset.id.notin_(list(body.renames.keys())))).all()}
        proposed = list(body.renames.values())
        dupes = {n for n in proposed if proposed.count(n) > 1 or n in taken}
        if any(not n.strip() for n in proposed):
            raise HTTPException(400, "a rename produced an empty name")
        if dupes:
            raise HTTPException(409, f"rename collision: {sorted(dupes)[:5]}")

    results: list[DatasetBulkRowResult] = []
    for did in body.ids:
        d = db.get(Dataset, did)
        if not d:
            results.append(DatasetBulkRowResult(id=did, ok=False, error="not found"))
            continue
        try:
            patch = DatasetPatch(
                project_id=body.project_id, experiment_id=body.experiment_id,
                set_id=body.set_id, clear_project=body.clear_project or None,
                clear_experiment=body.clear_experiment or None,
                clear_set=body.clear_set or None,
                name=body.renames.get(did))
            _apply_patch(db, d, patch)
            if body.renames.get(did):
                _mark_edited(d, ["name"])
            if body.add_tags or body.remove_tags:
                tags = [t for t in (d.tags or []) if t not in body.remove_tags]
                tags += [t for t in body.add_tags if t not in tags]
                d.tags = tags
            db.commit()
            results.append(DatasetBulkRowResult(id=did, ok=True, name=d.name))
        except HTTPException as e:
            db.rollback()
            results.append(DatasetBulkRowResult(id=did, ok=False, error=str(e.detail)))
        except Exception as e:  # noqa: BLE001 — report, don't abort the batch
            db.rollback()
            results.append(DatasetBulkRowResult(id=did, ok=False,
                                                error=f"{type(e).__name__}: {e}"))
    ok = sum(1 for r in results if r.ok)
    return DatasetBulkResult(attempted=len(results), succeeded=ok,
                             failed=len(results) - ok, results=results)


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
