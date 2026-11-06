---
title: BV/TV & Digit Length Guide
audience: scientist
---

# BV/TV & Digit Length Guide

BV/TV and digit length are measured with **LeviDigit**, the official method.
A measurement runs as its own job over a finished run's segmentation mask. This
guide covers what it reports, how to start one, and how to read the result.

---

## What it measures

| Metric | Definition |
|---|---|
| **TV** (total volume) | voxels inside the segmentation mask, in voxels and mm³ |
| **BV** (bone volume) | mask voxels whose grey value is **at or above the bone threshold** |
| **BV/TV** | BV ÷ TV, shown as a percent |
| **Digit length** | the straight line from the centroid of the digit's largest concavity to its distal endpoint, counting only the parts of that line that lie inside the mask. In mm and µm |

The bone threshold is a **grey value on the 0–255 scale, inclusive**. It is a
fixed grey threshold: it is not converted from Hounsfield units.

**Use your lab's threshold.** The box starts at the server's default, which the
administrator sets to the lab's value (`MICROCT_LEVIDIGIT_BONE_THRESHOLD`). If
it has never been set it is **60**, the example from LeviDigit's own
instructions, which is not a validated lab value. If the box does not show your
lab's value, type it in and tell your administrator. Every measurement records
the threshold it used, so changing the default later never changes a past
result.

## Starting a measurement

1. Open a **succeeded** run. Only a finished segmentation can be measured.
2. In the **BV/TV & digit length** panel, check the bone threshold and click
   **Measure BV/TV + digit length**.
3. A **measurement worker** must be running (`microct-worker` or
   `microct-measure-worker`), or the job stays at *queued*.

The panel follows the job while it runs. Without figures it finishes in
seconds; with them it takes longer. **Skip figures** turns off the publication
figure.

To re-measure, for example at another threshold, use **Measure again**. Earlier
measurements of the run are kept in the registry.

## Digit datasets vs everything else

Digit length is only meaningful on a digit. On a dataset that is **not
categorized as uct digit** (a uct ho scan, say), the measurement runs **BV/TV
only** and says so. To include digit length, set the dataset's subtype to
`digit` in the dataset drawer.

## Requirements LeviDigit enforces

LeviDigit refuses, rather than guesses, when its input is outside what its
numbers are defined for. The measurement then fails with LeviDigit's own
message. It requires:

- **4 µm isotropic voxels** (0.004 mm, within 1%)
- a **0–255** grayscale volume
- a **binary** mask with the same shape and geometry as the volume

## Reading the result

- **BV/TV** with BV and TV in mm³ and the threshold it used.
- **Digit length** in mm and µm.
- **No digit length** when LeviDigit could not find a defensible measurement
  on the mask. **This is not a length of zero.** Check the segmentation (for
  example with ✂ Crop mask), then measure again.
- **Details** lists voxel counts, the counted fraction of the length line, the
  concavity volume and the landmark coordinates.
- **⭳ CSV** downloads LeviDigit's measurements table for the run.

## Normalization and statistics

**🧮 Analyze** (BV/TV normalization) and the measurement statistics use
**LeviDigit values only**. A dataset without a LeviDigit measurement shows
*no BV/TV* rather than borrowing a value from another method.

## The older in-app methods (SK)

Before LeviDigit, the app had its own interim threshold BV/TV (an 800 HU
threshold converted per scan) and the perios `digitpipe_v5` morphometry
(socket, bone length). Those measurements are **kept** on the server, tagged
**SK**, and can still be read through the API, but the site no longer shows
them and nothing on the site uses their numbers.
