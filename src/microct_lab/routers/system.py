"""System endpoints: stats, config, compute probe, ingest, model discovery, open-folder."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Dataset, Model, Run
from ..schemas import IngestRequest
from ..registry import discover_models, ingest_root, resolve_nas
from ..sysinfo import compute_info

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
        "nas_root": str(settings.nas_root) if settings.nas_root else None,
        "analyses_dir": str(settings.analyses_dir),
        "default_device": settings.default_device,
    }


@router.get("/system/compute")
def system_compute():
    """CPU / RAM / GPU available on THIS machine — for pre-run device allocation."""
    return compute_info()


@router.post("/ingest")
def ingest(req: IngestRequest, db: Session = Depends(get_db)):
    return ingest_root(db, req.root)


class DiscoverRequest(BaseModel):
    root: str | None = None


@router.post("/system/discover-models")
def discover(req: DiscoverRequest, db: Session = Depends(get_db)):
    """Scan MICROCT_MODELS_ROOT (or a given root) and register new model folders."""
    return discover_models(db, req.root)


class OpenFolderRequest(BaseModel):
    path: str | None = None
    nas_relpath: str | None = None
    dataset_id: int | None = None


@router.post("/system/open-folder")
def open_folder(req: OpenFolderRequest, db: Session = Depends(get_db)):
    """Open a dataset/analysis folder in the local file manager (this machine).

    Intended for the local-lab workflow where the browser and server share a
    desktop. Resolves a NAS-relative path (portable across drive mappings), a
    dataset id, or an absolute path — the last only if it exists on this machine.
    """
    target: Path | None = None
    if req.dataset_id is not None:
        d = db.get(Dataset, req.dataset_id)
        if not d:
            raise HTTPException(404, "dataset not found")
        target = resolve_nas(d.nas_relpath) or Path(d.slices_path)
    elif req.nas_relpath:
        target = resolve_nas(req.nas_relpath)
    elif req.path:
        target = Path(req.path)
    if not target or not target.exists():
        raise HTTPException(404, f"folder not found on this machine: {target}")

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # noqa: SLF001 (Windows-only, intended)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"could not open folder: {e}")
    return {"opened": str(target)}


@router.get("/timeline")
def timeline(limit: int = 200, db: Session = Depends(get_db)):
    """Unified chronological feed of datasets, runs, and projects for the timeline view."""
    from ..models import Project

    events = []
    for d in db.scalars(select(Dataset).order_by(Dataset.created_at.desc()).limit(limit)).all():
        events.append({"kind": "dataset", "id": d.id, "title": d.name,
                       "detail": d.type, "at": d.created_at})
    for r in db.scalars(select(Run).order_by(Run.created_at.desc()).limit(limit)).all():
        events.append({"kind": "run", "id": r.id, "title": f"Run #{r.id}",
                       "detail": r.status, "at": r.created_at,
                       "dataset": r.dataset.name if r.dataset else None})
    for p in db.scalars(select(Project).order_by(Project.created_at.desc()).limit(limit)).all():
        events.append({"kind": "project", "id": p.id, "title": p.name,
                       "detail": "project", "at": p.created_at})
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"events": events[:limit]}
