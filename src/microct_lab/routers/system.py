"""System endpoints: stats, config, ingest trigger."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Dataset, Model, Run
from ..schemas import IngestRequest
from ..registry import ingest_root

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    status_counts = dict(db.execute(
        select(Run.status, func.count()).group_by(Run.status)).all())
    recent = db.scalars(select(Run).order_by(Run.created_at.desc()).limit(8)).all()
    return {
        "datasets": db.scalar(select(func.count()).select_from(Dataset)) or 0,
        "models": db.scalar(select(func.count()).select_from(Model)) or 0,
        "model_families": db.scalar(select(func.count(func.distinct(Model.family)))) or 0,
        "runs": db.scalar(select(func.count()).select_from(Run)) or 0,
        "runs_by_status": status_counts,
        "recent_runs": [
            {"id": r.id, "status": r.status, "dataset": r.dataset.name if r.dataset else None,
             "model": r.model.name if r.model else None, "version": r.model_version,
             "roi_mm3": r.roi_mm3, "created_at": r.created_at}
            for r in recent
        ],
    }


@router.get("/config")
def config():
    return {
        "data_root": str(settings.data_root),
        "results_root": str(settings.results_root),
        "models_root": str(settings.models_root),
        "state_dir": str(settings.state_dir),
        "default_device": settings.default_device,
    }


@router.post("/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    return ingest_root(db, req.root)
