# Technical Guide — processing, interpretation, and model improvement

For the person who runs studies through the model, interprets the results,
records feedback, and drives the improvement cycle that produces new model
versions. Assumes the [USER_GUIDE](USER_GUIDE.md) for basic app use.

---

## 1. How the segmentation actually works

**The data.** A microCT scan is a 3D grid of voxels; each voxel is a single
intensity (X-ray absorption ≈ density). Segmentation produces a second grid of the
same shape — a **label map** — where each voxel is `1` (ROI) or `0` (background).
The red overlay in the viewer is that label map.

**The model.** It is an **nnU-Net v2** convolutional neural network (a "U-Net"),
configuration `3d_fullres`. It was trained on **55** hand-labelled volumes at
**4 µm isotropic** resolution, single grayscale channel, with per-volume Z-score
intensity normalization. It did **not** get hand-written rules; it was optimized,
over many passes, to reproduce the human outlines, and in doing so it learned the
ROI's statistical fingerprint — intensity, texture, shape, and spatial context.
Its cross-validation **Dice score is 0.968** (Dice is overlap-with-truth on a 0–1
scale; >0.95 is very strong).

**Inference (how a new scan is segmented).** The volume is normalized, then a fixed
3D window (patch size 80×224×128) is **slid across the whole volume** with overlap;
for each patch the network outputs a per-voxel probability of "ROI," and the
overlapping predictions are blended. Voxels above the decision threshold become the
mask. Using all five folds ("ensemble") averages five models for a bit more
accuracy at ~5× the compute.

**Why resolution matters.** nnU-Net resamples every input to the training spacing
(4 µm). R2/R4 are already 4 µm, so there is **no resampling loss** — the single
biggest reason this model transfers cleanly to your scans.

**How you'd do it manually (for context).** Slice by slice in ImageJ/Fiji, 3D
Slicer, Dragonfly, or CTAn: threshold by density (fast but grabs look-alikes),
hand-paint outlines (accurate, tedious, subjective), or region-grow from a seed
(then fix leaks), interpolate between slices, clean specks, and check in 3D. Hours
per specimen and inconsistent between operators. The model replaces the grind and
applies one consistent criterion to every scan; the human role shifts to
**supervision and correction**.

---

## 2. Data & registry layout

Code lives in the repo; **data lives outside it**, wired via `.env`:

```
MICROCT_DATA_ROOT     datasets (each = a folder of *_rec*.bmp slices + *_rec.log)
MICROCT_MODELS_ROOT   trained nnU-Net model folders
MICROCT_RESULTS_ROOT  per-run outputs (one folder per run: mask, preview, log)
MICROCT_STATE_DIR     registry.db (SQLite) + thumbnails
```

The **registry** (SQLite) has three tables — **Models**, **Datasets**, **Runs** —
that tie everything together. A run records its dataset, its model **and version and
weight fingerprint** (a hash of plans/dataset/checkpoint sizes), parameters, ROI
metrics, the machine/hardware debrief, timings, and your QC. That snapshot is
immutable, so a result stays traceable to the exact model that made it even after
the model row changes.

Each successful run writes, in its results folder: `<case>_0000.nii.gz` (the
stacked input volume), `<case>.nii.gz` (the mask), a preview PNG, a per-run log,
and `<case>_result.json` (metrics + environment). The viewer also caches
downsampled `view_*` copies for display.

---

## 3. Processing studies

1. **Ingest** raw stacks (Datasets → Ingest). The app parses each `*_rec.log`; the
   voxel size it reads drives the run's spacing, so a mislabelled scan can't
   silently be segmented at the wrong scale.
2. **Run** (New run). First pass: single fold (`0`), `--device auto`. Reserve the
   5-fold ensemble + TTA for final numbers.
3. **Interpret** the report: ROI volume (mm³ / voxels / µm³), and — critically —
   **look at the overlay**. Zoom into the ROI boundary; check for the common
   failure modes below. The Dice number is the *training* score, not a guarantee on
   a new scan.

The compute itself is the `scripts/segment_microct.py` pipeline (stack → NIfTI →
nnU-Net → mask + volume + preview + **per-slice mask BMPs** + environment). The
worker calls it per queued run; it is also usable standalone on any machine.

**Live progress.** The pipeline logs each phase; `GET /api/runs/{id}/progress`
parses that log into `{phase, percent, determinate, detail, elapsed_sec, eta_sec}`.
The unit under the percentage is nnU-Net's **sliding-window patch count** (patches
done / total) during inference — the phase that dominates runtime — mapped onto an
overall 0–100% across convert (2–15%), model load (15%), inference (15–90%), and
finalize (90–100%). When the patch count isn't yet parseable the bar is
indeterminate but still names the phase. The report polls this every 3 s.

**Per-slice mask BMPs.** Every run also writes `<case>_mask_bmp/` — one 8-bit BMP
per Z-slice (255 = ROI), filenames mirroring the source stack 1:1 — via
`src/microct_lab/bmp_export.py`. New runs generate it automatically; older runs can
be back-filled with `POST /api/runs/{id}/export_bmp` (the report's **🖼 Mask BMPs**
button), and `GET /api/runs/{id}/bmp_status` reports whether it exists.

**Stopping & deleting runs.** `POST /api/runs/{id}/cancel` cancels a queued run
immediately; for a running run it sets status `canceling`. The worker runs the
segmentation as a `Popen` child and polls the DB every ~2 s — on seeing `canceling`
it kills the process tree (psutil) and finalizes the run as `canceled` (a crash
mid-run is likewise recovered to `canceled`/`failed` on worker restart).
`DELETE /api/runs/{id}?purge=true` removes the registry record and, when purge is
set, the run's `output_dir` from disk; in-flight runs must be stopped first.

