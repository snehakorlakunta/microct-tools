"""
Shared utility functions for the digitpipe_v2 pipeline.

This module contains common functions used across multiple notebooks:
- NIfTI I/O with affine preservation
- 3D line drawing (Bresenham)
- Morphological operations
- Metrics computation and saving
"""

import nibabel as nib
import numpy as np
from pathlib import Path
from scipy import ndimage
from scipy.spatial import ConvexHull
from sklearn.decomposition import PCA
from skimage.morphology import convex_hull_image, ball
from skimage.draw import polygon
import json
import warnings
warnings.filterwarnings('ignore')

# Constants
VOXEL_SIZE_MM = 0.004  # mm per voxel (4 micrometers)
VOXEL_VOLUME_MM3 = VOXEL_SIZE_MM ** 3  # mm^3 per voxel


def ensure_dir(path):
    """Create directory if it doesn't exist."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)


def load_nifti(path):
    """
    Load a NIfTI file and return data, affine, and header.

    Args:
        path: Path to .nii.gz file

    Returns:
        Tuple of (data, affine, header)
    """
    nii = nib.load(str(path))
    data = nii.get_fdata()
    return data, nii.affine, nii.header


def save_nifti(data, affine, header, output_path):
    """
    Save data as NIfTI file, preserving affine and header.

    Args:
        data: 3D numpy array
        affine: 4x4 affine transformation matrix
        header: NIfTI header
        output_path: Path to save .nii.gz file
    """
    nii = nib.Nifti1Image(data.astype(np.uint8), affine, header)
    nib.save(nii, str(output_path))


def save_metrics(metrics, output_path):
    """Save metrics list to JSON file."""
    with open(str(output_path), 'w') as f:
        json.dump(metrics, f, indent=2)


def load_metrics(path):
    """Load metrics from JSON file."""
    with open(str(path), 'r') as f:
        return json.load(f)


def largest_component(mask):
    """
    Keep only the largest connected component of a binary mask.

    Args:
        mask: Binary 3D numpy array

    Returns:
        Binary mask with only largest component
    """
    labeled, num = ndimage.label(mask)
    if num <= 1:
        return mask.astype(np.uint8)

    sizes = ndimage.sum(mask, labeled, range(1, num + 1))
    largest = np.argmax(sizes) + 1
    return (labeled == largest).astype(np.uint8)


def bresenham_line_3d(p1, p2):
    """
    Generate 3D line coordinates from p1 to p2 using Bresenham's algorithm.

    Args:
        p1: Starting point (z, y, x) or (x, y, z)
        p2: Ending point (z, y, x) or (x, y, z)

    Returns:
        Array of coordinates along the line, shape (N, 3)
    """
    p1 = np.array(p1, dtype=int)
    p2 = np.array(p2, dtype=int)

    d = np.abs(p2 - p1)
    s = np.sign(p2 - p1)

    driving_axis = np.argmax(d)

    points = []
    err = np.array([0, 0, 0])
    current = p1.copy()

    steps = d[driving_axis]
    for _ in range(steps + 1):
        points.append(current.copy())

        for i in range(3):
            if i != driving_axis:
                err[i] += d[i]
                if 2 * err[i] >= d[driving_axis]:
                    current[i] += s[i]
                    err[i] -= d[driving_axis]

        current[driving_axis] += s[driving_axis]

    return np.array(points)


def compute_2d_hull_slice(slice_2d):
    """
    Compute 2D convex hull of a binary slice.

    Args:
        slice_2d: 2D binary numpy array

    Returns:
        2D binary mask of convex hull, same shape as input
    """
    if not np.any(slice_2d):
        return np.zeros_like(slice_2d, dtype=np.uint8)

    coords = np.argwhere(slice_2d > 0)

    if len(coords) < 3:
        return slice_2d.astype(np.uint8)

    try:
        hull = ConvexHull(coords)
        vertices = coords[hull.vertices]

        slice_hull = np.zeros_like(slice_2d, dtype=np.uint8)
        rr, cc = polygon(vertices[:, 0], vertices[:, 1], slice_2d.shape)
        slice_hull[rr, cc] = 1

        return slice_hull
    except:
        return slice_2d.astype(np.uint8)


def compute_slicewise_hull(mask, axis=2):
    """
    Compute 3D hull using slice-wise 2D convex hulls.

    Args:
        mask: Binary 3D numpy array
        axis: Axis along which to slice (0, 1, or 2)

    Returns:
        3D binary hull mask
    """
    hull_mask = np.zeros_like(mask, dtype=np.uint8)
    n_slices = mask.shape[axis]

    for i in range(n_slices):
        if axis == 0:
            slice_2d = mask[i, :, :]
            hull_mask[i, :, :] = compute_2d_hull_slice(slice_2d)
        elif axis == 1:
            slice_2d = mask[:, i, :]
            hull_mask[:, i, :] = compute_2d_hull_slice(slice_2d)
        else:
            slice_2d = mask[:, :, i]
            hull_mask[:, :, i] = compute_2d_hull_slice(slice_2d)

    return hull_mask


def shrink_wrap_distance(hull_mask, original_mask, percentile=50):
    """
    Shrink hull by keeping voxels within distance threshold from original mask.

    Args:
        hull_mask: Binary hull mask
        original_mask: Binary original mask
        percentile: Percentile of distances to use as threshold

    Returns:
        Shrink-wrapped binary mask
    """
    distance = ndimage.distance_transform_edt(~original_mask.astype(bool))

    hull_distances = distance[hull_mask > 0]
    if len(hull_distances) == 0:
        return hull_mask.copy()

    threshold = np.percentile(hull_distances, percentile)

    shrunk = hull_mask & (distance <= threshold)
    shrunk = shrunk.astype(np.uint8)

    shrunk = largest_component(shrunk)
    shrunk = ndimage.binary_fill_holes(shrunk).astype(np.uint8)

    return shrunk


def compute_major_axis(mask):
    """
    Compute the major axis of a 3D binary mask using PCA.

    Args:
        mask: Binary 3D numpy array

    Returns:
        Dictionary containing center, direction, length, endpoints, explained_variance
    """
    coords = np.argwhere(mask > 0)

    if len(coords) < 2:
        raise ValueError("Mask has too few voxels for axis computation")

    center = coords.mean(axis=0)

    pca = PCA(n_components=3)
    pca.fit(coords)

    major_axis_direction = pca.components_[0]

    centered_coords = coords - center
    projections = centered_coords @ major_axis_direction

    min_proj = projections.min()
    max_proj = projections.max()
    axis_length = max_proj - min_proj

    start_point = center + min_proj * major_axis_direction
    end_point = center + max_proj * major_axis_direction

    return {
        'center': center.tolist(),
        'direction': major_axis_direction.tolist(),
        'length': float(axis_length),
        'endpoints': (start_point.tolist(), end_point.tolist()),
        'explained_variance': float(pca.explained_variance_ratio_[0]),
    }


def draw_line_3d(shape, start, end, thickness=3, value=2):
    """
    Draw a 3D line in a volume with specified thickness.

    Args:
        shape: Shape of the output volume
        start: Starting point
        end: Ending point
        thickness: Line thickness in voxels
        value: Value to set for line voxels

    Returns:
        Volume with line drawn
    """
    line_volume = np.zeros(shape, dtype=np.uint8)

    start = np.round(start).astype(int)
    end = np.round(end).astype(int)

    start = np.clip(start, 0, np.array(shape) - 1)
    end = np.clip(end, 0, np.array(shape) - 1)

    points = bresenham_line_3d(start, end)

    for point in points:
        if all(0 <= point[i] < shape[i] for i in range(3)):
            line_volume[point[0], point[1], point[2]] = 1

    if thickness > 1:
        radius = (thickness - 1) // 2
        if radius > 0:
            line_volume = ndimage.binary_dilation(line_volume, structure=ball(radius))

    return (line_volume * value).astype(np.uint8)


def compute_3d_convex_hull(mask):
    """
    Compute true 3D convex hull of binary mask.

    Args:
        mask: Binary 3D mask

    Returns:
        Binary 3D convex hull mask
    """
    try:
        hull_mask = convex_hull_image(mask > 0).astype(np.uint8)
        return hull_mask
    except Exception as e:
        print(f"WARNING: Convex hull computation failed: {e}")
        return mask.copy()


def compute_shrink_metrics(original_mask, hull_mask, shrunk_mask):
    """
    Compute comparison metrics for shrink-wrapping.

    Returns:
        Dictionary of metrics
    """
    original_vol = int(np.sum(original_mask))
    hull_vol = int(np.sum(hull_mask))
    shrunk_vol = int(np.sum(shrunk_mask))

    contained = int(np.sum(shrunk_mask[original_mask > 0]))
    containment_ratio = float(contained / original_vol) if original_vol > 0 else 0.0

    hull_expansion = float(hull_vol / original_vol) if original_vol > 0 else 0.0
    shrunk_expansion = float(shrunk_vol / original_vol) if original_vol > 0 else 0.0
    reduction_ratio = float(shrunk_vol / hull_vol) if hull_vol > 0 else 0.0

    return {
        'original_volume': original_vol,
        'hull_volume': hull_vol,
        'shrunk_volume': shrunk_vol,
        'containment_ratio': containment_ratio,
        'hull_expansion': hull_expansion,
        'shrunk_expansion': shrunk_expansion,
        'reduction_ratio': reduction_ratio,
        'voxels_removed': hull_vol - shrunk_vol,
    }


def compute_hull_metrics(original_mask, hull_mask):
    """
    Compute metrics for hull generation.

    Returns:
        Dictionary of metrics
    """
    original_vol = int(np.sum(original_mask))
    hull_vol = int(np.sum(hull_mask))

    containment_voxels = int(np.sum(hull_mask[original_mask > 0]))
    containment_ratio = float(containment_voxels / original_vol) if original_vol > 0 else 0.0
    expansion_ratio = float(hull_vol / original_vol) if original_vol > 0 else 0.0

    return {
        'original_volume': original_vol,
        'hull_volume': hull_vol,
        'containment_voxels': containment_voxels,
        'containment_ratio': containment_ratio,
        'expansion_ratio': expansion_ratio,
    }


def compute_socket_metrics(shrunk_mask, hull_mask, socket_mask, num_components):
    """
    Compute metrics for socket detection.

    Returns:
        Dictionary of metrics
    """
    socket_volume = int(np.sum(socket_mask))

    centroid = None
    equivalent_radius = None

    if socket_volume > 0:
        coords = np.argwhere(socket_mask > 0)
        centroid = coords.mean(axis=0).tolist()
        equivalent_radius = float((3 * socket_volume / (4 * np.pi)) ** (1/3))

    return {
        'shrunk_volume': int(np.sum(shrunk_mask)),
        'hull_volume': int(np.sum(hull_mask)),
        'socket_volume': socket_volume,
        'num_components': num_components,
        'centroid': centroid,
        'equivalent_radius': equivalent_radius,
    }
