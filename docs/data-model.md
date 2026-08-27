# microCT Segmentation Lab — Data Model & Organization

A visual reference for how **Models**, **Datasets**, and **Runs** relate, how a run
moves through its lifecycle, and how the pieces sit on disk. Diagrams reflect the
**current** schema in `src/microct_lab/models.py`. A final section sketches the
**proposed** Project/Experiment hierarchy from the planned edits.

---

## 1. Entity relationships (current)

Three core tables. A **Run** ties one **Dataset** to one **Model** and records the
result plus a full hardware/environment debrief.

```mermaid
erDiagram
    MODEL ||--o{ RUN : "produces"
    DATASET ||--o{ RUN : "is input to"

    MODEL {
        int     id PK
        string  name UK "display name"
        string  family "groups versions"
        string  version "v1, v2, ..."
        string  fingerprint "hash of weights"
        string  path "model folder"
        string  configuration "3d_fullres"
        json    labels
        json    channels
        float   training_spacing_mm
        float   cross_val_dice
        string  source_dataset
        int     num_training_cases
        datetime created_at
    }

    DATASET {
        int     id PK
        string  name
        string  slices_path UK "folder of *_rec*.bmp"
        string  pattern "*rec*.bmp"
        string  subtype "uct: digit | ho"
        string  digit_id "L2..R4, parsed from name"
        bool    unamputated "normalization reference"
        json    edited_fields "manual edits, never clobbered"
        json    crop_box "z0 z1 y0 y1 x0 x1 full-res voxels"
        int     project_id FK "direct membership"
        string  scanner
        float   voxel_size_um
        int     width
        int     height
        int     slices
        int     bit_depth
        string  study "group key (soft)"
        json    tags
        json    log "raw *_rec.log (or _rec_Tra/Cor/Sag fallback)"
        string  thumbnail
        int     size_bytes
        bool    flagged
        datetime created_at
    }

    RUN {
        int     id PK
        int     dataset_id FK
        int     model_id FK
        string  status "queued|running|succeeded|failed|canceled"
        json    params "folds, tta, step, device, spacing"
        string  device_used
        string  model_version "snapshot at run time"
        json    model_snapshot "immutable model identity"
        float   duration_sec
        int     roi_voxels
        float   roi_mm3
        json    env "host/CPU/GPU/versions/peak mem"
        string  host
        string  gpu
        string  output_dir
        string  mask_nii
        string  preview_png
        string  qc_status "unreviewed|pass|minor|fail"
        json    qc_tags "failure modes"
        int     rating "1..5"
        bool    flagged "flag for retraining"
    }
```

**Key points**

- **Model versioning is half-built already:** `family` groups revisions, `version`
  labels them (`v1`, `v2`), and `fingerprint` (a hash of the weights) lets the same
  model bits be recognized across re-registration.
- Each Run stores an **immutable `model_snapshot`** so results stay traceable to the
  exact model + version even if the Model row is later edited.
- `Dataset.study` and `Dataset.tags` are the only current grouping mechanisms — flat,
  not a real hierarchy (this is what the proposed Projects layer replaces).
- Deleting a Dataset **cascades** to its Runs (`cascade="all, delete-orphan"`).

---

## 2. Run lifecycle (state machine)

The worker polls the DB, claims the oldest `queued` run, executes it, and writes a
terminal status. A cancel request flips a running job to `canceling`; the worker
kills the subprocess within a couple of seconds.

```mermaid
stateDiagram-v2
    [*] --> queued : POST /api/runs (enqueue)
    queued --> running : worker claims oldest queued
    queued --> canceled : cancel while still queued

    running --> succeeded : exit 0 + result.json
    running --> failed : non-zero exit / crash
    running --> canceling : POST /{id}/cancel

    canceling --> canceled : worker kills process tree

    succeeded --> [*]
    failed --> [*]
    canceled --> [*]

    note right of running
        Progress parsed live from run.log:
        converting -> loading -> predicting -> finalizing
    end note
```

---

## 3. System architecture (request → result)

```mermaid
flowchart LR
    subgraph Browser
        UI["Web SPA (app.js)<br/>NiiVue viewer"]
    end

    subgraph Server["FastAPI (main.py)"]
        R1["/api/datasets"]
        R2["/api/models"]
        R3["/api/runs"]
        R4["/api/system"]
        REG["registry.py<br/>(service layer)"]
    end

    DB[("SQLite registry.db<br/>Models · Datasets · Runs")]

    subgraph Worker["worker.py (separate process)"]
        POLL["poll queued runs"]
        SEG["segment_microct.py<br/>(nnU-Net subprocess)"]
    end

    FS[("Filesystem roots<br/>data · models · results · state")]

    UI -->|HTTP JSON| R1 & R2 & R3 & R4
    R1 & R2 & R3 --> REG
    REG --> DB
    R4 --> DB
    POLL <-->|claim / update status| DB
    POLL --> SEG
    SEG -->|masks, preview, logs| FS
    REG -->|ingest scans, register models| FS
    UI -->|view_mask.nii.gz / preview.png| R3
    R3 -->|FileResponse| FS
```

- The **web server** and the **worker** are independent processes that coordinate
  only through the SQLite DB — no Redis/broker.
- Large microCT volumes are **downsampled** server-side (`view_*.nii.gz`) before the
  browser loads them into WebGL.

---

## 4. On-disk / storage layout

Everything data-related lives **outside the git repo**, configured via `.env`
(`config.py`). Paths shown are the current Windows values.

