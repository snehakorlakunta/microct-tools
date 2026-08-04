#!/usr/bin/env python3
"""
Standalone pipeline runner for digitpipe_v3.
Usage: python run_pipeline.py /path/to/target/directory

The target directory must contain:
  - labels/   (binary segmentation masks *.nii.gz)
  - images/   (CT images *.nii.gz)
"""

import sys
import time
import json
import argparse
from pathlib import Path
from datetime import timedelta

import numpy as np
from scipy import ndimage
from skimage import morphology

# Add script directory to path for utils import
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from utils import (
    load_nifti, save_nifti, save_metrics, ensure_dir, largest_component,
    compute_slicewise_hull, compute_hull_metrics, shrink_wrap_distance,
    compute_shrink_metrics, compute_major_axis, draw_line_3d,
    compute_3d_convex_hull, compute_socket_metrics, bresenham_line_3d,
    VOXEL_SIZE_MM, VOXEL_VOLUME_MM3
)

# ============================================================
# CONFIGURATION
# ============================================================
DOWNSAMPLE_FACTOR = 2
PERCENTILES = [50, 95]
AXIS_THICKNESS = 3
AXIS_VALUE = 2
# v5: Keep second largest component if within this distance of largest
SECONDARY_COMPONENT_MAX_DISTANCE_MM = 0.45
SECONDARY_COMPONENT_MAX_DISTANCE_VOXELS = SECONDARY_COMPONENT_MAX_DISTANCE_MM / VOXEL_SIZE_MM  # 75 voxels
# Socket detection morphology parameters
EROSION_ITERATIONS = 5
DILATION_ITERATIONS = 5
INTENSITY_THRESHOLD = 80.0


