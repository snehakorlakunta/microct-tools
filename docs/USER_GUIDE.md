# User Guide — microCT Segmentation Lab

A practical, click-by-click guide to using the app day to day. For what the tool
is and why, see [EXECUTIVE_SUMMARY.md](EXECUTIVE_SUMMARY.md); for the
processing/retraining details see [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md).

---

## 1. Starting the app

On the processing machine (see the main [README](../README.md) for install):

- Double-click **`run.bat`** (Windows) or run **`./run.sh`** (Linux/macOS), or run
  `microct-web` and `microct-worker` in two terminals.
- **On a fresh machine:** install **Python 3.12 (64-bit)** with *Add to PATH*, then
  double-click `run.bat` — it installs everything **offline** from the bundled
  `dependencies/` wheelhouse (no internet needed for the dashboard). Full details,
  and how to add the GPU segmentation engine, are in
  [DEPENDENCIES.md](../DEPENDENCIES.md).
- Open **http://localhost:8000** in a browser. To let colleagues on your network
  in, set `MICROCT_HOST=0.0.0.0` in `.env` and share `http://<your-ip>:8000`.

The **☰** button (top-left) collapses the left navigation to give the content more
room. The left nav has: **Overview, Datasets, Models, Runs, QC & Insights**.

---

## 2. First-time setup (once)

1. **Register the model** — *Models → Register model* → paste the path to the
   trained nnU-Net folder (the one that contains `plans.json`, `dataset.json`, and
   `fold_0 … fold_4`). The app reads its labels, training resolution, cross-
   validation Dice score, and a fingerprint of the exact weights, and records it as
   a versioned model (e.g. `v1`).
2. **Ingest datasets** — *Datasets → Ingest datasets*. The app scans your data
   folder, finds each reconstructed slice stack, reads its `*_rec.log` (voxel size,
   dimensions, scanner, kV, filter, date), makes a thumbnail, and lists it.

---

## 3. Finding the right scan (Datasets)

The Datasets page is a searchable catalog:

- **Search** by name, and **filter** by study or scanner.
- **Sort** by newest, name, voxel size, slice count, or scan date.
- Click a card to open its detail: full metadata from the scan log, editable
  **tags** and **notes**, a **flag** toggle, and the list of runs already done on
  it (so you can compare model versions on the same scan).

---

## 4. Running the model (New run)

- Click **＋ New run** (bottom-left) or *Compare/New run on this dataset* from a
  dataset's detail.
- Choose the **model & version**, tick one or more **datasets**, and set:
  - **Folds** — `0` (fast, single fold, recommended first pass) or `0 1 2 3 4`
    (5-fold ensemble, most accurate, ~5× slower).
  - **Device** — `auto` (uses the GPU if present), `cuda`, or `cpu`.
  - **Step** — sliding-window overlap; `0.5` default, `0.7` is faster with a small
    accuracy cost.
  - **TTA** — test-time augmentation; more accurate, slower.
- Click **Queue run(s)**. The job worker picks them up one at a time. Watch status
  on the **Runs** page (queued → running → succeeded).

> On a GPU this is minutes per scan; on a CPU expect ~1–2 hours for a large volume.

---

## 5. Reading a result (the Run report)

