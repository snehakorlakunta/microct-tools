"""The registry: ORM tables for Models, Datasets, and Runs."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class Model(Base):
    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    # Versioning: `family` groups revisions of the "same" model; `version` is the
    # revision label (v1, v2, ...); `fingerprint` is a hash of plans/dataset/
    # checkpoint sizes so results can be tied to the exact model bits.
    family: Mapped[Optional[str]] = mapped_column(String(200), index=True, nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="v1")
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    path: Mapped[str] = mapped_column(String(1024))
    configuration: Mapped[str] = mapped_column(String(64), default="3d_fullres")
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[dict] = mapped_column(JSON, default=dict)
    training_spacing_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cross_val_dice: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_dataset: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    num_training_cases: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    runs: Mapped[list["Run"]] = relationship(back_populates="model")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slices_path: Mapped[str] = mapped_column(String(1024), unique=True)
    pattern: Mapped[str] = mapped_column(String(64), default="*rec*.bmp")

    scanner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    voxel_size_um: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    slices: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bit_depth: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_voltage_kv: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_current_ua: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filter: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scan_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    study: Mapped[Optional[str]] = mapped_column(String(200), index=True, nullable=True)

    tags: Mapped[list] = mapped_column(JSON, default=list)
    log: Mapped[dict] = mapped_column(JSON, default=dict)
    thumbnail: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    flagged: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    runs: Mapped[list["Run"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), index=True)

    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|succeeded|failed|canceled
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    device_used: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # Immutable snapshot of the model identity at run time — so results stay
    # traceable to the exact model+version even if the Model row later changes.
    model_version: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    model_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    roi_voxels: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    roi_mm3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    roi_um3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_slice: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Full environment debrief: host, OS, CPU, RAM, GPU, versions, peak memory, timings.
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    host: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    gpu: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    peak_ram_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    peak_gpu_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    torch_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    output_dir: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    input_nii: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    mask_nii: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    preview_png: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    log_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- QC / failure-mode tracking ---
    qc_status: Mapped[str] = mapped_column(String(16), default="unreviewed", index=True)  # unreviewed|pass|minor|fail
    qc_tags: Mapped[list] = mapped_column(JSON, default=list)               # failure-mode labels
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # user QC: 1..5
    flagged: Mapped[bool] = mapped_column(default=False)                    # flag for retraining
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    dataset: Mapped["Dataset"] = relationship(back_populates="runs")
    model: Mapped["Model"] = relationship(back_populates="runs")