---

## 4. Interpreting results & recording feedback

Judge the mask against the grey image and tag what you see. The failure-mode
vocabulary (extend it in `src/microct_lab/routers/runs.py → FAILURE_MODES`):

| Failure mode | What it looks like | Usual cause |
|---|---|---|
| False positive outside specimen | red blobs off the sample | wider FOV than training crops |
| Boundary leak / bleed | mask spills past the ROI edge | low edge contrast |
| Under-segmentation | part of the ROI missed | faint/thin regions |
| Over-segmentation | non-ROI tissue included | look-alike density |
| Fragmented | ROI split into pieces | noise / gaps |
| Artifact confusion | ring/beam-hardening segmented | reconstruction artifacts |
| Holder / mounting | sample holder segmented | holder in FOV |

Record, per run: **outcome** (pass/minor/fail), the **failure-mode tags**, a
**note**, and **⚑ flag for retraining** if the case should join the next training
set. The **QC & Insights** page aggregates these across all runs — counts per
failure mode and the flagged list — so you can find similar cases and prioritise.

> Rule of thumb: don't re-label cases the model already gets right (it's near the
> ceiling there). Spend effort on *new failure modes* — that's where retraining
> pays off. And if a failure is caused by bad input (artifacts, wrong resolution),
> fix the pipeline first; retraining can't fix garbage in.

---

## 5. The improvement loop (feedback → new model version)

This is the core cycle the whole tool is built around.

**Step 1 — Select cases.** On QC & Insights, take the **flagged** runs (and use the
failure-mode filters to gather similar cases). These are your improvement targets.

**Step 2 — Correct the masks (ground truth).** Correcting an existing mask is far
faster than annotating from scratch:
- Open `<case>_0000.nii.gz` (image) and `<case>.nii.gz` (mask) in **3D Slicer**
  (Segment Editor) or MONAI Label.
- Paint/erase until the mask is correct. Save the corrected label.

**Step 3 — Assemble an nnU-Net training dataset.** Add each corrected
image+label pair to the nnU-Net raw dataset for the model's dataset ID (here 501):

```
nnUNet_raw/Dataset501_Glioblastoma/
  imagesTr/  <case>_0000.nii.gz      # the grayscale volume (channel 0000)
  labelsTr/  <case>.nii.gz           # the corrected label (0 background, 1 ROI)
  dataset.json                        # update "numTraining"
```

Keep the existing 55 and **add** the new corrected cases. Same 4 µm spacing, same
label scheme.

**Step 4 — Retrain (or fine-tune).**

```bash
nnUNetv2_plan_and_preprocess -d 501 --verify_dataset_integrity
# retrain all folds (or a single fold to start):
nnUNetv2_train 501 3d_fullres 0
# ... folds 1..4, or use pretrained weights to fine-tune from the current model:
#   nnUNetv2_train 501 3d_fullres 0 -pretrained_weights /path/to/checkpoint_final.pth
```

Fine-tuning from the current checkpoint is usually faster and enough when you're
closing specific gaps; full retraining is cleaner when you've added many cases.
Adding even **5–20** well-corrected examples of a *specific* failure mode typically
yields a clear, reliable gain on that mode — nnU-Net is data-efficient. The global
Dice may barely move (it's already high); success is measured **per failure mode**.

**Step 5 — Validate.** Compare the new model's cross-validation summary, and — the
real test — run it on held-out scans and eyeball the previously-failing cases.

**Step 6 — Register the new version.** *Models → Register model* → point at the new
trained folder. The app auto-assigns the next version (`v2`), reads its Dice, and
computes a new fingerprint. `v1` and `v2` now coexist.

**Step 7 — Re-run & compare.** Start runs of the same datasets with `v2`. Each new
run sits beside the old one; use **Compare** to confirm the ROI/boundary improved
where it used to fail. If it regressed elsewhere, that's visible too.

```
flagged runs -> correct masks (Slicer) -> add to nnUNet_raw -> retrain/fine-tune
   -> validate -> register v2 -> re-run datasets -> compare v1 vs v2 -> adopt v2
```

Because every run is pinned to a model fingerprint, this history is fully
auditable: you can always answer "which model produced this number, and is the
newer one actually better?"

---

## 6. Moving to another machine / GPU

The dashboard is light and portable; the **compute** (torch + nnU-Net) is heavy and
GPU/CUDA-specific. On the target machine install `pip install -e ".[seg]"` plus a
CUDA build of torch, point `.env` at the data, and runs go from ~hours (CPU) to
minutes (GPU). The registry and results are just files — copy them along, or start
fresh and re-ingest. Model versions and their fingerprints travel with the model
folders, so results stay traceable across machines.

**Offline install of the dashboard.** The USB carries a `dependencies/` wheelhouse
(Windows x64 / Python 3.12) covering everything the dashboard needs; `run.bat`
installs from it with `--no-index --find-links dependencies` when present, so a
fresh Windows machine only needs Python 3.12 and no internet. The segmentation
engine is intentionally *not* bundled (multi-GB, CUDA-specific) — install it on the
GPU box, or pre-bundle a matching `dependencies-seg/` folder. Full recipes,
including cross-platform `pip download` flags, are in
[DEPENDENCIES.md](../DEPENDENCIES.md).

> Note: the wheelhouse and results paths in the seeded registry are **absolute**;
> when you move the USB to a different drive letter or machine, re-point `.env` and
> (if needed) re-ingest so stored paths resolve locally.
