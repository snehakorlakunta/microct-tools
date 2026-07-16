"""Dataset catalog endpoints: search / filter / sort, detail, thumbnail, edit."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Dataset, Run
from ..schemas import DatasetDetail, DatasetOut

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

SORTABLE = {"created_at", "name", "voxel_size_um", "slices", "scan_date", "study", "size_bytes"}


def _run_counts(db: Session) -> dict:
    return dict(db.execute(select(Run.dataset_id, func.count()).group_by(Run.dataset_id)).all())


@router.get("", response_model=list[DatasetOut])
def list_datasets(q: Optional[str] = None, study: Optional[str] = None,
                  scanner: Optional[str] = None, flagged: Optional[bool] = None,
                  sort: str = "created_at", order: str = "desc",
                  db: Session = Depends(get_db)):
    stmt = select(Dataset)
    if q:
        stmt = stmt.where(Dataset.name.ilike(f"%{q}%"))
    if study:
        stmt = stmt.where(Dataset.study == study)
    if scanner:
        stmt = stmt.where(Dataset.scanner == scanner)
    if flagged is not None:
        stmt = stmt.where(Dataset.flagged == flagged)
    col = getattr(Dataset, sort if sort in SORTABLE else "created_at")
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())

    counts = _run_counts(db)
    out = []
    for d in db.scalars(stmt).all():
        o = DatasetOut.model_validate(d)
        o.run_count = counts.get(d.id, 0)
        out.append(o)
    return out


@router.get("/facets")
def facets(db: Session = Depends(get_db)):
    studies = [s for (s,) in db.execute(
        select(Dataset.study).distinct().where(Dataset.study.isnot(None))).all()]
    scanners = [s for (s,) in db.execute(
        select(Dataset.scanner).distinct().where(Dataset.scanner.isnot(None))).all()]
    return {"studies": sorted(studies), "scanners": sorted(scanners)}


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


class DatasetPatch(BaseModel):
    tags: Optional[list] = None
    notes: Optional[str] = None
    flagged: Optional[bool] = None
    study: Optional[str] = None


@router.patch("/{dataset_id}", response_model=DatasetOut)
def patch_dataset(dataset_id: int, body: DatasetPatch, db: Session = Depends(get_db)):
    d = db.get(Dataset, dataset_id)
    if not d:
        raise HTTPException(404, "dataset not found")
    if body.tags is not None:
        d.tags = body.tags
    if body.notes is not None:
        d.notes = body.notes
    if body.flagged is not None:
        d.flagged = body.flagged
    if body.study is not None:
        d.study = body.study
    db.commit()
    db.refresh(d)
    o = DatasetOut.model_validate(d)
    o.run_count = len(d.runs)
    return o