Open a run (from Runs, or a dataset's runs) to get a structured report. Layout:

- **Left:** the **Viewer** fills the height. **Right:** stacked cards — Summary,
  Segmentation metrics, Parameters, Run environment, QC & failure modes, Run log.
- Drag the **vertical divider** to resize the two columns; the right column scrolls
  on its own so the image stays in view.
- Each card has a **▾ collapse** and an **✕ hide**. The **Panels** bar (top) brings
  hidden ones back; **Reset layout** restores defaults. Your layout is remembered
  across reloads.

**Key numbers** (Summary + Segmentation metrics): ROI volume (mm³), ROI voxel
count, ROI in µm³, best slice, run duration, and the model's cross-validation Dice.
**Parameters** and **Run environment** record exactly how and where it ran (folds,
step, device, machine, CPU/GPU, RAM, versions, peak memory, per-phase timings).

Use **⭳ Export report** for a Markdown copy of the whole thing.

**While a run is in progress**, the report shows a **live progress bar** at the top:
the current phase (converting → loading model → segmenting → finalizing) with a
percentage driven by nnU-Net's sliding-window patches, plus elapsed time and a
rough ETA. It updates on its own and swaps to the finished result automatically.

**Per-slice mask BMPs** — every succeeded run has a **🖼 Mask BMPs** button at the
top of the report. The mask is saved as one 8-bit BMP per slice (white = ROI) in a
`<case>_mask_bmp/` folder next to the result, named to match your input slices 1:1.
New runs write this automatically; the button back-fills older runs and shows a ✓
with the slice count once present.

---

## 6. Using the viewer

The viewer shows the grey scan with the segmentation in **red** on top. Controls
live in the **left icon rail**:

- **▦ A C S ⬡** — view mode: Multiplanar (all three planes), Axial, Coronal,
  Sagittal, or 3D render.
- **＋ / − / ✋** — zoom in, zoom out, pan mode (then drag). **Ctrl+scroll** also
  zooms. Zooming in **turns pan on automatically**, so you can immediately drag to
  reposition instead of moving the crosshair; press **P** or **⟲** to toggle back.
- **α** — a vertical slider for the red mask's opacity.
- **▲ ◄ ► ▼** — rotate (shown in 3D; you can also drag to rotate, scroll to zoom).
- **⟲** — reset view (zoom, pan, rotation, mode).
- **⤢** — maximize the viewer to the whole window (**F** key; **Esc** to exit).
- **⭳** — download the full-resolution mask.

**Cine (movie) playback** — in a single plane (Axial/Coronal/Sagittal) a strip
appears under the image: **⏮ ▶ ⏭**, a **scrub slider** with a "slice i / n"
readout, and an **fps** selector to play through the stack.

Keyboard: **F** fullscreen, **Space** play/pause, **← / →** step slice,
**+ / − / 0** zoom in/out/reset, **P** pan.

> The viewer uses a downsampled copy for speed and to fit browser memory; the
> **full-resolution mask is unchanged** for measurement and download.

---

## 7. Reviewing quality & recording feedback (QC)

This is how your expertise gets captured for later model improvement.

On the run report, the **QC & failure modes** card lets you:

- Set an **outcome**: pass / minor / fail.
- Tag **failure modes** from the vocabulary — e.g. *false positive outside
  specimen, boundary leak, under-segmentation, artifact confusion, holder
  segmented*. Click as many as apply.
- Add a **note** (what went wrong / what to study).
- **⚑ Flag for retraining** to mark this case as a candidate for the next training
  set.

Everything you record here is stored on the run and aggregated on the
**QC & Insights** page, which shows how often each failure mode occurs and lists
all flagged cases — so later you can pull up "every case with a boundary leak" and
decide what to fix.

---

## 8. Comparing runs (model versions)

When you improve the model and re-run a scan, you get a second run alongside the
first. From a run report click **⇄ Compare dataset runs**, or tick rows on the
**Runs** page and **Compare selected**.

If two or more runs succeeded, the compare view opens a **linked comparison
viewer**: both results load as live viewers **side by side under one shared
toolbar** (view mode, zoom, ✋ pan, per-side mask opacity **α A / α B**, cine,
maximize). With **sync panes** on (the default) scrubbing or moving the crosshair
in one pane moves the other to the same place, so you always compare the *same*
location in `v1` and `v2`. A metric table with the **ROI % difference** sits below.
(Runs that haven't succeeded fall back to static previews.)

---

## 9. Where your files are

Nothing is hidden inside the app. Set in `.env`, your datasets, results, models,
and the registry database all live in normal folders on disk (outside the code),
so you can back them up by copying and update the app without touching your data.
