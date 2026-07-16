# microCT Segmentation Lab

A self-hosted, single-machine web app to **catalog microCT datasets, run nnU-Net
segmentation, track every run by model version, review results, and visualize
them in the browser** — with an **embedded NiiVue 3D/2D viewer** (no external
viewer, no DICOM conversion). Built for a lab: you and colleagues open one URL.

- **Code is a git repo** you can clone, `pip install`, and run.
- **Datasets, results, models, and the registry DB live *outside* the repo**,
  wired up through `.env`. Pull a new version of the app without touching data.
- **Registry = a single SQLite file** — no database server to run.
- **Viewer is built in** — the grayscale volume + segmentation overlay render on
  a WebGL canvas inside the run page (2D MPR + 3D). NiiVue is vendored for
  offline use.

---

## What segmentation is (in plain terms)

A microCT scan is a 3D block of tiny cubes (voxels); each one just holds a
brightness number — how much X-ray that spot absorbed. **Segmentation** is going
through that block and labelling every voxel as either "this is the structure I
care about" (the ROI) or "not." The result is a second block of the same size — a
stencil that's `1` where the structure is and `0` everywhere else. The red overlay
this app shows in the viewer *is* that stencil laid on the grey image.

Done by hand, that means tracing the outline slice by slice (R2 here has **459**
slices) in tools like ImageJ/Fiji or 3D Slicer — hours of work, and subjective:
two people draw slightly different boundaries, which makes samples hard to compare.

This app does it with a **trained neural network (nnU-Net)** that learned the
structure's visual fingerprint from 55 hand-labelled examples and now applies the
*same* criterion to every scan automatically — for R2 it labelled ~9.8 million
voxels (an ROI of 0.63 mm³) on its own. The win isn't just speed (minutes on a GPU
vs. hours by hand); it's **consistency** (one standard across every scan) and a
**human-in-the-loop improvement loop**: the model does the 459-slice grind, the
expert reviews it, tags what's wrong, and feeds corrections back so the next model
version is better. The machine doesn't replace the expert — it lets the expert
supervise at scale.

## Documentation

- **[Executive summary](docs/EXECUTIVE_SUMMARY.md)** — what it does and why, for PIs/stakeholders.
- **[User guide](docs/USER_GUIDE.md)** — click-by-click daily use (UI, viewer, QC, compare).
- **[Technical guide](docs/TECHNICAL_GUIDE.md)** — how segmentation works, and the full
  feedback → correct → retrain → register-new-version → re-run loop.
- **[Dependencies & offline install](DEPENDENCIES.md)** — what a fresh machine needs,
  the bundled wheelhouse, and how to add the GPU segmentation engine.

---

## What it does

- **Datasets** — scan a folder tree, auto-read each SkyScan `*_rec.log`
  (voxel size, dimensions, scanner, kV/µA, filter, date), make a thumbnail, and
  register it. Sort / search / filter to find the scan you want.
- **Models** — register trained nnU-Net folders; auto-reads labels, training
  spacing, cross-val Dice, training cases, and a **fingerprint of the weights**.
  Models are **versioned** (family + version), so improving a model just adds
  `v2` next to `v1`.
- **Runs** — queue a dataset + model → the worker runs segmentation → results
  (mask, ROI volume, preview) are stored and tracked. Every run records **which
  model + version + fingerprint** produced it, so results stay traceable and you
  can compare versions on the same dataset. In-progress runs show a **live
  progress bar** (phase + sliding-window percent + ETA). Each result also saves a
  **per-slice mask BMP stack** (`<case>_mask_bmp/`, one 8-bit BMP per slice, named
  1:1 to the input) automatically.
- **Viewer & comparison** — an embedded NiiVue viewer (mask overlaid in red, MPR /
  axial / coronal / sagittal / 3D, cine, pan/zoom, maximize). Two or more succeeded
  runs open a **linked comparison viewer**: side-by-side live panes under one shared
  toolbar with **synced** crosshair/scrub, so `v1` vs `v2` are always at the same
  location. Raw datasets can also be visualized on their own, before any run.
- **QC & failure modes** — on each result, set an outcome (pass / minor / fail),
  tag **failure modes** from a vocabulary (false-positive-outside, boundary
  leak, artifact confusion, …), add notes, and **flag for retraining**. The
  **Insights** page aggregates failure modes so you can pull up all similar cases
  later and decide what to fix or re-label.

---

## Architecture

```
FastAPI (API + serves the SPA)
  ├─ SQLite registry  (Models / Datasets / Runs)         <- one file, in STATE_DIR
  ├─ DB-backed job queue  ->  worker process             <- cross-platform, no Redis
  │      worker runs  scripts/segment_microct.py  on the GPU/CPU
  └─ web/  SPA  +  vendored NiiVue viewer (embedded)

External storage (configured in .env, NOT in the repo):
  DATA_ROOT   datasets (folders of *_rec*.bmp + *_rec.log)
  MODELS_ROOT trained nnU-Net model folders
  RESULTS_ROOT per-run outputs (mask.nii.gz, preview, log)
  STATE_DIR   registry.db + thumbnails
```

