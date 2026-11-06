# Vendored third-party code: `LeviDigit`

## What is here

`levidigit/` is a **verbatim copy** of the `levidigit/` package from the
LeviDigit repository. Nothing in it has been modified. The app drives it through
`scripts/measure_levidigit.py`, which calls `levidigit.bvtv.analyze_bvtv` (and
`measure_case` for BV/TV-only runs) exactly as the upstream
`postprocessing/analyze_bvtv.py` does, so this directory can be re-vendored by
overwriting it.

LeviDigit is the **official** BV/TV and digit-length calculation in this app
(`pipeline_version = levidigit_v1`). The older in-app methods (`bvtv_thresh_v1`
and `digitpipe_v5`) are kept and tagged **SK**.

| | |
|---|---|
| **Source repo** | <https://github.com/snehakorlakunta/levi_digit_model> (a fork) |
| **Branch** | default |
| **Commit vendored** | `4e27656ce3909a3a73dc0b02f5a9b36a7676a45c` |
| **Commit date** | 2026-10-16 |
| **Vendored on** | 2026-11-06 |
| **License** | MIT, Copyright (c) 2026 Vishal Gokani — see `LICENSE` beside this file |

### What is used

| File | Used by the app |
|---|---|
| `bvtv.py` | yes — `analyze_bvtv` (BV/TV + digit length + derived masks) and `measure_case` |
| `digit_length.py` | yes (through `bvtv.py`) — largest-concavity digit length |
| `nifti.py`, `paths.py`, `constants.py` | yes (through `bvtv.py`) — input validation, 0.004 mm spacing check |
| `visualization.py` | yes, unless the measurement is queued with `skip_viz` — publication figures (needs matplotlib) |
| `inference.py`, `model.py` | no — segmentation is done by the app's own nnU-Net run. Kept so the copy stays verbatim |

### Not vendored

The upstream repo's `inference/`, `postprocessing/`, `training/`, `tools/` and
`model_release/` folders. The model weights are not in that repo at all; they
are distributed separately as `levidigit_nnunet_model.zip`. To segment with
them in this app, unzip it and register the folder that contains `plans.json`
under **Models → Register model**.

## Re-vendoring

```bash
cp -r <levi_digit_model checkout>/levidigit scripts/levidigit_vendor/
cp <levi_digit_model checkout>/LICENSE scripts/levidigit_vendor/LICENSE
```

then update the commit above and run `pytest tests/test_levidigit.py`, which
checks the parts of LeviDigit's output this app depends on.
