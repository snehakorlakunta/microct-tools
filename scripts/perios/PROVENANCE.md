# Vendored third-party code: `perios / digitpipe_v5`

## What is here

`digitpipe_v5/` is a **verbatim copy** of the `digitpipe_v5/` directory of the
`perios` morphometry pipeline. Nothing in it has been modified — the app drives it
through `scripts/measure_morphometry.py`, which treats `run_pipeline.py` as an
opaque subprocess so this directory can be re-vendored by overwriting it.

| | |
|---|---|
| **Source repo** | <https://github.com/snehakorlakunta/perios> |
| **Upstream of that fork** | <https://github.com/mwilde49/perios> (`snehakorlakunta/perios` is a fork of it) |
| **Branch** | `master` |
| **Commit vendored** | `88bedbaaee4e28a1b0b46ac48377af0aeb38499d` |
| **Commit date** | 2026-07-22 |
| **Commit subject** | "Add README with pipeline showcase GIFs" |
| **Vendored on** | 2026-08-03 |
| **Fetched via** | `https://raw.githubusercontent.com/snehakorlakunta/perios/88bedbaaee4e28a1b0b46ac48377af0aeb38499d/digitpipe_v5/<file>` |

### Files vendored (13 of 13 — nothing skipped)

| File | Bytes | Role |
|---|---|---|
| `run_pipeline.py` | 27304 | **the entry point the app calls** — the 8 stages, end to end |
| `utils.py` | 10616 | **shared helpers + metric definitions** (`VOXEL_SIZE_MM`, hull/shrink/socket metrics) |
| `00_config.ipynb` | 2397 | notebook: config constants |
| `00_downsample.ipynb` | 11786 | notebook: stage 0 |
| `01_hull_generation.ipynb` | 15777 | notebook: stage 1 |
| `02_shrink_wrap.ipynb` | 19989 | notebook: stage 2 |
| `03_major_axis.ipynb` | 14423 | notebook: stage 3 |
| `04_socket_detection.ipynb` | 7359 | notebook: stage 4 |
| `05_bone_length.ipynb` | 18308 | notebook: stage 5 |
| `06_upsample_metrics.ipynb` | 11104 | notebook: stage 6 |
| `07_export_excel.ipynb` | 12315 | notebook: stage 7 (**richer** than the `.py` — see note below) |
| `08_visualization.ipynb` | 7408 | notebook: stage 8 (GIFs; skipped by `--skip-viz`) |
| `run_all.ipynb` | 8094 | notebook: drives 00–08 |

Only `run_pipeline.py` and `utils.py` are executed by this app. The notebooks are
kept because they are the authoritative, human-readable description of each stage
and of the metric semantics.

---

## License status — use in this project is authorised

`perios` comes from a colleague working on this same project, and the author has
confirmed its use here. **Vendoring and using `digitpipe_v5/` within
`microct-seg-lab` is authorised — there is no licensing issue for this project's
own use, and no restriction on shipping the app to the people working on it.**

One factual note for the future, not a blocker: the upstream repository
(`snehakorlakunta/perios`, and its parent `mwilde49/perios`) **carries no LICENSE
file**. That has no bearing on the authorised use above, but under default
copyright it means there is no standing public grant to third parties. So if this
app is ever redistributed more widely than the project — open-sourced, published,
put in a public container image, or handed to an outside party — get that
permission recorded explicitly at that point: ask the perios authors
(`mwilde49`, `snehakorlakunta`) to add a license file (MIT/BSD/Apache-2.0 would
all work), and note it here.

No license header, copyright notice, or attribution has been removed or altered —
the files are byte-for-byte as fetched.

---

## Re-vendoring

```bash
python - <<'PY'
import urllib.request, os
SHA = "88bedbaaee4e28a1b0b46ac48377af0aeb38499d"   # or a newer commit
base = f"https://raw.githubusercontent.com/snehakorlakunta/perios/{SHA}/digitpipe_v5/"
dest = os.path.join("scripts", "perios", "digitpipe_v5")
os.makedirs(dest, exist_ok=True)
for f in ["run_pipeline.py", "utils.py",
          "00_config.ipynb", "00_downsample.ipynb", "01_hull_generation.ipynb",
          "02_shrink_wrap.ipynb", "03_major_axis.ipynb", "04_socket_detection.ipynb",
          "05_bone_length.ipynb", "06_upsample_metrics.ipynb", "07_export_excel.ipynb",
          "08_visualization.ipynb", "run_all.ipynb"]:
    open(os.path.join(dest, f), "wb").write(urllib.request.urlopen(base + f).read())
PY
```

After re-vendoring, re-check the metric key names documented in
`scripts/measure_morphometry.py` (see the `METRIC KEY PROVENANCE` comment there) —
the parser is defensive but the *names* were read out of this exact commit.

---

## Runtime dependencies of the vendored code

Scanned from the imports of `run_pipeline.py` + `utils.py` + the notebooks:

| Package | Needed by | In the `morph` extra? |
|---|---|---|
| `nibabel` | `utils.load_nifti/save_nifti` | yes |
| `numpy` | everywhere | yes (core dep) |
| `scipy` | `ndimage`, `spatial.ConvexHull` | yes |
| `scikit-image` | `morphology`, `draw.polygon`, `convex_hull_image` | yes |
| **`scikit-learn`** | **`utils.py` top-level `from sklearn.decomposition import PCA`** | **yes — added; see note** |
| `pandas` | stage 7 Excel export | yes |
| `openpyxl` | stage 7 Excel writer engine | yes |
| `imageio` | **notebook 08 only** (GIFs) — not imported by `run_pipeline.py` | no (not needed) |
| `trimesh` | **not imported anywhere in the vendored code** | yes (kept, but unused — see note) |

Two deviations from the originally-specified extra, both deliberate:

1. **`scikit-learn` was ADDED.** `utils.py` imports `sklearn.decomposition.PCA` at
   module level, so *every* stage fails at import time without it. The pipeline
   cannot run without scikit-learn; omitting it would have been a hard bug.
2. **`trimesh` is retained but unused.** No file in `digitpipe_v5/` imports it. It
   is kept in the extra as specified (a future mesh-based stage may want it), but
   it is safe to drop if the offline wheelhouse budget matters.
