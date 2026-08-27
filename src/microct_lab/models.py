"""The registry: ORM tables for Projects, Experiments, Sets, Datasets, Models, Runs, Analyses."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


# Dataset modality types (the leaf "kind" of data). uCT is the historical default.
DATASET_TYPES = ("uct", "scrna", "spatial", "omics", "other")

# Categories WITHIN a modality. For uct: "digit" (terminal phalanx — the anatomy
# digitpipe_v5 measures) vs "ho" (heterotopic ossification). Kept free-form in
# the column so other modalities can grow their own subtypes without DDL.
UCT_SUBTYPES = ("digit", "ho")

# Digit identifiers for digit-uCT datasets: side (L/R) + digit number (2-4).
DIGIT_IDS = ("L2", "L3", "L4", "R2", "R3", "R4")


class AppSetting(Base):
    """Tiny key/value store for settings changed at runtime from the UI.

    Needed because the web server and the worker are separate processes: an
    in-memory Settings override on the server would never reach the worker, but
    both poll this table. Values are JSON so a setting can be a number, string
    or object. Env vars remain the *defaults*; a row here overrides them.
    """
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Project(Base):
    """Top-level container. Groups one or more experiments (and their analyses)."""
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # Who runs this project — free text, shown on the card, sortable/searchable.
    project_lead: Mapped[Optional[str]] = mapped_column(String(200), index=True, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    experiments: Mapped[list["Experiment"]] = relationship(
        back_populates="project", cascade="all, delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="project", cascade="all, delete-orphan")


class Experiment(Base):
    """A modality-typed grouping inside a project (e.g. Experiment_002 [uCT])."""
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # Modality this experiment holds: uct | scrna | spatial | omics | other
    type: Mapped[str] = mapped_column(String(32), default="uct", index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    project: Mapped["Project"] = relationship(back_populates="experiments")
    sets: Mapped[list["DatasetSet"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="experiment")


class DatasetSet(Base):
    """A named set/group of datasets inside an experiment (e.g. Set1 [R13 treated])."""
    __tablename__ = "dataset_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set-level details. Filled once per set, these autofill onto member
    # datasets (which stay individually editable — see routers/projects.py
    # propagation and Dataset.edited_fields).
    organism: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    subtype: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    experiment: Mapped["Experiment"] = relationship(back_populates="sets")
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="set")


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
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    runs: Mapped[list["Run"]] = relationship(back_populates="model")


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    slices_path: Mapped[str] = mapped_column(String(1024), unique=True)
    pattern: Mapped[str] = mapped_column(String(64), default="*rec*.bmp")

    # Modality + organism grouping for the browse taxonomy (uCT > Mouse > Set1 ...).
    type: Mapped[str] = mapped_column(String(32), default="uct", index=True)
    # Category within the modality: for uct, "digit" | "ho". Gates which
    # measurements make sense (digit length only for digit anatomy).
    subtype: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    organism: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)

    # Digit-uCT identity: which digit this scan is (L2..R4). Parsed from the
    # dataset name at ingest (see registry.parse_digit_id), always editable.
    digit_id: Mapped[Optional[str]] = mapped_column(String(8), index=True, nullable=True)
    # Marks a normalization *reference* digit (an unamputated control). Driven by
    # the checkbox in the analysis UI; persisted so the choice sticks.
    unamputated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Fields the user changed by hand (list of column names). Set-detail
    # propagation in "unedited only" mode and re-ingest both skip fields listed
    # here, so a manual correction is never silently overwritten.
    edited_fields: Mapped[list] = mapped_column(JSON, default=list)

    # Active 3D crop box in FULL-RES voxel coordinates [z0, z1, y0, y1, x0, x1]
    # (half-open, numpy-style). Snapshotted into Run.params at enqueue time, so
    # editing it later never changes what an existing run meant.
    crop_box: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Placement in the Project > Experiment > Set hierarchy (all nullable: an
    # ingested dataset starts unassigned and is organized later). project_id is
    # stored directly — not only derived through the experiment — so a dataset
    # can belong to a project before it has an experiment or set.
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True)
    set_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("dataset_sets.id"), index=True, nullable=True)
    experiment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("experiments.id"), index=True, nullable=True)

    # NAS-relative path (relative to settings.nas_base) so the record survives a
    # different drive-letter mapping of "Ultron" on another machine.
    nas_relpath: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

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
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    set: Mapped[Optional["DatasetSet"]] = relationship(back_populates="datasets")
    # NOTE: no delete-orphan cascade — runs are immutable records that must
    # survive their dataset (runs can only be archived, never deleted).
    runs: Mapped[list["Run"]] = relationship(back_populates="dataset")


class Analysis(Base):
    """An analysis artifact: R code + generated figures, comparing one or more
    datasets/sets. Lives in the shared Analyses folder; the record indexes it."""
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=True)
    experiment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("experiments.id"), index=True, nullable=True)

    title: Mapped[str] = mapped_column(String(300), index=True)
    type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # e.g. "R13 vs CTL"
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Folder (NAS-relative) that holds the R files + figure images for this analysis.
    files_relpath: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    # What this analysis compares (loose references by id — a prototype-friendly M2M).
    dataset_ids: Mapped[list] = mapped_column(JSON, default=list)
    set_ids: Mapped[list] = mapped_column(JSON, default=list)
    run_ids: Mapped[list] = mapped_column(JSON, default=list)

    # Computed results stored WITH the record (e.g. the BV/TV normalization
    # table), so a saved analysis renders without recomputing — and without
    # needing files on the NAS.
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)

    project: Mapped[Optional["Project"]] = relationship(back_populates="analyses")
    experiment: Mapped[Optional["Experiment"]] = relationship(back_populates="analyses")


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
    # Runs are never deleted — only archived (hidden from default views).
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    dataset: Mapped["Dataset"] = relationship(back_populates="runs")
    model: Mapped["Model"] = relationship(back_populates="runs")
    # NOTE: no delete-orphan cascade — mirrors Dataset.runs. A Measurement is an
    # immutable provenance record that must survive its Run (archive, never delete).
    measurements: Mapped[list["Measurement"]] = relationship(back_populates="run")


class Measurement(Base):
    """A morphometry measurement job: the perios digitpipe run over a finished
    segmentation. One Measurement per (Run, pipeline_version) attempt.

    Segmentation answers "which voxels are the digit"; this answers "how big is the
    socket, how long is the bone". It is CPU-only, so it can be worked by a separate
    `microct-measure-worker` alongside the GPU segmentation worker.
    """
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    # Denormalized from the Run so the common "all measurements for dataset X"
    # query needs no join.
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)

    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|succeeded|failed|canceled
    pipeline_version: Mapped[str] = mapped_column(String(64), default="digitpipe_v5", index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # --- headline metrics, promoted out of `metrics` for filtering/aggregation ---
    socket_volume_voxels: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    socket_volume_mm3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    socket_radius_voxels: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    socket_radius_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    socket_centroid: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # [i, j, k] voxels
    phalanx_volume_voxels: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    phalanx_volume_mm3: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bone_length_voxels: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bone_length_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    euclidean_distance_voxels: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    euclidean_distance_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # The full flat metric dict from <case>_measurement.json (hull, shrink-wrap,
    # major axis, BV/TV, line inside/outside, and anything else the pipeline emits).
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    output_dir: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    log_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    # The 7-class annotated label volume for NiiVue (1 bone, 2 socket, 3 line
    # outside bone, 4 line inside bone, 5 furthest point, 6 socket COM,
    # 7 first intersection).
    annotated_nii: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    xlsx_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    env: Mapped[dict] = mapped_column(JSON, default=dict)
    host: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)

    # --- QC ---
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    run: Mapped["Run"] = relationship(back_populates="measurements")
