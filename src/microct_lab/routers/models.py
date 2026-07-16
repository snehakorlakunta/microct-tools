"""Model registry endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Model
from ..modelmeta import available_folds
from ..registry import register_model
from ..schemas import ModelOut, RegisterModelRequest

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
def list_models(db: Session = Depends(get_db)):
    return db.scalars(select(Model).order_by(Model.family, Model.version)).all()


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


@router.delete("/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    m = db.get(Model, model_id)
    if not m:
        raise HTTPException(404, "model not found")
    if m.runs:
        raise HTTPException(409, "model has runs; delete those first")
    db.delete(m)
    db.commit()
    return {"deleted": model_id}
