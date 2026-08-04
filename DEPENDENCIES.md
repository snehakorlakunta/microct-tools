# Dependencies & offline install

This app ships with a **bundled wheelhouse** so a fresh machine can install the
dashboard with **no internet connection**. This document explains what is
bundled, what you must install yourself, and how to add the (large, GPU‑specific)
segmentation engine.

---

## 1. What you need on a new machine before `run.bat` works

Copy the whole USB folder to the new machine, then:

1. **Install Python 3.12 (64‑bit) for Windows** from python.org.
   - On the first installer screen, tick **“Add python.exe to PATH.”**
   - Python **3.12** is important: the bundled wheels are built for 3.12 / 64‑bit
     Windows. 3.10 or 3.11 will also work for the pure‑Python parts but a few
     compiled wheels (numpy, Pillow, pydantic‑core, …) are 3.12‑specific.
2. **Double‑click `run.bat`.**

That’s it. `run.bat` creates a local `.venv`, installs everything **offline** from
`.\dependencies`, starts the job worker + web server, and opens
`http://127.0.0.1:8000`.

You do **not** need Git, Docker, Visual C++ build tools, or an internet
connection to run the dashboard. (Git is only needed if you want to keep using
version control.)

> The dashboard (catalog, run tracking, result/BMP viewing, comparison) runs
> without PyTorch. PyTorch + nnU‑Net are only required to **execute new
> segmentations** — see section 3.

---

## 2. What is bundled (`.\dependencies`)

A pip “wheelhouse” — one `.whl` per package — for the **core dashboard**:

- Web/app: fastapi, uvicorn[standard] (h11, httptools, websockets, watchfiles,
  pyyaml, python‑dotenv, click, colorama, anyio, idna, starlette, h11),
  python‑multipart
- Data/DB: SQLAlchemy (+greenlet), pydantic (+pydantic‑core), pydantic‑settings
- Imaging: Pillow, numpy, SimpleITK
- Utility: psutil, natsort
- Build tools so `pip install -e .` works offline: pip, setuptools, wheel

Platform: **Windows x86‑64, CPython 3.12**. Total ≈ 50 MB.

`run.bat` installs from here automatically when the folder is present
(`pip install --no-index --find-links dependencies -e .`). If the folder is
missing, it falls back to installing from PyPI over the internet.

### Refresh / rebuild the wheelhouse

From the app folder, on a Windows + Python 3.12 machine with internet:

```
python -m pip download -d dependencies -r requirements-core.txt
```

To build it for a **different** target (e.g. another Python version or Linux),
add pip’s platform flags, e.g. for 64‑bit Windows + Python 3.11:

```
python -m pip download -d dependencies -r requirements-core.txt ^
  --only-binary=:all: --platform win_amd64 --python-version 3.11
```

---

## 3. The segmentation engine (PyTorch + nnU‑Net) — NOT bundled

PyTorch is large (hundreds of MB to several GB) and **hardware‑specific**: the
build must match the target machine’s GPU/CUDA (or be a CPU build). Bundling a
single copy would be wrong for most machines, so it is intentionally left out.

Install it **once** on the machine that will actually run segmentation:

### GPU machine (recommended — minutes per volume)

1. Pick the CUDA build of PyTorch for that machine’s CUDA from
   <https://pytorch.org/get-started/locally/>, e.g. for CUDA 12.1:
   ```
   pip install torch --index-url https://download.pytorch.org/whl/cu121
   ```
2. Then the segmentation extras:
   ```
   pip install nnunetv2 natsort
   ```
   (or, from the app folder, `pip install -e ".[seg]"` after step 1.)

### CPU‑only (works, but slow — hours per volume)

```
pip install torch nnunetv2 natsort
```

### Run a dataset on the GPU machine (e.g. R4) — results land on the USB

Once the engine is installed on the GPU box, run R4 (or any dataset) so its
outputs land straight in the USB `results/` folder:

- **Via the dashboard (recommended):** plug in the USB, start `run.bat`, go to
  **New run**, pick the model + the **R4** dataset, and start it. Because `.env`
  points `RESULTS_ROOT` at `...\USBFiles\results`, the mask, preview, ROI volume,
  environment debrief, **and the per-slice `R4_mask_bmp/` stack** are written there
  automatically, and the run is tracked with the live progress bar. Nothing extra
  to copy.
