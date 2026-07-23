"""Analyses: index R code + figures living in the shared Analyses folder."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Analysis
from ..schemas import AnalysisIn, AnalysisOut, AnalysisPatch

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".tif", ".tiff", ".pdf"}
CODE_EXTS = {".r", ".rmd", ".py", ".ipynb", ".sh", ".txt", ".md", ".csv", ".tsv", ".json"}


def _folder(a: Analysis) -> Path:
    """Absolute folder for this analysis on THIS machine."""
    rel = a.files_relpath or f"analysis_{a.id}"
    return (settings.analyses_dir / rel).resolve()


def _safe_join(base: Path, name: str) -> Path:
    """Join and confirm the result stays inside base (no path traversal)."""
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(400, "invalid path")
    return target


@router.get("", response_model=list[AnalysisOut])
def list_analyses(project_id: int | None = None, experiment_id: int | None = None,
                  db: Session = Depends(get_db)):
    stmt = select(Analysis).order_by(Analysis.created_at.desc())
    if project_id:
        stmt = stmt.where(Analysis.project_id == project_id)
    if experiment_id:
        stmt = stmt.where(Analysis.experiment_id == experiment_id)
    return db.scalars(stmt).all()


@router.post("", response_model=AnalysisOut)
def create_analysis(body: AnalysisIn, db: Session = Depends(get_db)):
    a = Analysis(
        title=body.title, project_id=body.project_id, experiment_id=body.experiment_id,
        type=body.type, description=body.description, files_relpath=body.files_relpath,
        dataset_ids=body.dataset_ids or [], set_ids=body.set_ids or [],
        run_ids=body.run_ids or [], tags=body.tags or [],
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


@router.get("/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: int, db: Session = Depends(get_db)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    return a


@router.patch("/{analysis_id}", response_model=AnalysisOut)
def patch_analysis(analysis_id: int, body: AnalysisPatch, db: Session = Depends(get_db)):
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    for field in ("title", "project_id", "experiment_id", "type", "description",
                  "files_relpath", "dataset_ids", "set_ids", "run_ids", "tags"):
        val = getattr(body, field)
        if val is not None:
            setattr(a, field, val)
    db.commit()
    db.refresh(a)
    return a


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: int, db: Session = Depends(get_db)):
    """Delete the analysis *record* only. The files on the NAS are never touched."""
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    db.delete(a)
    db.commit()
    return {"deleted": analysis_id}


@router.get("/{analysis_id}/files")
def analysis_files(analysis_id: int, db: Session = Depends(get_db)):
    """List files in this analysis's folder, split into figures vs code/data."""
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    folder = _folder(a)
    if not folder.is_dir():
        return {"folder": str(folder), "exists": False, "figures": [], "files": []}
    figures, files = [], []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(folder).as_posix()
        ext = p.suffix.lower()
        entry = {"name": rel, "size": p.stat().st_size, "ext": ext}
        if ext in IMAGE_EXTS:
            entry["url"] = f"/api/analyses/{analysis_id}/file?name={rel}"
            figures.append(entry)
        else:
            files.append(entry)
    return {"folder": str(folder), "exists": True, "figures": figures, "files": files}


@router.get("/{analysis_id}/file")
def analysis_file(analysis_id: int, name: str, db: Session = Depends(get_db)):
    """Serve a single file (image inline, others as download) from the analysis folder."""
    a = db.get(Analysis, analysis_id)
    if not a:
        raise HTTPException(404, "analysis not found")
    folder = _folder(a)
    target = _safe_join(folder, name)
    if not target.is_file():
        raise HTTPException(404, "file not found")
    media = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=media, filename=os.path.basename(name))
