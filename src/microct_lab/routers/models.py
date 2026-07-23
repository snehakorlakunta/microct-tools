"""Model registry endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Model
from ..modelmeta import available_folds
from ..registry import register_model
from ..schemas import ModelOut, ModelPatch, RegisterModelRequest

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
def list_models(include_archived: bool = False, db: Session = Depends(get_db)):
    stmt = select(Model).order_by(Model.family, Model.version)
    if not include_archived:
        stmt = stmt.where(Model.archived == False)  # noqa: E712
    return db.scalars(stmt).all()


@router.post("/register", response_model=ModelOut)
def register(req: RegisterModelRequest, db: Session = Depends(get_db)):
    try:
        return register_model(db, req.path, req.name, req.family, req.version)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))


@router.get("/{model_id}", response_model=ModelOut)
def get_model(model_id: int, db: Session = Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    return m


@router.get("/{model_id}/folds")
def model_folds(model_id: int, db: Session = Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    return {"folds": available_folds(m.path)}


@router.patch("/{model_id}", response_model=ModelOut)
def patch_model(model_id: int, body: ModelPatch, db: Session = Depends(get_db)):
    """Rename a model / edit its description (display name is independent of the
    folder name) or archive it. Names are unique across the registry."""
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    if body.name is not None and body.name != m.name:
        clash = db.scalar(select(Model).where(Model.name == body.name, Model.id != m.id))
        if clash:
            raise HTTPException(409, "a model with that name already exists")
        m.name = body.name
    if body.description is not None:
        m.description = body.description
    if body.archived is not None:
        m.archived = body.archived
    db.commit()
    db.refresh(m)
    return m


@router.delete("/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    """Delete a model with no runs; a model that has runs is archived instead so
    its runs stay traceable to the exact model revision."""
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    if m.runs:
        m.archived = True
        db.commit()
        return {"archived": model_id, "reason": "model has runs (kept for provenance)"}
    db.delete(m)
    db.commit()
    return {"deleted": model_id}
