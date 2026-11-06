# Organize & Analyze

The features added in the Linux-review build: how datasets are categorized and
grouped, how to edit many at once, and the analysis tools — BV/TV and digit
length, digit normalization, and 3D cropping.

## Categories: uct digit vs uct ho

Every uCT dataset can carry a **subtype**: `digit` (terminal phalanx) or `ho`
(heterotopic ossification). Set it in the dataset drawer, or once per set via
**set details** (below).

The subtype is load-bearing: **digit length is only measured on `uct digit`
datasets.** On any other dataset the LeviDigit measurement runs BV/TV only. A
dataset tagged `phalanx` from before categories existed still counts as a
digit, and the server stamps `subtype = digit` on those automatically.

## Digit IDs

Digit-uCT datasets carry a **digit ID** — `L2 L3 L4 R2 R3 R4` (side + digit).
It is parsed from the dataset name at ingest (`BDNF-R-T3` → `R3`; a name with
two different digit tokens is left blank rather than guessed) and always
editable in the drawer. A manual edit is remembered: re-ingest never
overwrites it.

The **unamputated** checkbox marks a dataset as a normalization *reference*
digit. The analysis dialog manages it for you (L3/R3 are pre-checked by
default), but it can also be set per dataset.

## Hierarchy: independent project / experiment / set assignment

A dataset can now be placed at any level: directly in a **project**, in an
**experiment**, or in a **set** — three separate dropdowns in the drawer.
Assigning a set fills in its experiment and project automatically; assigning
an experiment fills in its project. Clearing works downward only: clearing the
project clears everything, clearing the set leaves the experiment.

A dataset card with **no set** shows a thin **yellow line** under its preview
— the visual "not filed yet" marker.

## Bulk editing

On the Datasets page, toggle **Select** to get checkboxes on every card and an
action bar:

- **Assign** — project, experiment, and set pickers, applied independently to
  every selected dataset.
- **Tags** — add and/or remove tags across the selection.
- **Rename** — find & replace or a regular expression, with a live before →
  after preview of every name. The preview is exactly what gets saved; the
  server refuses collisions before touching anything.

Each dataset is settled on its own — one bad row is reported, not allowed to
abort the rest.

## Set details that autofill

A set can carry **organism** and **subtype**. Members inherit them when they
are assigned. Editing the details of a set that already has members asks how
to propagate:

- **Update all datasets** — overwrite every member.
- **Update only unedited datasets** — skip any field you set by hand on a
  specific dataset. This is the default, and the reason manual edits are
  tracked.
- **Don't update** — change the set only.

## Running by set

Every set in the project view has a **▶ Run set** button that opens the run
dialog with the whole set pre-selected. The dialog itself now offers
selection **by set or by individual dataset** (with search), shows how many
datasets are selected and which, and lists models by their **display name** —
the name you gave the model, not its original folder name.

With more than one NVIDIA GPU, the run dialog also offers **parallel runs**:
the worker executes up to that many segmentations at once, each pinned to its
own GPU. The setting applies live — no restart.

## BV/TV and digit length (LeviDigit)

BV/TV and digit length come from **LeviDigit**, the official method. Any
succeeded segmentation (digit **and** ho) can be measured from the run page:
`TV` = voxels in the mask, `BV` = mask voxels at or above a bone threshold,
`BV/TV = BV ÷ TV`, plus digit length on digit datasets.

The threshold is a **grey value, 0–255 inclusive**, set by the administrator to
the lab's value (unset, it is 60, LeviDigit's README example). It is not
converted from Hounsfield units. Every measurement stores the threshold it
used. See the **BV/TV & Digit Length Guide** for the details.

The older interim threshold BV/TV (800 HU, converted per scan) and the
digitpipe morphometry are kept on the server, tagged **SK**, but the site no
longer shows them.

## Digit normalization

**🧮 Analyze** on a set or experiment opens the normalization dialog for its
digit datasets. Tick the unamputated reference digits (L3/R3 pre-checked),
pick a mode:

- **Per leg** — each dataset ÷ the reference on the same leg of the same
  mouse.
- **Per mouse** — ÷ the mean of that mouse's references (L and R).
- **Per set** — ÷ the mean of all references in the set.

"Same mouse" is derived from the dataset name with the digit token removed
(`BDNF-R-T3` → `BDNF`) — the dialog shows the derived grouping so a
mis-grouping is visible before you compute. Only **LeviDigit** BV/TV values
are used; a dataset without one shows *no BV/TV*. The result table shows raw BV/TV,
the reference used, and the normalized value per dataset, with per-set means.
**Save as analysis** stores the table as an analysis record in the project.

## 3D crop

Most QC failures are artifacts or extra bone that got segmented. The crop box
fixes that two ways:

- **Crop before running** (dataset drawer → ✂ Crop): the box is saved on the
  dataset and applied to the raw stack *before* inference on every future run
  — the model never sees the artifact. Existing runs are untouched; each run
  snapshots the box it used.
- **Crop a finished mask** (run page → ✂ Crop mask): zeroes the mask outside
  the box with no GPU cost. The ROI numbers are recomputed, the original mask
  file is kept (repeated crops adjust rather than compound), and existing
  measurements are flagged because they describe the pre-crop mask.

**BMP export** now has two modes: the classic binary mask, and **masked
image** — the original scan pixels inside the mask, black elsewhere, which is
the stack you want for visual QC. Each mode exports to its own folder.

## Where all of this is stored

Every field on this page — categories, digit IDs, hierarchy, set details,
crop boxes, thresholds, normalization tables — lives in the server's SQLite
registry (`state/registry.db`). Scan folders on disk are never written to.
Back up with `scripts/backup_registry.py`, never a plain file copy (the DB is
in WAL mode).
