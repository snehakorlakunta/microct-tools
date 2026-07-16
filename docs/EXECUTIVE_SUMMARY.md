# Executive Summary — microCT Segmentation Lab

## The problem

A microCT scan is a 3D block of tiny cubes (voxels), each holding one number: how
much X-ray that spot absorbed (its density). To measure a structure of interest —
a bone graft, a tumour, a defect — someone has to go through that block and mark
which voxels belong to the structure and which don't. This is called
**segmentation**, and done by hand it is slow and subjective: a single specimen
can be hundreds of image slices (R2 in this project has **459**), taking hours of
painstaking tracing, and two people — or the same person on two different days —
will draw slightly different boundaries. That inconsistency makes it hard to
trust comparisons between samples.

## What this tool does

It turns that manual grind into an automated, consistent, and auditable workflow.

- A **trained AI model** (nnU-Net) segments each scan automatically — it learned
  what the structure looks like from 55 hand-labelled examples and now applies the
  *same* criterion to every new scan. On the R2 sample it labelled ~9.8 million
  voxels (an ROI volume of **0.63 mm³**) on its own.
- A **self-hosted dashboard** manages the whole lifecycle: it catalogs scans and
  their metadata, runs the model (with a **live progress bar**), stores results, and
  shows them in an **in-browser 3D/2D viewer** — no external software, no file
  wrangling. Two model versions of the same scan can be opened in a **synced
  side-by-side viewer** to confirm an improvement, and every result is also saved as
  a **per-slice image stack** for use in other tools.
- Every result is **traceable**: which model version (down to a fingerprint of the
  exact weights) produced it, on which machine, how long it took, and with what
  settings.
- A **review loop** lets the expert judge each result, tag *why* something is wrong
  (a vocabulary of failure modes), and flag cases for retraining — turning human
  feedback into the raw material for the next, better model.

## Why it matters

- **Speed** — minutes per scan on a GPU (about 110 minutes on a CPU) versus hours
  of manual work.
- **Consistency** — the model applies one learned standard to every scan, so
  measurements are comparable across R2, R4, and the next fifty samples.
- **Traceability** — results are permanently tied to the model version and hardware
  that produced them; re-running an old dataset with a newer model is one click and
  the results sit side by side for comparison.
- **Improvement over time** — the tool is built around a human-in-the-loop cycle:
  the model does the 459-slice grind; the expert supervises, corrects, and feeds
  those corrections back so the model keeps getting better. It is not "the machine
  replaced the expert" — it is "the expert now supervises at scale."

## Who uses it

- **Researchers / PIs** review results, compare samples, and make measurements.
- **Study processors** run scans through the model, QC the output, and record
  feedback.
- **Model maintainers** collect the flagged cases, retrain, and register improved
  model versions.

## The workflow at a glance

```
scan (raw slices)  ->  ingest (auto-read metadata)  ->  run the model
      ->  review the result in the viewer + tag any failure modes
      ->  flag weak cases  ->  correct their masks  ->  retrain
      ->  register model v2  ->  re-run datasets  ->  compare v1 vs v2
```

The rest of the documentation covers day-to-day use ([USER_GUIDE.md](USER_GUIDE.md))
and the processing/feedback/retraining details ([TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md)).
