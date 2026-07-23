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
        string  scanner
        float   voxel_size_um
        int     width
        int     height
        int     slices
        int     bit_depth
        string  study "group key (soft)"
        json    tags
        json    log "raw *_rec.log"
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

## 5. Proposed hierarchy (planned edits — not yet built)

The requested Project → Experiment → Set → File model, with Analysis nodes and
mixed dataset types (µCT, scRNA, spatial). Shown here so the target is concrete.

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

> **Note:** This section is the design target for the Projects/Experiments work
> (Tier 3). The exact tables should be finalized before implementation, since a
> wrong hierarchy is expensive to undo.
