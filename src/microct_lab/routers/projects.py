"""Projects / Experiments / Sets: the organization hierarchy, plus stats & export."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Analysis, Dataset, DatasetSet, Experiment, Project, Run
from ..schemas import (ExperimentIn, ExperimentOut, ExperimentPatch, ProjectIn,
                       ProjectOut, ProjectPatch, SetIn, SetOut, SetPatch)
from .. import stats as stats_mod

router = APIRouter(prefix="/api", tags=["projects"])

PROJECT_SORTS = {"created_at": Project.created_at, "name": Project.name,
                 "project_lead": Project.project_lead}


# --------------------------------------------------------------------- projects
def _project_out(db: Session, p: Project) -> ProjectOut:
    o = ProjectOut.model_validate(p)
    o.experiment_count = len(p.experiments)
    o.analysis_count = len(p.analyses)
    # Direct membership: project_id is stamped on the dataset whenever it is
    # placed anywhere in this project (init_db backfills legacy rows).
    o.dataset_count = db.scalar(select(func.count()).select_from(Dataset)
                                .where(Dataset.project_id == p.id)) or 0
    return o


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(include_archived: bool = False, q: Optional[str] = None,
                  sort: str = "created_at", order: str = "desc",
                  db: Session = Depends(get_db)):
    stmt = select(Project)
    if not include_archived:
        stmt = stmt.where(Project.archived == False)  # noqa: E712
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Project.name.ilike(like),
                              Project.project_lead.ilike(like),
                              Project.description.ilike(like)))
    col = PROJECT_SORTS.get(sort, Project.created_at)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
    rows = db.scalars(stmt).all()
    if q:  # tag matches — JSON list membership, filtered in Python (small volumes)
        ql = q.lower()
        extra = [p for p in db.scalars(select(Project)).all()
                 if p not in rows and any(ql in str(t).lower() for t in (p.tags or []))
                 and (include_archived or not p.archived)]
        rows = list(rows) + extra
    return [_project_out(db, p) for p in rows]


@router.post("/projects", response_model=ProjectOut)
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    p = Project(name=body.name, project_lead=body.project_lead,
                description=body.description, tags=body.tags or [])
    db.add(p)
    db.commit()
    db.refresh(p)
    return _project_out(db, p)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return _project_out(db, p)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, body: ProjectPatch, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    for field in ("name", "project_lead", "description", "tags", "archived"):
        val = getattr(body, field)
        if val is not None:
            setattr(p, field, val)
    db.commit()
    db.refresh(p)
    return _project_out(db, p)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Delete an empty project. Detaches nothing destructive: experiments must be
    emptied first (their datasets are only unlinked, never deleted)."""
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    if p.experiments:
        raise HTTPException(409, "project has experiments; remove them first")
    # Unlink directly-attached datasets (never delete them).
    for d in db.scalars(select(Dataset).where(Dataset.project_id == p.id)).all():
        d.project_id = None
    db.delete(p)
    db.commit()
    return {"deleted": project_id}


# ------------------------------------------------------------------ experiments
def _exp_out(db: Session, e: Experiment) -> ExperimentOut:
    o = ExperimentOut.model_validate(e)
    o.set_count = len(e.sets)
    o.dataset_count = db.scalar(select(func.count()).select_from(Dataset)
                                .where(Dataset.experiment_id == e.id)) or 0
    return o


