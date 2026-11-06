"""Filename and input-discovery helpers."""

from __future__ import annotations

import re
from pathlib import Path


NIFTI_SUFFIXES = (".nii", ".nii.gz")
CHANNEL_SUFFIX = re.compile(r"_\d{4}$")


def strip_nifti_suffix(path: Path) -> str:
    name = path.name
    if name.lower().endswith(".nii.gz"):
        return name[:-7]
    if name.lower().endswith(".nii"):
        return name[:-4]
    raise ValueError(f"Not a NIfTI file: {path}")


def case_id_from_image(path: Path) -> str:
    stem = strip_nifti_suffix(path)
    return CHANNEL_SUFFIX.sub("", stem)


def discover_nifti_images(input_dir: Path) -> dict[str, Path]:
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    files = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.name.lower().endswith(NIFTI_SUFFIXES)
    )
    if not files:
        raise FileNotFoundError(f"No .nii or .nii.gz files found directly in: {input_dir}")

    cases: dict[str, Path] = {}
    for path in files:
        stem = strip_nifti_suffix(path)
        match = re.search(r"_(\d{4})$", stem)
        if match and match.group(1) != "0000":
            raise ValueError(
                f"LeviDigit is single-channel; unexpected nnU-Net channel file: {path.name}"
            )
        case_id = case_id_from_image(path)
        if not case_id:
            raise ValueError(f"Could not derive a case ID from: {path.name}")
        if case_id in cases:
            raise ValueError(
                f"Duplicate case ID {case_id!r} from {cases[case_id].name} and {path.name}"
            )
        cases[case_id] = path
    return cases


def mask_path_for_case(mask_dir: Path, case_id: str) -> Path:
    gz = mask_dir / f"{case_id}.nii.gz"
    if gz.is_file():
        return gz
    nii = mask_dir / f"{case_id}.nii"
    if nii.is_file():
        return nii
    raise FileNotFoundError(f"No predicted mask found for {case_id} in {mask_dir}")