```mermaid
flowchart TD
    ROOT["USBFiles/ (.env roots)"]

    ROOT --> DATA["datasets/<br/>MICROCT_DATA_ROOT"]
    ROOT --> MODELS["models/<br/>MICROCT_MODELS_ROOT"]
    ROOT --> RESULTS["results/<br/>MICROCT_RESULTS_ROOT"]
    ROOT --> STATE["state/<br/>MICROCT_STATE_DIR"]

    DATA --> DS1["scan_folder/<br/>*_rec*.bmp + *_rec.log"]
    MODELS --> M1["nnUNet model folder/<br/>plans + checkpoints"]
    RESULTS --> RUN1["{dataset}__{model}__run{id}/<br/>mask.nii.gz · preview.png · run.log · mask_bmp/"]
    STATE --> DBF["registry.db"]
    STATE --> THUMB["thumbnails/"]
    STATE --> CACHE["cache/ (downsampled views)"]
```

- **Ingest** walks `MICROCT_DATA_ROOT` for `*_rec.log` files → creates Dataset rows.
- **Run outputs** are named `{dataset}__{model}__run{id}` under the results root.
- The **registry DB + thumbnails + view cache** live under the state dir.

---

## 5. Project hierarchy (implemented on branch `feature/projects-and-ux`)

The requested Project → Experiment → Set → Dataset model, with Analysis nodes and
mixed dataset types (µCT, scRNA, spatial). These tables now exist in
`models.py` (`Project`, `Experiment`, `DatasetSet`, `Analysis`); `Dataset` gained
`type`, `organism`, `set_id`, `experiment_id`, `nas_relpath`, `archived`; `Run` and
`Model` gained `archived`.

```mermaid
erDiagram
    PROJECT   ||--o{ EXPERIMENT : contains
    EXPERIMENT ||--o{ DATASET_SET : contains
    EXPERIMENT ||--o{ ANALYSIS : contains
    DATASET_SET ||--o{ DATASET : groups
    DATASET   ||--o{ RUN : "is input to"
    MODEL     ||--o{ RUN : produces
    ANALYSIS  }o--o{ DATASET_SET : compares

    PROJECT {
        int id PK
        string name
        json tags
    }
    EXPERIMENT {
        int id PK
        int project_id FK
        string type "scRNA|spatial|uCT"
    }
    DATASET_SET {
        int id PK
        int experiment_id FK
        string name "e.g. Set1 [R13 treated]"
    }
    DATASET {
        int id PK
        int set_id FK "nullable"
        string type "uCT|scRNA|spatial"
        json tags
    }
    ANALYSIS {
        int id PK
        int experiment_id FK
        string title "e.g. R13 vs CTL"
        string files_path "Ultron/Analyses/..."
    }
    RUN {
        int id PK
        string status
        bool archived "runs archived, never deleted"
    }
    MODEL {
        int id PK
        string family
        string version
    }
```

```mermaid
flowchart TD
    P1["Project_1"]
    P1 --> E1["Experiment_001 [scRNA]"]
    P1 --> E2["Experiment_002 [uCT]"]
    E1 --> D1["Dataset [BT Day 0]"]
    E1 --> D2["Dataset [BT Day 7]"]
    E1 --> A1["Analysis [R code + figures]"]
    E2 --> S1["Set1 [R13 treated] (1,2,3)"]
    E2 --> S2["Set2 [CTL treated] (1,3,4)"]
    E2 --> S3["Set3 [R13+NR treated] (1,2,3)"]
    E2 --> A2["Analysis [R13 vs CTL: S1 vs S2]"]
    E2 --> A3["Analysis [R13+NR vs CTL: S3 vs S2]"]
```

> **Note:** All hierarchy FKs are nullable — an ingested dataset starts
> unassigned and is organized into a set/experiment later. Datasets and runs are
> never destroyed by deleting a project, experiment, or set: those operations only
> unlink (null the FK). Runs can be archived but never deleted.


---

## 6. Additions from the Linux-review build (2026-08-26)

- **Dataset category**: `Dataset.subtype` distinguishes **uct digit** from
  **uct ho**. The digit-morphometry gate now keys on it (legacy `phalanx` tag
  still honored; `init_db` backfills subtype from that tag). `digit_id`
  (L2..R4) is parsed from the name at ingest (`registry.parse_digit_id`) and
  `mouse_key()` groups same-animal scans by the name minus the digit token.
- **Independent hierarchy**: `Dataset.project_id` exists alongside
  `experiment_id`/`set_id`. Assigning a set fills experiment+project;
  assigning an experiment fills project; clears cascade downward only.
- **Set details**: `DatasetSet.organism/subtype` autofill members on
  assignment and can be propagated on set edit — `all` or `unedited`
  (skipping any field in `Dataset.edited_fields`, the manual-edit memory that
  re-ingest also respects).
- **app_settings**: tiny key/value table for runtime settings shared by the
  server and worker processes (`parallel_gpu_runs`, `bvtv_threshold_hu`).
- **Parallel worker**: up to `parallel_gpu_runs` segmentation subprocesses at
  once, each pinned via `CUDA_VISIBLE_DEVICES` (slot -> GPU); measurements run
  in their own thread. Limit re-read every loop, clamped to detected GPUs.
- **Interim BV/TV**: `Measurement` rows with `pipeline_version=bvtv_thresh_v1`
  are executed by `scripts/measure_bvtv.py` (threshold within the mask). The
  HU threshold is converted per scan through the `_rec.log` CS window
  (`bvtv.py`); the whole conversion is frozen into `Measurement.params`.
- **Normalization analyses**: `Analysis.data` stores the computed
  normalized-BV/TV table (`type=bvtv_normalization`).
- **Crop**: `Dataset.crop_box` is applied before inference (snapshotted into
  `Run.params.crop_box` at enqueue); `POST /runs/{id}/crop` retro-crops a
  finished mask (original path kept in `params.mask_nii_original`). BMP
  export has two modes: `mask` and `masked_image` (original pixels inside the
  mask, black elsewhere), in separate folders.
