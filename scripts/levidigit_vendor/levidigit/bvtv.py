"""BV/TV, digit-length, artifact, and figure postprocessing."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import nibabel as nib
import numpy as np

from .digit_length import DigitLengthError, DigitLengthResult, measure_digit_length
from .nifti import validate_spacing
from .paths import discover_nifti_images, mask_path_for_case


CSV_COLUMNS = [
    "case_id", "image_file", "mask_file", "bone_threshold", "voxel_volume_mm3",
    "tv_voxels", "bv_voxels", "tv_mm3", "bv_mm3", "bv_tv_percent",
    "digit_length_mm", "digit_length_um", "digit_length_status",
    "concavity_voxels", "concavity_mm3", "counted_fraction",
    "concavity_centroid_i", "concavity_centroid_j", "concavity_centroid_k",
    "concavity_centroid_x_mm", "concavity_centroid_y_mm", "concavity_centroid_z_mm",
    "distal_endpoint_i", "distal_endpoint_j", "distal_endpoint_k",
    "distal_endpoint_x_mm", "distal_endpoint_y_mm", "distal_endpoint_z_mm",
]


LABEL_DEFINITIONS = {
    "purpose": "Visualization labelmaps only; use derived_masks for quantitative analysis.",
    "overlap_priority": "landmarks > counted length > concavity > BV > TV",
    "standard": {
        "0": {"name": "background", "color": "#000000"},
        "1": {"name": "TV below bone threshold", "color": "#56B4E9"},
        "2": {"name": "BV within TV", "color": "#D55E00"},
        "3": {"name": "digit length counted", "color": "#0047AB"},
    },
    "audit_additions": {
        "4": {"name": "largest concavity", "color": "#CC79A7"},
        "5": {"name": "concavity centroid", "color": "#FFFFFF"},
        "6": {"name": "distal endpoint", "color": "#009E73"},
    },
    "tv_reconstruction": "In the standard labelmap, labels 1, 2, and 3 together reconstruct TV. Audit overlays may replace TV voxels; use the authoritative binary masks for measurement.",
}


def validate_threshold(value: float) -> float:
    threshold = float(value)
    if not 0.0 <= threshold <= 255.0:
        raise ValueError(f"Bone threshold must be between 0 and 255; got {value}")
    return threshold


def _load_case(case_id: str, image_path: Path, mask_path: Path) -> tuple[object, np.ndarray, np.ndarray]:
    image_nii = nib.load(str(image_path))
    mask_nii = nib.load(str(mask_path))
    validate_spacing(image_nii, image_path)
    if len(image_nii.shape) != 3:
        raise ValueError(f"Expected a 3D image for {case_id}; got {image_nii.shape}")
    if image_nii.shape != mask_nii.shape:
        raise ValueError(f"Shape mismatch for {case_id}: image {image_nii.shape}, mask {mask_nii.shape}")
    if not np.allclose(image_nii.affine, mask_nii.affine, rtol=1e-5, atol=1e-7):
        raise ValueError(f"Image and mask affines differ for {case_id}")
    image = np.asanyarray(image_nii.dataobj)
    mask_data = np.asanyarray(mask_nii.dataobj)
    if not np.all(np.isfinite(image)):
        raise ValueError(f"Image contains non-finite values: {image_path}")
    image_min, image_max = float(np.min(image)), float(np.max(image))
    if image_min < 0.0 or image_max > 255.0:
        raise ValueError(f"Expected 0-255 intensities for {case_id}; observed {image_min:g}-{image_max:g}")
    values = np.unique(mask_data)
    binary = np.isclose(values, 0.0, atol=1e-5) | np.isclose(values, 1.0, atol=1e-5)
    if not np.all(binary):
        raise ValueError(f"Predicted mask for {case_id} must be binary 0/1; found {values[:10]}")
    mask = mask_data > 0.5
    if not np.any(mask):
        raise ValueError(f"Predicted mask is empty for {case_id}")
    return image_nii, image, mask


def _save_mask(mask: np.ndarray, reference: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = reference.header.copy()
    header.set_data_dtype(np.uint8)
    output = nib.Nifti1Image(mask.astype(np.uint8), reference.affine, header)
    output.set_qform(reference.affine, code=int(reference.header["qform_code"]))
    output.set_sform(reference.affine, code=int(reference.header["sform_code"]))
    nib.save(output, str(path))


def _marker(shape: tuple[int, ...], center: np.ndarray, radius: int = 2) -> np.ndarray:
    output = np.zeros(shape, dtype=bool)
    rounded = np.rint(center).astype(int)
    lower = np.maximum(rounded - radius, 0)
    upper = np.minimum(rounded + radius + 1, shape)
    grid = np.ogrid[tuple(slice(int(lo), int(hi)) for lo, hi in zip(lower, upper))]
    local = sum((axis - value) ** 2 for axis, value in zip(grid, rounded)) <= radius ** 2
    output[tuple(slice(int(lo), int(hi)) for lo, hi in zip(lower, upper))] = local
    return output


def _save_artifacts(case_id: str, output_root: Path, reference: object, roi: np.ndarray, bv: np.ndarray, result: DigitLengthResult) -> None:
    _save_mask(bv, reference, output_root / "derived_masks" / "bv" / f"{case_id}.nii.gz")
    _save_mask(result.concavity_mask, reference, output_root / "derived_masks" / "concavity" / f"{case_id}.nii.gz")
    _save_mask(result.counted_line_mask, reference, output_root / "derived_masks" / "digit_length_counted" / f"{case_id}.nii.gz")
    standard = np.zeros(roi.shape, dtype=np.uint8)
    standard[roi] = 1
    standard[bv] = 2
    standard[result.counted_line_mask] = 3
    audit = standard.copy()
    audit[result.concavity_mask] = 4
    audit[result.counted_line_mask] = 3
    audit[_marker(roi.shape, result.concavity_centroid_voxel)] = 5
    audit[_marker(roi.shape, result.distal_endpoint_voxel)] = 6
    _save_mask(standard, reference, output_root / "visualization_labelmaps" / "standard" / f"{case_id}.nii.gz")
    _save_mask(audit, reference, output_root / "visualization_labelmaps" / "audit" / f"{case_id}.nii.gz")
    landmarks = {
        "case_id": case_id,
        "coordinate_convention": {"voxel": "zero-based NIfTI array indices (i, j, k)", "world": "NIfTI affine coordinates in millimetres (x, y, z)"},
        "concavity_centroid_voxel": result.concavity_centroid_voxel.tolist(),
        "concavity_centroid_world_mm": result.concavity_centroid_world_mm.tolist(),
        "first_counted_voxel": result.first_counted_voxel.tolist(),
        "distal_endpoint_voxel": result.distal_endpoint_voxel.tolist(),
        "distal_endpoint_world_mm": result.distal_endpoint_world_mm.tolist(),
        "digit_length_mm": result.digit_length_mm,
        "digit_length_um": result.digit_length_mm * 1000.0,
        "total_centroid_to_endpoint_mm": result.total_centroid_to_endpoint_mm,
        "counted_fraction": result.counted_fraction,
        "measurement_rule": "sum straight-line subsegments whose midpoint lies inside the binary ROI; all outside-ROI intervals are excluded",
        "concavity_method": "perios2 v5 adaptation: slice-wise hull, 95th-percentile shrink wrap, 3D hull difference, 5 erosions, 5 dilations, largest component",
    }
    landmark_path = output_root / "landmarks" / f"{case_id}.json"
    landmark_path.parent.mkdir(parents=True, exist_ok=True)
    landmark_path.write_text(json.dumps(landmarks, indent=2) + "\n", encoding="utf-8")


def _measurement_row(
    case_id: str,
    image_path: Path,
    mask_path: Path,
    threshold: float,
    image_nii: object,
    image: np.ndarray,
    mask: np.ndarray,
) -> dict[str, object]:
    bv = mask & (image >= threshold)
    voxel_volume = float(abs(np.linalg.det(image_nii.affine[:3, :3])))
    tv_voxels, bv_voxels = int(np.count_nonzero(mask)), int(np.count_nonzero(bv))
    return {
        "case_id": case_id, "image_file": image_path.name, "mask_file": mask_path.name,
        "bone_threshold": threshold, "voxel_volume_mm3": voxel_volume,
        "tv_voxels": tv_voxels, "bv_voxels": bv_voxels,
        "tv_mm3": tv_voxels * voxel_volume, "bv_mm3": bv_voxels * voxel_volume,
        "bv_tv_percent": (bv_voxels / tv_voxels) * 100.0,
    }


def measure_case(
    case_id: str, image_path: Path, mask_path: Path, bone_threshold: float
) -> dict[str, object]:
    """Return BV/TV metrics without writing artifacts (compatibility helper)."""
    threshold = validate_threshold(bone_threshold)
    image_nii, image, mask = _load_case(case_id, image_path, mask_path)
    return _measurement_row(
        case_id, image_path, mask_path, threshold, image_nii, image, mask
    )


def analyze_bvtv(input_dir: Path, mask_dir: Path, bone_threshold: float, output_csv: Path, *, make_figures: bool = True, figure_dpi: int = 300, concavity_downsample_factor: int = 2) -> list[dict[str, object]]:
    threshold = validate_threshold(bone_threshold)
    if figure_dpi <= 0:
        raise ValueError(f"figure_dpi must be positive; got {figure_dpi}")
    cases = discover_nifti_images(input_dir)
    mask_dir = mask_dir.expanduser().resolve()
    if not mask_dir.is_dir():
        raise FileNotFoundError(f"Mask directory not found: {mask_dir}")
    output_root = output_csv.parent
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "label_definitions.json").write_text(json.dumps(LABEL_DEFINITIONS, indent=2) + "\n", encoding="utf-8")
    rows: list[dict[str, object]] = []
    failures: dict[str, str] = {}
    for case_id, image_path in sorted(cases.items()):
        mask_path = mask_path_for_case(mask_dir, case_id)
        image_nii, image, roi = _load_case(case_id, image_path, mask_path)
        bv = roi & (image >= threshold)
        row = _measurement_row(
            case_id, image_path, mask_path, threshold, image_nii, image, roi
        )
        try:
            result = measure_digit_length(roi, np.asarray(image_nii.affine), downsample_factor=concavity_downsample_factor)
            voxel_volume = float(row["voxel_volume_mm3"])
            centroid_voxel, centroid_world = result.concavity_centroid_voxel, result.concavity_centroid_world_mm
            endpoint_voxel, endpoint_world = result.distal_endpoint_voxel, result.distal_endpoint_world_mm
            row.update({
                "digit_length_mm": result.digit_length_mm, "digit_length_um": result.digit_length_mm * 1000.0,
                "digit_length_status": "complete", "concavity_voxels": int(np.count_nonzero(result.concavity_mask)),
                "concavity_mm3": int(np.count_nonzero(result.concavity_mask)) * voxel_volume, "counted_fraction": result.counted_fraction,
                "concavity_centroid_i": centroid_voxel[0], "concavity_centroid_j": centroid_voxel[1], "concavity_centroid_k": centroid_voxel[2],
                "concavity_centroid_x_mm": centroid_world[0], "concavity_centroid_y_mm": centroid_world[1], "concavity_centroid_z_mm": centroid_world[2],
                "distal_endpoint_i": int(endpoint_voxel[0]), "distal_endpoint_j": int(endpoint_voxel[1]), "distal_endpoint_k": int(endpoint_voxel[2]),
                "distal_endpoint_x_mm": endpoint_world[0], "distal_endpoint_y_mm": endpoint_world[1], "distal_endpoint_z_mm": endpoint_world[2],
            })
            _save_artifacts(case_id, output_root, image_nii, roi, bv, result)
            if make_figures:
                from .visualization import save_all_figures
                save_all_figures(case_id=case_id, image_nii=image_nii, image=image, roi_mask=roi, bv_mask=bv, result=result, threshold=threshold, figures_dir=output_root / "figures", dpi=figure_dpi)
        except DigitLengthError as error:
            row.update({column: "" for column in CSV_COLUMNS if column not in row})
            row["digit_length_status"] = f"failed: {error}"
            failures[case_id] = str(error)
            _save_mask(bv, image_nii, output_root / "derived_masks" / "bv" / f"{case_id}.nii.gz")
        rows.append(row)
    with output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "bone_threshold": threshold,
        "threshold_rule": "image intensity >= bone_threshold inside predicted mask",
        "digit_length_rule": "straight centroid-to-distal line; sum only subsegments inside the binary ROI",
        "digit_length_parameters": {
            "concavity_method": "perios2_v5_adapted",
            "concavity_downsample_factor": concavity_downsample_factor,
            "shrink_percentile": 95,
            "morphology_iterations": 5,
            "line_samples_per_voxel": 4,
            "secondary_component_max_gap_mm": 0.45,
        },
        "units": {"volume": "mm3", "digit_length": ["mm", "um"], "coordinates": "mm"},
        "num_cases": len(rows), "num_digit_lengths_complete": len(rows) - len(failures),
        "digit_length_failures": failures, "figures_created": make_figures,
        "figure_dpi": figure_dpi if make_figures else None,
    }
    output_csv.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return rows
