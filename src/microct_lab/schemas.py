"""Pydantic schemas for API I/O."""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, PlainSerializer


def _as_utc(v: dt.datetime) -> str:
    """Serialize a timestamp as unambiguous UTC, ending in `Z`.

    Every timestamp in the registry is UTC — the models default to
    `datetime.utcnow()` — but that produces a NAIVE datetime, which pydantic then
    renders as "2026-07-16T03:51:46.017532" with no zone. JavaScript's Date
    parses a bare datetime like that as *local* time, so a browser silently
    shifts every displayed time by its UTC offset. Values already stored in
    SQLite are naive too, so this is fixed at serialization rather than by
    changing the column default: it corrects existing rows and new ones alike.
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


UtcDateTime = Annotated[dt.datetime, PlainSerializer(_as_utc, return_type=str)]


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
    archived: bool = False
    created_at: UtcDateTime


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    name: str
    slices_path: str
    pattern: str
    type: str = "uct"
    subtype: Optional[str] = None
    organism: Optional[str] = None
    digit_id: Optional[str] = None
    unamputated: bool = False
    edited_fields: list[str] = []
    crop_box: Optional[list[int]] = None
    project_id: Optional[int] = None
    set_id: Optional[int] = None
    experiment_id: Optional[int] = None
    nas_relpath: Optional[str] = None
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
    tags: list[str] = []
    thumbnail: Optional[str] = None
    size_bytes: Optional[int] = None
    notes: Optional[str] = None
    flagged: bool = False
    archived: bool = False
    created_at: UtcDateTime
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
    created_at: UtcDateTime
    started_at: Optional[UtcDateTime] = None
    ended_at: Optional[UtcDateTime] = None
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
    qc_tags: list[str] = []
    rating: Optional[int] = None
    flagged: bool = False
    archived: bool = False
    review_note: Optional[str] = None
    dataset_name: Optional[str] = None
    model_name: Optional[str] = None
    # Computed by the router (NOT stored): this run has been 'canceling' for
    # longer than MICROCT_STUCK_AFTER_SECONDS, which means no worker picked the
    # request up and it will never resolve on its own. The UI uses this to decide
    # when to offer POST /api/runs/{id}/cancel?force=true.
    stuck: bool = False


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    run_id: int
    dataset_id: int
    status: str
    pipeline_version: str = "digitpipe_v5"
    params: dict = {}
    error: Optional[str] = None
    created_at: UtcDateTime
    started_at: Optional[UtcDateTime] = None
    ended_at: Optional[UtcDateTime] = None
    duration_sec: Optional[float] = None
    socket_volume_voxels: Optional[float] = None
    socket_volume_mm3: Optional[float] = None
    socket_radius_voxels: Optional[float] = None
    socket_radius_mm: Optional[float] = None
    socket_centroid: Optional[list[float]] = None
    phalanx_volume_voxels: Optional[float] = None
    phalanx_volume_mm3: Optional[float] = None
    bone_length_voxels: Optional[float] = None
    bone_length_mm: Optional[float] = None
    euclidean_distance_voxels: Optional[float] = None
    euclidean_distance_mm: Optional[float] = None
    metrics: dict = {}
    output_dir: Optional[str] = None
    log_path: Optional[str] = None
    annotated_nii: Optional[str] = None
    xlsx_path: Optional[str] = None
    env: dict = {}
    host: Optional[str] = None
    notes: Optional[str] = None
    flagged: bool = False
    archived: bool = False
    # convenience, filled by the router
    dataset_name: Optional[str] = None
    run_status: Optional[str] = None
    # Computed by the router (NOT stored) — see RunOut.stuck. Offers the UI a cue
    # for POST /api/measurements/{id}/cancel?force=true.
    stuck: bool = False


class MeasurementCreate(BaseModel):
    run_ids: list[int]
    pipeline_version: str = "digitpipe_v5"
    skip_viz: bool = False
    # bvtv_thresh only: the bone threshold in Hounsfield units, converted
    # per-scan to a grey value from the scan's own _rec.log reconstruction
    # window (see bvtv.py). Ignored by digitpipe.
    threshold_hu: Optional[float] = None


class MeasurementPatch(BaseModel):
    notes: Optional[str] = None
    flagged: Optional[bool] = None
    archived: Optional[bool] = None


# ---- projects / experiments / sets / analyses ----

class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    project_lead: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = []
    archived: bool = False
    created_at: UtcDateTime
    experiment_count: int = 0
    dataset_count: int = 0
    analysis_count: int = 0


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    type: str = "uct"
    description: Optional[str] = None
    tags: list[str] = []
    created_at: UtcDateTime
    set_count: int = 0
    dataset_count: int = 0


class SetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    experiment_id: int
    name: str
    description: Optional[str] = None
    organism: Optional[str] = None
    subtype: Optional[str] = None
    tags: list[str] = []
    created_at: UtcDateTime
    dataset_count: int = 0


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: Optional[int] = None
    experiment_id: Optional[int] = None
    title: str
    type: Optional[str] = None
    description: Optional[str] = None
    files_relpath: Optional[str] = None
    dataset_ids: list[int] = []
    set_ids: list[int] = []
    run_ids: list[int] = []
    tags: list[str] = []
    data: Optional[dict] = None
    created_at: UtcDateTime


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


class ModelPatch(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: Optional[str] = None
    description: Optional[str] = None
    archived: Optional[bool] = None


class RunReview(BaseModel):
    qc_status: Optional[str] = None
    qc_tags: Optional[list[str]] = None
    rating: Optional[int] = None
    flagged: Optional[bool] = None
    review_note: Optional[str] = None


class ProjectIn(BaseModel):
    name: str
    project_lead: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    project_lead: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    archived: Optional[bool] = None


class ExperimentIn(BaseModel):
    project_id: int
    name: str
    type: str = "uct"
    description: Optional[str] = None
    tags: Optional[list[str]] = None


class ExperimentPatch(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    project_id: Optional[int] = None


class SetIn(BaseModel):
    experiment_id: int
    name: str
    description: Optional[str] = None
    organism: Optional[str] = None
    subtype: Optional[str] = None
    tags: Optional[list[str]] = None


class SetPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    organism: Optional[str] = None
    subtype: Optional[str] = None
    tags: Optional[list[str]] = None
    experiment_id: Optional[int] = None
    # How to push organism/subtype changes onto member datasets:
    #   "all"      — overwrite every member
    #   "unedited" — skip datasets whose edited_fields shows a manual value
    #   "none"     — change the set only (default)
    propagate: str = "none"


class AnalysisIn(BaseModel):
    title: str
    project_id: Optional[int] = None
    experiment_id: Optional[int] = None
    type: Optional[str] = None
    description: Optional[str] = None
    files_relpath: Optional[str] = None
    dataset_ids: Optional[list[int]] = None
    set_ids: Optional[list[int]] = None
    run_ids: Optional[list[int]] = None
    tags: Optional[list[str]] = None


class AnalysisPatch(BaseModel):
    title: Optional[str] = None
    project_id: Optional[int] = None
    experiment_id: Optional[int] = None
    type: Optional[str] = None
    description: Optional[str] = None
    files_relpath: Optional[str] = None
    dataset_ids: Optional[list[int]] = None
    set_ids: Optional[list[int]] = None
    run_ids: Optional[list[int]] = None
    tags: Optional[list[str]] = None


class DatasetPatch(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    organism: Optional[str] = None
    digit_id: Optional[str] = None
    unamputated: Optional[bool] = None
    crop_box: Optional[list[int]] = None
    project_id: Optional[int] = None
    set_id: Optional[int] = None
    experiment_id: Optional[int] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None
    flagged: Optional[bool] = None
    archived: Optional[bool] = None
    study: Optional[str] = None
    # Use a sentinel-free approach: absent = leave, present-null = clear membership
    clear_set: Optional[bool] = None
    clear_experiment: Optional[bool] = None
    clear_project: Optional[bool] = None
    clear_crop: Optional[bool] = None
    # Clear the digit identity / subtype explicitly (None in the fields above
    # means "leave unchanged", so clearing needs its own flag).
    clear_digit_id: Optional[bool] = None


class DatasetBulkRequest(BaseModel):
    """One request, many datasets: independent assignment to project /
    experiment / set, tag add/remove, and renames. Each dataset is settled
    individually so one failure cannot abandon the rest."""
    ids: list[int]
    project_id: Optional[int] = None
    experiment_id: Optional[int] = None
    set_id: Optional[int] = None
    clear_project: bool = False
    clear_experiment: bool = False
    clear_set: bool = False
    add_tags: list[str] = []
    remove_tags: list[str] = []
    # id -> new name. The CLIENT computes final names (regex/pattern preview
    # happens there), the server only applies + checks collisions — so what the
    # preview showed is exactly what lands.
    renames: dict[int, str] = {}


class DatasetBulkRowResult(BaseModel):
    id: int
    ok: bool
    error: Optional[str] = None
    name: Optional[str] = None


class DatasetBulkResult(BaseModel):
    attempted: int
    succeeded: int
    failed: int
    results: list[DatasetBulkRowResult]


# ---- BV/TV normalization analysis ----

class NormalizeBvtvRequest(BaseModel):
    """Compute normalized BV/TV over the digit datasets of a set or experiment.

    mode:
      per_leg   — each dataset ÷ the unamputated reference on the same leg
                  (side) of the same mouse
      per_mouse — ÷ mean of that mouse's references (L and R together)
      per_set   — ÷ mean of ALL references in the dataset's set
    reference_dataset_ids: the ticked "unamputated" datasets. Persisted onto the
    datasets' `unamputated` flag as a side effect so the choice sticks.
    """
    set_ids: list[int] = []
    experiment_id: Optional[int] = None
    mode: str = "per_leg"
    reference_dataset_ids: list[int] = []
    save: bool = False
    title: Optional[str] = None


class RunCropRequest(BaseModel):
    """Retro-crop a finished run's mask: voxels outside the box are zeroed.
    Box is [z0, z1, y0, y1, x0, x1], half-open, in the RUN's mask geometry."""
    box: list[int]


class SystemSettingsPatch(BaseModel):
    # How many segmentation runs the worker may execute at once (1..gpu_count).
    parallel_gpu_runs: Optional[int] = None
    # Bone threshold default for the interim BV/TV measurement, in HU.
    bvtv_threshold_hu: Optional[float] = None