- **Standalone (no dashboard):** run the pipeline and point `--out` into the USB:
  ```
  python scripts\segment_microct.py --slices <path-to>\R4\R4 ^
    --model <trained-model-folder> --case R4 ^
    --out <USB>\USBFiles\results\R4 --folds 0 --device auto
  ```
  This writes `R4.nii.gz`, `R4_mask_bmp\`, preview and `R4_result.json` into the USB
  folder. (Use the dashboard afterwards if you also want it in the registry/UI.)

> Whichever machine runs R4, its registry paths are absolute — keep `.env`'s
> `RESULTS_ROOT` pointed at the USB `results` folder on that machine so everything
> stays together on the stick.

### Pre‑bundle the engine for offline install (optional)

On a machine that matches the target, download the engine wheels into a second
folder and copy it to the USB:

```
python -m pip download -d dependencies-seg torch nnunetv2 natsort
```

Then on the target: `pip install --no-index --find-links dependencies-seg torch nnunetv2 natsort`.
For a CUDA build, add `--index-url https://download.pytorch.org/whl/cu121` to the
`pip download` command so the right torch is fetched.

---

## 4. The morphometry engine (`[morph]`) — NOT bundled either

The **morphometry measurement** job type (socket volume/radius, phalanx volume,
bone length — the vendored `scripts/perios/digitpipe_v5` pipeline) is **CPU‑only**
and does not need torch, but it does need a scientific‑Python stack that is **not**
in the offline wheelhouse.

> ⚠️ **The bundled `.\dependencies` wheelhouse does NOT contain the `[morph]`
> packages.** On a fresh, offline machine the dashboard and segmentation will
> install fine, but **enqueuing a measurement will fail** until the wheelhouse is
> refreshed (or the packages are installed from PyPI). Refresh it before relying on
> the morphometry feature offline.

Install online:

```
pip install -e ".[morph]"
```

That pulls: `nibabel`, `scipy`, `scikit-image`, `scikit-learn`, `trimesh`,
`pandas`, `openpyxl`.

> `scikit-learn` is **required** — the vendored `digitpipe_v5/utils.py` imports
> `sklearn.decomposition.PCA` at module level, so the pipeline cannot even import
> without it. `trimesh` is currently unused by any vendored file.

### Refresh the offline wheelhouse to include morphometry

On a Windows + Python 3.12 machine with internet, from the app folder:

```
python -m pip download -d dependencies nibabel scipy scikit-image scikit-learn trimesh pandas openpyxl
```

(add them to `requirements-core.txt` first if you want the single
`pip download -r requirements-core.txt` command to cover them). Expect this to add
roughly **150–250 MB** to `.\dependencies` — scipy, scikit‑image and scikit‑learn
are large compiled wheels. Budget USB space accordingly.

### The anatomy gate (on by default)

`digitpipe_v5` is built for **mouse terminal phalanx** at ~4 µm and returns
plausible-but-meaningless numbers on anything else, so `POST /api/measurements`
refuses a run whose dataset is not tagged as that anatomy (HTTP 400, with a
message naming the dataset and the tag that unblocks it):

| Setting | Default | Meaning |
|---|---|---|
| `MICROCT_MORPH_REQUIRE_ANATOMY` | `true` | Enforce the gate. `false` allows any dataset. |
| `MICROCT_MORPH_ANATOMY_TAGS` | `phalanx` | Dataset tag(s) that count. Comma-separated, any one matches, case-insensitive. |

Both are reported by `GET /api/config` so the UI can name the required tag
instead of hardcoding it. `morphqc.py` still checks the resulting numbers against
the reference cohort afterwards — the gate is the earlier, harder stop.

### Run measurements on a separate CPU box

Segmentation wants the GPU; morphometry does not. Run one worker of each against
the same registry:

```
microct-worker --kind segmentation      :: on the GPU box
microct-measure-worker                  :: on any CPU box  (= microct-worker --kind measurement)
```

`microct-worker` with no arguments still does **both** kinds, as before.

### Licensing of the vendored pipeline

`scripts/perios/digitpipe_v5` is vendored from a colleague's repository on this
same project, and its author has confirmed its use here — nothing is blocked. The
upstream repo carries no license file, which only matters if the app is ever
redistributed beyond the project; see `scripts/perios/PROVENANCE.md`.

---

## 5. Summary

| Task | Needs internet? | Needs GPU stack? |
|------|-----------------|------------------|
| Run the dashboard, view/compare results, export BMPs | No (uses `.\dependencies`) | No |
| Run a **new** segmentation | Once, to install torch + nnU‑Net (unless pre‑bundled) | GPU recommended |
| Run a **morphometry measurement** | Once, to install `[morph]` — **not in the wheelhouse yet** | No (CPU‑only) |