> **Why a DB-backed queue instead of RQ/Redis?** The processing machine is often
> Windows and has a single GPU. RQ needs `fork()` (Linux) and Redis has no
> official Windows build. A DB-backed queue + one worker process is
> cross-platform, dependency-free, and perfect for sequential GPU jobs. Swap in
> Celery later if you ever scale past one GPU.

---

## Install & run

```bash
git clone <your-remote> microct-seg-lab
cd microct-seg-lab
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .                                   # dashboard/registry/viewer
cp .env.example .env                               # then edit the paths in .env
```

To actually run segmentation on **this** machine (the GPU box), also install the
compute extra (install a CUDA build of torch first for GPU speed):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121   # or CPU: pip install torch
pip install -e ".[seg]"          # adds nnunetv2 + natsort
```

Start it (two processes):

```bash
microct-web        # http://127.0.0.1:8000
microct-worker     # in another terminal — runs queued jobs
```

Or just **double-click `run.bat`** (Windows) / **`./run.sh`** (Linux/macOS):
it creates the venv, installs, starts the worker + web server, and opens the
browser. Set `MICROCT_HOST=0.0.0.0` in `.env` to let colleagues reach it on your
LAN at `http://<your-ip>:8000`.

> **Fresh machine, no internet?** Install **Python 3.12 (64-bit)** and run
> `run.bat` — it installs the dashboard **offline** from the bundled
> `dependencies/` wheelhouse. See [DEPENDENCIES.md](DEPENDENCIES.md).

### First-time flow in the UI
1. **Models → Register model** → paste a trained model folder path.
2. **Datasets → Ingest datasets** → scans `MICROCT_DATA_ROOT`.
3. **New run** → pick model + dataset(s) → the worker processes them.
4. Open the run → inspect the overlay in the viewer → set QC / failure modes.

CLI equivalents (also in `scripts/`): `microct-register-model <path>`,
`microct-ingest [root]`, `microct-worker`, `microct-web`.

---

## Portable / USB deployment

The repo is self-contained. Copy the folder to a USB stick, plug it into the
target machine, and run `run.bat` / `run.sh`. Notes:

- **Offline-ready.** A **bundled wheelhouse** (`dependencies/`, Windows x64 /
  Python 3.12) ships on the USB, so `run.bat` installs the dashboard with **no
  internet** — you only need Python 3.12 on the target. See
  [DEPENDENCIES.md](DEPENDENCIES.md) to refresh it or build it for another platform.
- **The viewer is already offline** — NiiVue is vendored at
  `src/microct_lab/web/vendor/niivue.js` (falls back to CDN only if missing).
- **The dashboard is light and portable.** The heavy part is segmentation
  (`torch` + `nnunetv2`, multi-GB, CUDA-specific) — install `.[seg]` on the GPU
  machine that will do the compute. You can even run the dashboard from USB
  while pointing `RESULTS_ROOT`/`DATA_ROOT` at the machine's local disk.
- Keep your **data/results/models/DB on the target machine's disk** (via `.env`),
  not on the USB, so runs are fast and the stick stays just the code.

---

## Repo layout

```
microct-seg-lab/
├── pyproject.toml            # pip-installable; console scripts
├── requirements-core.txt     # dashboard deps (used to build the wheelhouse)
├── DEPENDENCIES.md           # offline install + GPU engine notes
├── dependencies/             # bundled wheelhouse (on the USB; git-ignored)
├── .env.example              # config: external data/results/models/DB paths
├── run.bat / run.sh          # one-click launchers (venv + offline install + serve)
├── Dockerfile / docker-compose.yml
├── scripts/                  # all runnable scripts, checked in
│   ├── segment_microct.py    # the GPU-ready segmentation pipeline (portable)
│   ├── bmp_stack_to_nifti.py # stack converter
│   ├── serve.py / run_worker.py / ingest_datasets.py / register_model.py
└── src/microct_lab/          # the installable package
    ├── config.py  database.py  models.py  schemas.py
    ├── logparse.py  modelmeta.py  registry.py  worker.py  cli.py
    ├── bmp_export.py          # mask → per-slice BMP stack
    ├── routers/   (system, models, datasets, runs)
    └── web/       index.html  styles.css  app.js  vendor/niivue.js
```

Data lives outside; `.gitignore` keeps `*.nii.gz`, `*.bmp`, `*.pth`, `*.db`,
`/data`, `/results`, `/models`, `/state` out of version control.

---

## Improving the model (the review loop)

As you review results and tag failure modes, the **Insights** page and the
**⚑ flag for retraining** action build your worklist. To improve the model:
correct the masks of flagged cases (in 3D Slicer), add those volume+label pairs
to the nnU-Net training set, retrain/fine-tune, register the new model as `v2`,
and re-run the same datasets — the registry keeps both versions' results side by
side so you can confirm the improvement. Gains are largest on *new* failure
modes (5–20 corrected examples usually move the needle); a model already at
Dice ~0.97 won't improve much on cases it already handles.