def print_header(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# STAGE 0: DOWNSAMPLE
# ============================================================
def downsample_mask(mask, factor, affine):
    zoom_factor = 1.0 / factor
    downsampled = ndimage.zoom(mask.astype(np.float32), zoom_factor, order=0)
    downsampled = (downsampled > 0.5).astype(np.uint8)
    new_affine = affine.copy()
    new_affine[:3, :3] *= factor
    return downsampled, new_affine


def downsample_image(image, factor, affine):
    zoom_factor = 1.0 / factor
    downsampled = ndimage.zoom(image.astype(np.float32), zoom_factor, order=1)
    new_affine = affine.copy()
    new_affine[:3, :3] *= factor
    return downsampled, new_affine


def stage_0_downsample(target):
    print_header("STAGE 0: DOWNSAMPLE")

    labels_in = target / "labels"
    images_in = target / "images"
    labels_out = ensure_dir(target / "labels_ds")
    images_out = ensure_dir(target / "images_ds")
    metrics_dir = ensure_dir(target / "metrics")

    shape_info = []

    # Downsample labels
    label_files = sorted(labels_in.glob("*.nii.gz"))
    print(f"Downsampling {len(label_files)} labels...")
    print(f"  v5: Keep largest component + 2nd largest if within {SECONDARY_COMPONENT_MAX_DISTANCE_MM}mm")

    for idx, f in enumerate(label_files, 1):
        sample_name = f.stem.replace('.nii', '')
        data, affine, header = load_nifti(f)
        mask = (data > 0).astype(np.uint8)
        original_voxels = np.sum(mask)
        original_shape = mask.shape

        # v5: Find connected components
        labeled, num_features = ndimage.label(mask)
        if num_features > 0:
            component_sizes = ndimage.sum(mask, labeled, range(1, num_features + 1))
            sorted_indices = np.argsort(component_sizes)[::-1]  # Largest first

            # Always keep largest component
            largest_label = sorted_indices[0] + 1
            mask_cleaned = (labeled == largest_label).astype(np.uint8)
            kept_components = 1

            # Check if second largest is close enough to keep
            if num_features > 1:
                second_label = sorted_indices[1] + 1
                second_mask = (labeled == second_label)

                # Compute distance from largest component
                largest_mask = (labeled == largest_label)
                dist_from_largest = ndimage.distance_transform_edt(~largest_mask)

                # Find minimum distance from second component to largest
                min_dist = np.min(dist_from_largest[second_mask])

                if min_dist <= SECONDARY_COMPONENT_MAX_DISTANCE_VOXELS:
                    mask_cleaned = mask_cleaned | second_mask.astype(np.uint8)
                    kept_components = 2

            mask = mask_cleaned

        cleaned_voxels = np.sum(mask)
        removed = original_voxels - cleaned_voxels

        ds_mask, ds_affine = downsample_mask(mask, DOWNSAMPLE_FACTOR, affine)
        save_nifti(ds_mask, ds_affine, header, labels_out / f.name)
        shape_info.append({
            'sample_name': sample_name,
            'original_shape': list(original_shape),
            'downsampled_shape': list(ds_mask.shape),
            'original_affine': affine.tolist(),
        })
        removed_str = f" (removed {removed:,}, kept {kept_components} comp)" if removed > 0 else ""
        print(f"  [{idx}/{len(label_files)}] {sample_name}: {original_shape} -> {ds_mask.shape}{removed_str}")

    # Downsample images
    image_files = sorted(images_in.glob("*.nii.gz"))
    print(f"Downsampling {len(image_files)} images...")

    for idx, f in enumerate(image_files, 1):
        data, affine, header = load_nifti(f)
        ds_image, ds_affine = downsample_image(data, DOWNSAMPLE_FACTOR, affine)
        save_nifti(ds_image.astype(np.float32), ds_affine, header, images_out / f.name)
        print(f"  [{idx}/{len(image_files)}] {f.stem}")

    # Save shape info
    with open(metrics_dir / "downsample_info.json", 'w') as fp:
        json.dump({'downsample_factor': DOWNSAMPLE_FACTOR, 'samples': shape_info}, fp, indent=2)

    print(f"Done. Labels: {labels_out}, Images: {images_out}")
    return len(label_files)


# ============================================================
# STAGE 1: HULL GENERATION
# ============================================================
def clean_binary_mask(mask_data):
    binary_mask = (mask_data > 0).astype(np.uint8)
    binary_mask = largest_component(binary_mask)
    binary_mask = ndimage.binary_fill_holes(binary_mask).astype(np.uint8)
    binary_mask = morphology.binary_closing(binary_mask, footprint=morphology.ball(1))
    return binary_mask.astype(np.uint8)


def stage_1_hull(target):
    print_header("STAGE 1: HULL GENERATION")

    input_dir = target / "labels_ds"
    output_dir = ensure_dir(target / "labelsHull_ds")
    metrics_dir = ensure_dir(target / "metrics")

    mask_files = sorted(input_dir.glob("*.nii.gz"))
    print(f"Processing {len(mask_files)} masks...")

    all_metrics = []
    for idx, f in enumerate(mask_files, 1):
        sample_name = f.stem.replace('.nii', '')
        data, affine, header = load_nifti(f)
        cleaned = clean_binary_mask(data)
        hull = compute_slicewise_hull(cleaned, axis=2)
        hull = ndimage.binary_fill_holes(hull).astype(np.uint8)
        hull = largest_component(hull)

        metrics = compute_hull_metrics(cleaned, hull)
        metrics['sample_name'] = sample_name
        all_metrics.append(metrics)

        save_nifti(hull, affine, header, output_dir / f"{sample_name}_hull.nii.gz")
        print(f"  [{idx}/{len(mask_files)}] {sample_name}: {metrics['expansion_ratio']:.2f}x expansion")

    save_metrics(all_metrics, metrics_dir / "hull_metrics_ds.json")
    print(f"Done. Output: {output_dir}")
    return len(mask_files)


# ============================================================
# STAGE 2: SHRINK-WRAP
# ============================================================
def stage_2_shrink(target):
    print_header("STAGE 2: SHRINK-WRAPPING")

    labels_dir = target / "labels_ds"
    hull_dir = target / "labelsHull_ds"
    metrics_dir = ensure_dir(target / "metrics")

    output_dirs = {p: ensure_dir(target / f"labelsShrunk_{p}_ds") for p in PERCENTILES}

    hull_files = sorted(hull_dir.glob("*_hull.nii.gz"))
    print(f"Processing {len(hull_files)} hulls at percentiles {PERCENTILES}...")

    all_metrics = []
    for idx, hf in enumerate(hull_files, 1):
        sample_name = hf.stem.replace('_hull', '').replace('.nii', '')
        orig_file = labels_dir / f"{sample_name}.nii.gz"
        if not orig_file.exists():
            continue

        orig_data, affine, header = load_nifti(orig_file)
        hull_data, _, _ = load_nifti(hf)
        orig_mask = (orig_data > 0).astype(np.uint8)
        hull_mask = (hull_data > 0).astype(np.uint8)

        print(f"  [{idx}/{len(hull_files)}] {sample_name}")
        for p in PERCENTILES:
            shrunk = shrink_wrap_distance(hull_mask, orig_mask, percentile=p)
            metrics = compute_shrink_metrics(orig_mask, hull_mask, shrunk)
            metrics['sample_name'] = sample_name
            metrics['percentile'] = p
            all_metrics.append(metrics)
            save_nifti(shrunk, affine, header, output_dirs[p] / f"{sample_name}_shrunk.nii.gz")
            reduction = (1 - metrics['reduction_ratio']) * 100
            print(f"      {p}th: {reduction:.1f}% reduced, containment={metrics['containment_ratio']:.4f}")

    save_metrics(all_metrics, metrics_dir / "shrink_metrics_ds.json")
    print(f"Done.")
    return len(hull_files)


# ============================================================
# STAGE 3: MAJOR AXIS
# ============================================================
def stage_3_axis(target):
    print_header("STAGE 3: MAJOR AXIS")

    input_dir = target / "labelsShrunk_50_ds"
    output_dir = ensure_dir(target / "labelsWithAxis_ds")
    metrics_dir = ensure_dir(target / "metrics")

    mask_files = sorted(input_dir.glob("*_shrunk.nii.gz"))
    print(f"Processing {len(mask_files)} masks...")

    all_info = []
    for idx, f in enumerate(mask_files, 1):
        sample_name = f.stem.replace('_shrunk', '').replace('.nii', '')
        data, affine, header = load_nifti(f)
        mask = (data > 0).astype(np.uint8)

        axis_info = compute_major_axis(mask)
        start, end = axis_info['endpoints']
        axis_line = draw_line_3d(mask.shape, start, end, thickness=AXIS_THICKNESS, value=AXIS_VALUE)
        output = np.where(axis_line > 0, AXIS_VALUE, mask)

        axis_info['sample_name'] = sample_name
        all_info.append(axis_info)

        save_nifti(output.astype(np.uint8), affine, header, output_dir / f"{sample_name}_with_axis.nii.gz")
        print(f"  [{idx}/{len(mask_files)}] {sample_name}: length={axis_info['length']:.1f}, var={axis_info['explained_variance']:.1%}")

    save_metrics(all_info, metrics_dir / "axis_info_ds.json")
    print(f"Done. Output: {output_dir}")
    return len(mask_files)


# ============================================================
# STAGE 4: SOCKET DETECTION
# ============================================================
def stage_4_socket(target):
    print_header("STAGE 4: SOCKET DETECTION")

    input_dir = target / "labelsShrunk_95_ds"
    metrics_dir = ensure_dir(target / "metrics")

    output_dirs = {
        'hull': ensure_dir(target / "labelsConvexHull3D_ds"),
        'difference': ensure_dir(target / "labelsHullDifference_ds"),
        'eroded': ensure_dir(target / "labelsHullDiff_eroded_ds"),
        'dilated': ensure_dir(target / "labelsHullDiff_dilated_ds"),
        'socket': ensure_dir(target / "labelsSocketDetected_ds"),
    }

    shrunk_files = sorted(input_dir.glob("*_shrunk.nii.gz"))
    print(f"Processing {len(shrunk_files)} masks (this may take a few minutes)...")

    all_metrics = []
    for idx, f in enumerate(shrunk_files, 1):
        sample_name = f.stem.replace('_shrunk', '').replace('.nii', '')
        print(f"  [{idx}/{len(shrunk_files)}] {sample_name}")

        shrunk_data, affine, header = load_nifti(f)
        shrunk_mask = (shrunk_data > 0).astype(np.uint8)

        # 3D convex hull
        hull = compute_3d_convex_hull(shrunk_mask)
        save_nifti(hull, affine, header, output_dirs['hull'] / f"{sample_name}_hull3d.nii.gz")

        # Difference
        diff = ((hull > 0) & (shrunk_mask == 0)).astype(np.uint8)
        save_nifti(diff, affine, header, output_dirs['difference'] / f"{sample_name}_difference.nii.gz")

        # Erode
        eroded = ndimage.binary_erosion(diff, iterations=EROSION_ITERATIONS).astype(np.uint8)
        save_nifti(eroded, affine, header, output_dirs['eroded'] / f"{sample_name}_eroded.nii.gz")

        # Dilate
        dilated = ndimage.binary_dilation(eroded, iterations=DILATION_ITERATIONS).astype(np.uint8)
        save_nifti(dilated, affine, header, output_dirs['dilated'] / f"{sample_name}_dilated.nii.gz")

        # Largest component
        labeled, num = ndimage.label(dilated)
        socket_mask = largest_component(dilated) if num > 0 else np.zeros_like(dilated)
        save_nifti(socket_mask, affine, header, output_dirs['socket'] / f"{sample_name}_socket.nii.gz")

        metrics = compute_socket_metrics(shrunk_mask, hull, socket_mask, num)
        metrics['sample_name'] = sample_name
        all_metrics.append(metrics)

        if metrics['equivalent_radius']:
            print(f"      Socket: {metrics['socket_volume']:,} voxels, radius={metrics['equivalent_radius']:.1f}")

    save_metrics(all_metrics, metrics_dir / "socket_metrics_ds.json")
    print(f"Done. Output: {output_dirs['socket']}")
    return len(shrunk_files)


# ============================================================
# STAGE 5: BONE LENGTH
# ============================================================
def measure_bone_length_v2(socket_mask, bone_mask, image_data, secondary_mask):
    socket_coords = np.argwhere(socket_mask > 0)
    if len(socket_coords) == 0:
        return None, None

    socket_com = socket_coords.mean(axis=0)
    bone_coords = np.argwhere(bone_mask > 0)
    distances = np.linalg.norm(bone_coords - socket_com, axis=1)
    furthest_idx = np.argmax(distances)
    furthest_point = bone_coords[furthest_idx]
    max_distance = distances[furthest_idx]

    line_points = bresenham_line_3d(socket_com.astype(int), furthest_point)

    intersection_idx = None
    intersection_point = None
    for i, pt in enumerate(line_points):
        z, y, x = pt
        if 0 <= z < bone_mask.shape[0] and 0 <= y < bone_mask.shape[1] and 0 <= x < bone_mask.shape[2]:
            if bone_mask[z, y, x] > 0:
                intersection_idx = i
                intersection_point = pt
                break

    if intersection_idx is None:
        socket_segment = line_points.copy()
        bone_segment = np.array([]).reshape(0, 3)
    else:
        socket_segment = line_points[:intersection_idx]
        bone_segment = line_points[intersection_idx:]

    bone_length_euclidean = 0.0
    if len(bone_segment) > 1:
        segments = np.diff(bone_segment, axis=0)
        bone_length_euclidean = float(np.sum(np.linalg.norm(segments, axis=1)))

    socket_length_euclidean = 0.0
    if len(socket_segment) > 1:
        segments = np.diff(socket_segment, axis=0)
        socket_length_euclidean = float(np.sum(np.linalg.norm(segments, axis=1)))

    # Visualization
    viz = np.zeros_like(bone_mask, dtype=np.uint8)
    viz[bone_mask > 0] = 1
    viz[socket_mask > 0] = 2
    for pt in socket_segment:
        z, y, x = pt
        if 0 <= z < viz.shape[0] and 0 <= y < viz.shape[1] and 0 <= x < viz.shape[2]:
            viz[z, y, x] = 3
    for pt in bone_segment:
        z, y, x = pt
        if 0 <= z < viz.shape[0] and 0 <= y < viz.shape[1] and 0 <= x < viz.shape[2]:
            viz[z, y, x] = 4
    z, y, x = furthest_point
    viz[z, y, x] = 5
    z, y, x = socket_com.astype(int)
    if 0 <= z < viz.shape[0] and 0 <= y < viz.shape[1] and 0 <= x < viz.shape[2]:
        viz[z, y, x] = 6
    if intersection_point is not None:
        z, y, x = intersection_point
        if 0 <= z < viz.shape[0] and 0 <= y < viz.shape[1] and 0 <= x < viz.shape[2]:
            viz[z, y, x] = 7

    # BV/TV
    TV_primary = int(np.sum(bone_mask))
    BV_primary = int(np.sum((bone_mask > 0) & (image_data >= INTENSITY_THRESHOLD)))
    BV_TV_primary = float(BV_primary / TV_primary) if TV_primary > 0 else 0.0

    TV_secondary = int(np.sum(secondary_mask))
    BV_secondary = int(np.sum((secondary_mask > 0) & (image_data >= INTENSITY_THRESHOLD)))
    BV_TV_secondary = float(BV_secondary / TV_secondary) if TV_secondary > 0 else 0.0

    metrics = {
        'socket_com': socket_com.tolist(),
        'furthest_point': furthest_point.tolist(),
        'first_intersection': intersection_point.tolist() if intersection_point is not None else None,
        'euclidean_distance_total': float(max_distance),
        'bone_length_euclidean': bone_length_euclidean,
        'socket_length_euclidean': socket_length_euclidean,
        'socket_volume': int(np.sum(socket_mask)),
        'bone_volume': int(np.sum(bone_mask)),
        'TV_primary': TV_primary,
        'BV_primary': BV_primary,
        'BV_TV_primary': BV_TV_primary,
        'TV_secondary': TV_secondary,
        'BV_secondary': BV_secondary,
        'BV_TV_secondary': BV_TV_secondary,
    }
    return viz, metrics


def stage_5_bone_length(target):
    print_header("STAGE 5: BONE LENGTH")

    socket_dir = target / "labelsSocketDetected_ds"
    phalanx_dir = target / "labelsShrunk_50_ds"
    labels_dir = target / "labels_ds"
    images_dir = target / "images_ds"
    metrics_dir = ensure_dir(target / "metrics")
    output_dir = ensure_dir(target / "labelsBoneLength_v2_ds")

    socket_files = sorted(socket_dir.glob("*_socket.nii.gz"))
    print(f"Processing {len(socket_files)} samples...")

    all_metrics = []
    for idx, sf in enumerate(socket_files, 1):
        sample_name = sf.stem.replace('_socket', '').replace('.nii', '')
        print(f"  [{idx}/{len(socket_files)}] {sample_name}")

        phalanx_file = phalanx_dir / f"{sample_name}_shrunk.nii.gz"
        labels_file = labels_dir / f"{sample_name}.nii.gz"

        # Find image file
        image_file = images_dir / f"{sample_name}.nii.gz"
        if not image_file.exists():
            matches = list(images_dir.glob(f"{sample_name}*.nii.gz"))
            image_file = matches[0] if matches else None

        if not phalanx_file.exists() or not labels_file.exists() or not image_file:
            print(f"      SKIPPED - missing files")
            continue

        socket_data, affine, header = load_nifti(sf)
        socket_mask = (socket_data > 0).astype(np.uint8)
        phalanx_data, _, _ = load_nifti(phalanx_file)
        phalanx_mask = (phalanx_data > 0).astype(np.uint8)
        labels_data, _, _ = load_nifti(labels_file)
        labels_mask = (labels_data > 0).astype(np.uint8)
        image_data, _, _ = load_nifti(image_file)

        viz, metrics = measure_bone_length_v2(socket_mask, labels_mask, image_data, phalanx_mask)
        if viz is not None:
            save_nifti(viz, affine, header, output_dir / f"{sample_name}_bone_length_v2.nii.gz")
            metrics['sample_name'] = sample_name
            all_metrics.append(metrics)
            print(f"      Bone length: {metrics['bone_length_euclidean']:.1f} voxels")

    save_metrics(all_metrics, metrics_dir / "bone_length_metrics_v2_ds.json")
    print(f"Done. Output: {output_dir}")
    return len(socket_files)


# ============================================================
# STAGE 6: UPSAMPLE & SCALE METRICS
# ============================================================
def scale_metrics(metrics, factor):
    scaled = metrics.copy()
    volume_fields = ['socket_volume', 'bone_volume', 'TV_primary', 'BV_primary', 'TV_secondary', 'BV_secondary']
    for field in volume_fields:
        if field in scaled and scaled[field] is not None:
            scaled[field] = int(scaled[field] * (factor ** 3))

    length_fields = ['euclidean_distance_total', 'bone_length_euclidean', 'socket_length_euclidean']
    for field in length_fields:
        if field in scaled and scaled[field] is not None:
            scaled[field] = float(scaled[field] * factor)

    coord_fields = ['socket_com', 'furthest_point', 'first_intersection']
    for field in coord_fields:
        if field in scaled and scaled[field] is not None:
            scaled[field] = [c * factor for c in scaled[field]]

    return scaled


def stage_6_upsample(target):
    print_header("STAGE 6: UPSAMPLE & SCALE METRICS")

    metrics_dir = target / "metrics"
    bone_ds_dir = target / "labelsBoneLength_v2_ds"
    labels_orig_dir = target / "labels"
    output_dir = ensure_dir(target / "labelsBoneLength_v2")

    # Load shape info
    with open(metrics_dir / "downsample_info.json", 'r') as f:
        ds_info = json.load(f)
    shape_lookup = {s['sample_name']: s for s in ds_info['samples']}

    # Upsample visualizations
    ds_files = sorted(bone_ds_dir.glob("*_bone_length_v2.nii.gz"))
    print(f"Upsampling {len(ds_files)} files...")

    for idx, dsf in enumerate(ds_files, 1):
        sample_name = dsf.stem.replace('_bone_length_v2', '').replace('.nii', '')
        if sample_name not in shape_lookup:
            continue

        ref_shape = tuple(shape_lookup[sample_name]['original_shape'])
        ref_affine = np.array(shape_lookup[sample_name]['original_affine'])

        ds_data, _, ds_header = load_nifti(dsf)
        zoom_factors = [ref_shape[i] / ds_data.shape[i] for i in range(3)]
        upsampled = ndimage.zoom(ds_data.astype(np.float32), zoom_factors, order=0).astype(np.uint8)

        if upsampled.shape != ref_shape:
            result = np.zeros(ref_shape, dtype=np.uint8)
            slices = tuple(slice(0, min(s1, s2)) for s1, s2 in zip(upsampled.shape, ref_shape))
            result[slices] = upsampled[slices]
            upsampled = result

        save_nifti(upsampled, ref_affine, ds_header, output_dir / f"{sample_name}_bone_length_v2.nii.gz")
        print(f"  [{idx}/{len(ds_files)}] {sample_name}: {ds_data.shape} -> {ref_shape}")

    # Scale metrics
    v2_ds_file = metrics_dir / "bone_length_metrics_v2_ds.json"
    if v2_ds_file.exists():
        with open(v2_ds_file, 'r') as f:
            v2_metrics_ds = json.load(f)
        v2_scaled = [scale_metrics(m, DOWNSAMPLE_FACTOR) for m in v2_metrics_ds]
        save_metrics(v2_scaled, metrics_dir / "bone_length_metrics_v2.json")
        print(f"Scaled {len(v2_scaled)} metric records")

    print(f"Done. Output: {output_dir}")
    return len(ds_files)


# ============================================================
# STAGE 7: EXCEL EXPORT
# ============================================================
def stage_7_excel(target):
    print_header("STAGE 7: EXCEL EXPORT")

    try:
        import pandas as pd
    except ImportError:
        print("pandas not installed - skipping Excel export")
        return 0

    metrics_dir = target / "metrics"
    output_file = target / "output.xlsx"

    # Load metrics
    socket_file = metrics_dir / "socket_metrics_ds.json"
    bone_v2_file = metrics_dir / "bone_length_metrics_v2.json"

    if not socket_file.exists():
        print("Socket metrics not found - skipping")
        return 0

    with open(socket_file, 'r') as f:
        socket_data = json.load(f)

    bone_v2_dict = {}
    if bone_v2_file.exists():
        with open(bone_v2_file, 'r') as f:
            for item in json.load(f):
                bone_v2_dict[item.get('sample_name', '')] = item

    rows = []
    for s in socket_data:
        sample_name = s.get('sample_name', '')
        socket_vol = s.get('socket_volume', 0) * (DOWNSAMPLE_FACTOR ** 3)
        socket_rad = s.get('equivalent_radius')
        if socket_rad:
            socket_rad *= DOWNSAMPLE_FACTOR

        row = {
            'Sample': sample_name,
            'Socket_Volume_voxels': socket_vol,
            'Socket_Volume_mm3': socket_vol * VOXEL_VOLUME_MM3,
            'Socket_Radius_voxels': socket_rad,
            'Socket_Radius_mm': socket_rad * VOXEL_SIZE_MM if socket_rad else None,
        }

        if sample_name in bone_v2_dict:
            b = bone_v2_dict[sample_name]
            bone_len = b.get('bone_length_euclidean', 0)
            row['Bone_Length_voxels'] = bone_len
            row['Bone_Length_mm'] = bone_len * VOXEL_SIZE_MM
            row['BV_TV_primary'] = b.get('BV_TV_primary')

        rows.append(row)

    df = pd.DataFrame(rows)

    # Summary stats
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    stats = []
    for col in numeric_cols:
        data = df[col].dropna()
        if len(data) > 0:
            stats.append({
                'Metric': col, 'Count': len(data), 'Mean': data.mean(),
                'Std': data.std(), 'Min': data.min(), 'Max': data.max()
            })
    stats_df = pd.DataFrame(stats)

    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='All_Measurements', index=False)
        stats_df.to_excel(writer, sheet_name='Summary_Statistics', index=False)

    print(f"Exported {len(df)} samples to {output_file}")
    return len(df)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='Run digitpipe_v3 pipeline')
    parser.add_argument('target_dir', help='Target directory with labels/ and images/ subdirectories')
    parser.add_argument('--skip-viz', action='store_true', help='Skip GIF visualization stage')
    args = parser.parse_args()

    target = Path(args.target_dir).resolve()

    print("=" * 70)
    print("DIGITPIPE V3 PIPELINE")
    print("=" * 70)
    print(f"Target: {target}")

    # Validate
    labels_dir = target / "labels"
    images_dir = target / "images"
    if not labels_dir.exists():
        print(f"ERROR: Labels directory not found: {labels_dir}")
        sys.exit(1)
    if not images_dir.exists():
        print(f"ERROR: Images directory not found: {images_dir}")
        sys.exit(1)

    n_labels = len(list(labels_dir.glob("*.nii.gz")))
    n_images = len(list(images_dir.glob("*.nii.gz")))
    print(f"Labels: {n_labels}, Images: {n_images}")

    # Run pipeline
    total_start = time.time()

    stages = [
        ("Downsample", stage_0_downsample),
        ("Hull Generation", stage_1_hull),
        ("Shrink-Wrap", stage_2_shrink),
        ("Major Axis", stage_3_axis),
        ("Socket Detection", stage_4_socket),
        ("Bone Length", stage_5_bone_length),
        ("Upsample", stage_6_upsample),
        ("Excel Export", stage_7_excel),
    ]

    results = []
    for name, func in stages:
        stage_start = time.time()
        try:
            count = func(target)
            elapsed = time.time() - stage_start
            results.append((name, "OK", count, elapsed))
        except Exception as e:
            elapsed = time.time() - stage_start
            results.append((name, "FAIL", 0, elapsed))
            print(f"ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()

    total_elapsed = time.time() - total_start

    # Summary
    print_header("PIPELINE COMPLETE")
    print(f"Total time: {timedelta(seconds=int(total_elapsed))}")
    print("\nResults:")
    for name, status, count, elapsed in results:
        print(f"  [{status}] {name}: {count} items ({elapsed:.1f}s)")

    output_file = target / "output.xlsx"
    if output_file.exists():
        print(f"\nFinal output: {output_file}")


if __name__ == "__main__":
    main()
