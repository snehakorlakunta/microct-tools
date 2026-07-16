"""Pydantic schemas for API I/O."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    name: str
    family: Optional[str] = None
    version: str = "v1"
    fingerprint: Optional[str] = None
    path: str
    configuration: str
    labels: dict
    channels: dict
    training_spacing_mm: Optional[float] = None
    cross_val_dice: Optional[float] = None
    source_dataset: Optional[str] = None
    num_training_cases: Optional[int] = None
    description: Optional[str] = None
    created_at: dt.datetime


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    name: str
    slices_path: str
    pattern: str
    scanner: Optional[str] = None
    voxel_size_um: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    slices: Optional[int] = None
    bit_depth: Optional[int] = None
    source_voltage_kv: Optional[float] = None
    source_current_ua: Optional[float] = None
    filter: Optional[str] = None
    scan_date: Optional[str] = None
    study: Optional[str] = None
    tags: list = []
    thumbnail: Optional[str] = None
    size_bytes: Optional[int] = None
    notes: Optional[str] = None
    flagged: bool = False
    created_at: dt.datetime
    run_count: int = 0


class DatasetDetail(DatasetOut):
    log: dict = {}


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    dataset_id: int
    model_id: int
    status: str
    params: dict
    device_used: Optional[str] = None
    model_version: Optional[str] = None
    model_snapshot: dict = {}
    created_at: dt.datetime
    started_at: Optional[dt.datetime] = None
    ended_at: Optional[dt.datetime] = None
    duration_sec: Optional[float] = None
    roi_voxels: Optional[int] = None
    roi_mm3: Optional[float] = None
    roi_um3: Optional[float] = None
    best_slice: Optional[int] = None
    env: dict = {}
    host: Optional[str] = None
    gpu: Optional[str] = None
    peak_ram_mb: Optional[float] = None
    peak_gpu_mb: Optional[float] = None
    torch_version: Optional[str] = None
    error: Optional[str] = None
    qc_status: str = "unreviewed"
    qc_tags: list = []
    rating: Optional[int] = None
    flagged: bool = False
    review_note: Optional[str] = None
    dataset_name: Optional[str] = None
    model_name: Optional[str] = None


# ---- request bodies ----

class IngestRequest(BaseModel):
    root: Optional[str] = None          # defaults to MICROCT_DATA_ROOT


class RunCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    dataset_ids: list[int]
    model_id: int
    folds: str = "0"
    tta: bool = False
    step: float = 0.5
    device: str = "auto"
    spacing_mm: Optional[float] = None  # default: dataset voxel size, else 0.004


class RegisterModelRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    path: str
    name: Optional[str] = None
    family: Optional[str] = None
    version: Optional[str] = None


class RunReview(BaseModel):
    qc_status: Optional[str] = None
    qc_tags: Optional[list] = None
    rating: Optional[int] = None
    flagged: Optional[bool] = None
    review_note: Optional[str] = None
