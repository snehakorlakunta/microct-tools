"""Largest-concavity detection and in-mask digit-length measurement."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError
from skimage.draw import polygon
from skimage.morphology import convex_hull_image


class DigitLengthError(RuntimeError):
    """Raised when a defensible digit-length measurement cannot be produced."""


@dataclass(frozen=True)
class DigitLengthResult:
    concavity_mask: np.ndarray
    counted_line_mask: np.ndarray
    concavity_centroid_voxel: np.ndarray
    concavity_centroid_world_mm: np.ndarray
    distal_endpoint_voxel: np.ndarray
    distal_endpoint_world_mm: np.ndarray
    first_counted_voxel: np.ndarray
    digit_length_mm: float
    total_centroid_to_endpoint_mm: float
    counted_fraction: float


def _length_roi(mask: np.ndarray, affine: np.ndarray, maximum_gap_mm: float = 0.45) -> np.ndarray:
    """Match perios2 v5: retain the largest component and a nearby runner-up."""
    labels, count = ndimage.label(mask)
    if count <= 1:
        return mask.astype(bool)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    ranked = np.argsort(sizes)[::-1]
    largest = labels == int(ranked[0])
    second = labels == int(ranked[1])
    spacing = tuple(float(np.linalg.norm(affine[:3, axis])) for axis in range(3))
    distance = ndimage.distance_transform_edt(~largest, sampling=spacing)
    if float(np.min(distance[second])) <= maximum_gap_mm:
        return largest | second
    return largest


def _largest_component(mask: np.ndarray) -> np.ndarray:
    labels, count = ndimage.label(mask)
    if count == 0:
        return np.zeros_like(mask, dtype=bool)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    return labels == int(np.argmax(sizes))


def _slice_hull(slice_mask: np.ndarray) -> np.ndarray:
    coords = np.argwhere(slice_mask)
    if len(coords) < 3:
        return slice_mask.copy()
    try:
        vertices = coords[ConvexHull(coords).vertices]
    except QhullError:
        return slice_mask.copy()
    rows, cols = polygon(vertices[:, 0], vertices[:, 1], slice_mask.shape)
    result = np.zeros_like(slice_mask, dtype=bool)
    result[rows, cols] = True
    return result


def _slice_wise_hull(mask: np.ndarray, axis: int = 2) -> np.ndarray:
    result = np.zeros_like(mask, dtype=bool)
    for index in range(mask.shape[axis]):
        source = np.take(mask, index, axis=axis)
        target = _slice_hull(source)
        selection = [slice(None)] * 3
        selection[axis] = index
        result[tuple(selection)] = target
    return result


def _crop_to_mask(mask: np.ndarray, padding: int) -> tuple[np.ndarray, tuple[slice, slice, slice]]:
    coords = np.argwhere(mask)
    if not len(coords):
        raise DigitLengthError("The predicted ROI mask is empty")
    lower = np.maximum(coords.min(axis=0) - padding, 0)
    upper = np.minimum(coords.max(axis=0) + padding + 1, mask.shape)
    slices = tuple(slice(int(lo), int(hi)) for lo, hi in zip(lower, upper))
    return mask[slices], slices


def detect_largest_concavity(
    roi_mask: np.ndarray,
    *,
    downsample_factor: int = 2,
    shrink_percentile: float = 95.0,
    morphology_iterations: int = 5,
) -> np.ndarray:
    """Detect the largest concavity using the published perios2 v5 construction."""
    if downsample_factor < 1:
        raise ValueError("downsample_factor must be at least 1")
    padding = 2 * morphology_iterations * downsample_factor + 2
    cropped, slices = _crop_to_mask(roi_mask.astype(bool), padding)
    zoom = 1.0 / downsample_factor
    reduced = ndimage.zoom(cropped.astype(np.uint8), zoom, order=0) > 0
    if np.count_nonzero(reduced) < 4:
        raise DigitLengthError("ROI is too small for concavity detection")

    planar_hull = _slice_wise_hull(reduced, axis=2)
    distance = ndimage.distance_transform_edt(~reduced)
    hull_distances = distance[planar_hull]
    if not len(hull_distances):
        raise DigitLengthError("Slice-wise hull is empty")
    limit = float(np.percentile(hull_distances, shrink_percentile))
    shrink_wrapped = planar_hull & (distance <= limit)
    shrink_wrapped = ndimage.binary_fill_holes(_largest_component(shrink_wrapped))

    with np.errstate(all="ignore"):
        hull_3d = convex_hull_image(shrink_wrapped)
    difference = hull_3d & ~shrink_wrapped
    eroded = ndimage.binary_erosion(difference, iterations=morphology_iterations)
    restored = ndimage.binary_dilation(eroded, iterations=morphology_iterations)
    socket_reduced = _largest_component(restored)
    if not np.any(socket_reduced):
        raise DigitLengthError(
            "No concavity survived hull differencing and morphology; inspect the ROI mask"
        )

    target_shape = cropped.shape
    enlarged = ndimage.zoom(
        socket_reduced.astype(np.uint8), downsample_factor, order=0
    ).astype(bool)
    fitted = np.zeros(target_shape, dtype=bool)
    common = tuple(slice(0, min(a, b)) for a, b in zip(enlarged.shape, target_shape))
    fitted[common] = enlarged[common]
    output = np.zeros_like(roi_mask, dtype=bool)
    output[slices] = fitted
    return _largest_component(output)


def _voxel_to_world(points: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return points @ affine[:3, :3].T + affine[:3, 3]


def measure_digit_length(
    roi_mask: np.ndarray,
    affine: np.ndarray,
    *,
    downsample_factor: int = 2,
    samples_per_voxel: int = 4,
) -> DigitLengthResult:
    """Measure only portions of the centroid-to-distal line that lie in the ROI."""
    roi = _length_roi(roi_mask.astype(bool), affine)
    concavity = detect_largest_concavity(
        roi, downsample_factor=downsample_factor
    )
    centroid_voxel = np.asarray(ndimage.center_of_mass(concavity), dtype=float)
    centroid_world = _voxel_to_world(centroid_voxel[None, :], affine)[0]

    roi_voxels = np.argwhere(roi)
    roi_world = _voxel_to_world(roi_voxels, affine)
    squared = np.sum((roi_world - centroid_world) ** 2, axis=1)
    distal_index = int(np.argmax(squared))
    distal_voxel = roi_voxels[distal_index].astype(int)
    distal_world = roi_world[distal_index]
    total_length = float(np.sqrt(squared[distal_index]))
    if total_length <= 0:
        raise DigitLengthError("Centroid and distal endpoint coincide")

    voxel_distance = float(np.linalg.norm(distal_voxel - centroid_voxel))
    sample_count = max(2, int(np.ceil(voxel_distance * samples_per_voxel)) + 1)
    parameters = np.linspace(0.0, 1.0, sample_count)
    sample_voxels = centroid_voxel + parameters[:, None] * (distal_voxel - centroid_voxel)
    midpoint_voxels = (sample_voxels[:-1] + sample_voxels[1:]) / 2.0
    indices = np.rint(midpoint_voxels).astype(int)
    valid = np.all((indices >= 0) & (indices < np.asarray(roi.shape)), axis=1)
    inside = np.zeros(len(indices), dtype=bool)
    inside[valid] = roi[tuple(indices[valid].T)]

    segment_length = total_length / (sample_count - 1)
    counted_length = float(np.count_nonzero(inside) * segment_length)
    if counted_length <= 0:
        raise DigitLengthError("The measurement line does not intersect the ROI")

    line_mask = np.zeros_like(roi, dtype=bool)
    counted_indices = indices[inside]
    line_mask[tuple(counted_indices.T)] = True
    ordered_unique = []
    seen: set[tuple[int, int, int]] = set()
    for point in counted_indices:
        key = tuple(int(value) for value in point)
        if key not in seen:
            seen.add(key)
            ordered_unique.append(point.copy())
    first_counted = np.asarray(ordered_unique[0], dtype=int)

    return DigitLengthResult(
        concavity_mask=concavity,
        counted_line_mask=line_mask,
        concavity_centroid_voxel=centroid_voxel,
        concavity_centroid_world_mm=centroid_world,
        distal_endpoint_voxel=distal_voxel,
        distal_endpoint_world_mm=distal_world,
        first_counted_voxel=first_counted,
        digit_length_mm=counted_length,
        total_centroid_to_endpoint_mm=total_length,
        counted_fraction=counted_length / total_length,
    )