@router.get("/experiments", response_model=list[ExperimentOut])
def list_experiments(project_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(Experiment).order_by(Experiment.created_at)
    if project_id:
        stmt = stmt.where(Experiment.project_id == project_id)
    return [_exp_out(db, e) for e in db.scalars(stmt).all()]


@router.post("/experiments", response_model=ExperimentOut)
def create_experiment(body: ExperimentIn, db: Session = Depends(get_db)):
    if not db.get(Project, body.project_id):
        raise HTTPException(404, "project not found")
    e = Experiment(project_id=body.project_id, name=body.name, type=body.type,
                   description=body.description, tags=body.tags or [])
    db.add(e)
    db.commit()
    db.refresh(e)
    return _exp_out(db, e)


@router.patch("/experiments/{experiment_id}", response_model=ExperimentOut)
def patch_experiment(experiment_id: int, body: ExperimentPatch, db: Session = Depends(get_db)):
    e = db.get(Experiment, experiment_id)
    if not e:
        raise HTTPException(404, "experiment not found")
    if body.project_id is not None and not db.get(Project, body.project_id):
        raise HTTPException(404, "target project not found")
    for field in ("name", "type", "description", "tags", "project_id"):
        val = getattr(body, field)
        if val is not None:
            setattr(e, field, val)
    db.commit()
    db.refresh(e)
    return _exp_out(db, e)


@router.delete("/experiments/{experiment_id}")
def delete_experiment(experiment_id: int, db: Session = Depends(get_db)):
    e = db.get(Experiment, experiment_id)
    if not e:
        raise HTTPException(404, "experiment not found")
    # Unlink any directly-attached datasets (never delete them).
    for d in db.scalars(select(Dataset).where(Dataset.experiment_id == e.id)).all():
        d.experiment_id = None
        d.set_id = None
    db.delete(e)  # cascades to its (now dataset-unlinked) sets
    db.commit()
    return {"deleted": experiment_id}


# ------------------------------------------------------------------------- sets
def _set_out(e_set: DatasetSet) -> SetOut:
    o = SetOut.model_validate(e_set)
    o.dataset_count = len(e_set.datasets)
    return o


@router.get("/sets", response_model=list[SetOut])
def list_sets(experiment_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(DatasetSet).order_by(DatasetSet.created_at)
    if experiment_id:
        stmt = stmt.where(DatasetSet.experiment_id == experiment_id)
    return [_set_out(s) for s in db.scalars(stmt).all()]


@router.post("/sets", response_model=SetOut)
def create_set(body: SetIn, db: Session = Depends(get_db)):
    if not db.get(Experiment, body.experiment_id):
        raise HTTPException(404, "experiment not found")
    s = DatasetSet(experiment_id=body.experiment_id, name=body.name,
                   description=body.description, organism=body.organism,
                   subtype=body.subtype, tags=body.tags or [])
    db.add(s)
    db.commit()
    db.refresh(s)
    return _set_out(s)


def _propagate_set_details(db: Session, s: DatasetSet, mode: str) -> dict:
    """Push the set's organism/subtype onto member datasets.

    mode "all" overwrites every member; "unedited" skips a dataset for any field
    it lists in edited_fields (a manual value is never clobbered silently).
    Returns counts for the UI toast."""
    updated = skipped = 0
    for d in s.datasets:
        edited = set(d.edited_fields or [])
        wrote = False
        for field in ("organism", "subtype"):
            val = getattr(s, field)
            if not val:
                continue
            if mode == "unedited" and field in edited:
                skipped += 1
                continue
            if getattr(d, field) != val:
                setattr(d, field, val)
                wrote = True
        if wrote:
            updated += 1
    db.commit()
    return {"updated": updated, "skipped_fields": skipped}


@router.patch("/sets/{set_id}", response_model=SetOut)
def patch_set(set_id: int, body: SetPatch, db: Session = Depends(get_db)):
    s = db.get(DatasetSet, set_id)
    if not s:
        raise HTTPException(404, "set not found")
    if body.experiment_id is not None and not db.get(Experiment, body.experiment_id):
        raise HTTPException(404, "target experiment not found")
    if body.propagate not in ("none", "all", "unedited"):
        raise HTTPException(400, "propagate must be one of: none, all, unedited")
    for field in ("name", "description", "organism", "subtype", "tags", "experiment_id"):
        val = getattr(body, field)
        if val is not None:
            setattr(s, field, val)
    # A set moved to another experiment takes its members along (set membership
    # implies experiment membership — same rule as dataset PATCH).
    if body.experiment_id is not None:
        e = db.get(Experiment, body.experiment_id)
        for d in s.datasets:
            d.experiment_id = e.id
            d.project_id = e.project_id
    db.commit()
    if body.propagate in ("all", "unedited"):
        _propagate_set_details(db, s, body.propagate)
    db.refresh(s)
    return _set_out(s)


@router.delete("/sets/{set_id}")
def delete_set(set_id: int, db: Session = Depends(get_db)):
    s = db.get(DatasetSet, set_id)
    if not s:
        raise HTTPException(404, "set not found")
    for d in s.datasets:  # unlink datasets, keep them
        d.set_id = None
    db.delete(s)
    db.commit()
    return {"deleted": set_id}


# ------------------------------------------------------------------------- tree
@router.get("/projects/{project_id}/tree")
def project_tree(project_id: int, db: Session = Depends(get_db)):
    """Full nested hierarchy for the Projects view."""
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")

    # One grouped pass over runs: per-dataset run count + "has a succeeded run"
    # (the green dot in the set view). Archived runs don't count.
    run_rows = db.execute(
        select(Run.dataset_id, Run.status, func.count())
        .where(Run.archived == False)  # noqa: E712
        .group_by(Run.dataset_id, Run.status)).all()
    run_count: dict[int, int] = {}
    has_success: set[int] = set()
    for did, status, n in run_rows:
        run_count[did] = run_count.get(did, 0) + n
        if status == "succeeded":
            has_success.add(did)

    def ds_node(d: Dataset) -> dict:
        return {"id": d.id, "name": d.name, "type": d.type,
                "subtype": d.subtype, "digit_id": d.digit_id,
                "unamputated": d.unamputated,
                "voxel_size_um": d.voxel_size_um, "slices": d.slices,
                "run_count": run_count.get(d.id, 0),
                "has_success": d.id in has_success,
                "tags": d.tags or []}

    experiments = []
    for e in sorted(p.experiments, key=lambda x: x.created_at):
        sets = [{"id": s.id, "name": s.name, "tags": s.tags or [],
                 "organism": s.organism, "subtype": s.subtype,
                 "description": s.description,
                 "datasets": [ds_node(d) for d in s.datasets]}
                for s in sorted(e.sets, key=lambda x: x.created_at)]
        direct = db.scalars(select(Dataset).where(
            Dataset.experiment_id == e.id, Dataset.set_id.is_(None))).all()
        analyses = [{"id": a.id, "title": a.title, "type": a.type}
                    for a in db.scalars(select(Analysis).where(
                        Analysis.experiment_id == e.id)).all()]
        experiments.append({"id": e.id, "name": e.name, "type": e.type,
                            "description": e.description,
                            "tags": e.tags or [], "sets": sets,
                            "datasets": [ds_node(d) for d in direct],
                            "analyses": analyses})
    # Datasets placed directly in the project, not (yet) in any experiment.
    proj_direct = db.scalars(select(Dataset).where(
        Dataset.project_id == p.id, Dataset.experiment_id.is_(None))).all()
    proj_analyses = [{"id": a.id, "title": a.title, "type": a.type}
                     for a in p.analyses if a.experiment_id is None]
    return {"id": p.id, "name": p.name, "project_lead": p.project_lead,
            "description": p.description,
            "tags": p.tags or [], "experiments": experiments,
            "datasets": [ds_node(d) for d in proj_direct],
            "analyses": proj_analyses}


# ------------------------------------------------------------------ stats/export
@router.get("/experiments/{experiment_id}/stats")
def experiment_stats(experiment_id: int, db: Session = Depends(get_db)):
    try:
        return stats_mod.experiment_stats(db, experiment_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/sets/compare")
def compare_sets(a: int, b: int, db: Session = Depends(get_db)):
    try:
        return stats_mod.compare_sets(db, a, b)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/experiments/{experiment_id}/export")
def export_experiment(experiment_id: int, include_masks: bool = False,
                      db: Session = Depends(get_db)):
    try:
        path = stats_mod.build_experiment_export(db, experiment_id, include_masks)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"path": str(path), "bytes": os.path.getsize(path),
            "download": f"/api/experiments/{experiment_id}/export/download?name={path.name}"}


@router.get("/experiments/{experiment_id}/export/download")
def download_export(experiment_id: int, name: str, db: Session = Depends(get_db)):
    exports = os.path.join(str(settings.results_root), "exports")
    safe = os.path.basename(name)
    path = os.path.join(exports, safe)
    if not os.path.isfile(path):
        raise HTTPException(404, "export not found (rebuild it)")
    return FileResponse(path, media_type="application/zip", filename=safe)
