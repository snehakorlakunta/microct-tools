"""Publication-oriented 3D and oblique-slice figures."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from matplotlib.colors import ListedColormap, to_rgb
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PIL import Image
from scipy import ndimage
from skimage.measure import marching_cubes

from .digit_length import DigitLengthResult


COLORS = {
    "tv": "#56B4E9",
    "bv": "#D55E00",
    "length": "#0047AB",
    "concavity": "#CC79A7",
    "centroid": "#FFFFFF",
    "endpoint": "#009E73",
}


def _voxel_to_world(points: np.ndarray, affine: np.ndarray) -> np.ndarray:
    return points @ affine[:3, :3].T + affine[:3, 3]


def _display_frame(
    roi_mask: np.ndarray, affine: np.ndarray, result: DigitLengthResult
) -> tuple[np.ndarray, np.ndarray]:
    origin = result.concavity_centroid_world_mm
    longitudinal = result.distal_endpoint_world_mm - origin
    longitudinal /= np.linalg.norm(longitudinal)
    coords = np.argwhere(roi_mask)
    if len(coords) > 100_000:
        coords = coords[:: int(np.ceil(len(coords) / 100_000))]
    world = _voxel_to_world(coords, affine)
    covariance = np.cov((world - world.mean(axis=0)).T)
    _, eigenvectors = np.linalg.eigh(covariance)
    candidates = eigenvectors[:, ::-1].T
    side = max(candidates, key=lambda vector: 1.0 - abs(float(vector @ longitudinal)))
    side = side - longitudinal * float(side @ longitudinal)
    side /= np.linalg.norm(side)
    references = [affine[:3, index] for index in range(3)]
    reference = max(references, key=lambda vector: abs(float(vector @ side)))
    if float(reference @ side) < 0:
        side *= -1
    vertical = np.cross(longitudinal, side)
    vertical /= np.linalg.norm(vertical)
    # Display x=side, y=longitudinal, z=vertical. The measurement direction is
    # therefore identical across samples before applying the fixed camera.
    basis = np.column_stack((side, longitudinal, vertical))
    return origin, basis


def _native_xy_camera_frame(
    affine: np.ndarray, result: DigitLengthResult
) -> tuple[np.ndarray, np.ndarray]:
    """Align the digit in native XY, then permit a fixed 30-degree camera tilt."""
    origin = result.concavity_centroid_world_mm
    axis_i = affine[:3, 0] / np.linalg.norm(affine[:3, 0])
    axis_j = affine[:3, 1] / np.linalg.norm(affine[:3, 1])
    normal = np.cross(axis_i, axis_j)
    normal /= np.linalg.norm(normal)
    direction = result.distal_endpoint_world_mm - origin
    horizontal = direction - normal * float(direction @ normal)
    if np.linalg.norm(horizontal) < 1e-8:
        horizontal = axis_i.copy()
    horizontal /= np.linalg.norm(horizontal)
    if float(horizontal @ direction) < 0:
        horizontal *= -1
    vertical = np.cross(normal, horizontal)
    vertical /= np.linalg.norm(vertical)
    basis = np.column_stack((horizontal, vertical, normal))
    return origin, basis


def _transform(points: np.ndarray, origin: np.ndarray, basis: np.ndarray) -> np.ndarray:
    return (points - origin) @ basis


def _crop(mask: np.ndarray, padding: int = 4) -> tuple[tuple[slice, slice, slice], np.ndarray]:
    coords = np.argwhere(mask)
    lower = np.maximum(coords.min(axis=0) - padding, 0)
    upper = np.minimum(coords.max(axis=0) + padding + 1, mask.shape)
    slices = tuple(slice(int(lo), int(hi)) for lo, hi in zip(lower, upper))
    return slices, lower


def _mesh(
    ax,
    mask: np.ndarray,
    affine: np.ndarray,
    origin: np.ndarray,
    basis: np.ndarray,
    color: str,
    alpha: float,
    *,
    max_dimension: int = 110,
    zorder: int = 1,
    shaded: bool = False,
) -> None:
    if np.count_nonzero(mask) < 4:
        return
    slices, lower = _crop(mask)
    volume = mask[slices].astype(np.uint8)
    factor = max(1, int(np.ceil(max(volume.shape) / max_dimension)))
    reduced = volume[::factor, ::factor, ::factor]
    padded = np.pad(reduced, 1)
    vertices, faces, _, _ = marching_cubes(padded, level=0.5)
    vertices = (vertices - 1.0) * factor + lower
    world = _voxel_to_world(vertices, affine)
    shown = _transform(world, origin, basis)
    triangles = shown[faces]
    collection = Poly3DCollection(triangles, linewidths=0.04)
    if shaded:
        edge_a = triangles[:, 1] - triangles[:, 0]
        edge_b = triangles[:, 2] - triangles[:, 0]
        normals = np.cross(edge_a, edge_b)
        norm = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = normals / np.maximum(norm, 1e-12)
        light = np.asarray((0.25, -0.45, 0.86), dtype=float)
        light /= np.linalg.norm(light)
        illumination = 0.32 + 0.68 * (0.25 + 0.75 * np.abs(normals @ light))
        base = np.asarray(to_rgb(color))
        rgba = np.column_stack((illumination[:, None] * base[None, :], np.full(len(faces), alpha)))
        collection.set_facecolor(rgba)
        collection.set_edgecolor("none")
    else:
        collection.set_facecolor(color)
        collection.set_edgecolor(color)
        collection.set_alpha(alpha)
    collection.set_zorder(zorder)
    ax.add_collection3d(collection)


def _voxel_cloud(
    ax,
    mask: np.ndarray,
    affine: np.ndarray,
    origin: np.ndarray,
    basis: np.ndarray,
    color: str,
    *,
    maximum_points: int = 6000,
    point_size: float = 3.0,
    alpha: float = 0.30,
    zorder: int = 2,
) -> None:
    voxels = np.argwhere(mask)
    if not len(voxels):
        return
    stride = max(1, int(np.ceil(len(voxels) / maximum_points)))
    shown = _transform(_voxel_to_world(voxels[::stride], affine), origin, basis)
    ax.scatter(
        shown[:, 0], shown[:, 1], shown[:, 2], s=point_size,
        color=color, alpha=alpha, depthshade=False, linewidths=0, zorder=zorder,
    )


def _equal_axes(ax, points: np.ndarray) -> None:
    lower = points.min(axis=0)
    upper = points.max(axis=0)
    span = np.maximum(upper - lower, 1e-6)
    padding = np.maximum(span * 0.035, np.max(span) * 0.008)
    ax.set_xlim(lower[0] - padding[0], upper[0] + padding[0])
    ax.set_ylim(lower[1] - padding[1], upper[1] + padding[1])
    ax.set_zlim(lower[2] - padding[2], upper[2] + padding[2])
    # Matching the axes box to the physical extents preserves metric shape and
    # avoids the large empty margins produced by enclosing a thin digit in a cube.
    ax.set_box_aspect(tuple(span + 2 * padding), zoom=1.28)
    ax.set_axis_off()
    # Face-on native XY is elev=90. Elev=60 tilts 30 degrees away from it;
    # azim=-90 rotates about the displayed digit-length axis.
    ax.view_init(elev=60, azim=-90)


def _counted_line_runs(result: DigitLengthResult, affine: np.ndarray) -> list[np.ndarray]:
    voxels = np.argwhere(result.counted_line_mask)
    if not len(voxels):
        return []
    world = _voxel_to_world(voxels, affine)
    direction = result.distal_endpoint_world_mm - result.concavity_centroid_world_mm
    order = np.argsort((world - result.concavity_centroid_world_mm) @ direction)
    ordered = world[order]
    maximum_step = max(np.linalg.norm(affine[:3, axis]) for axis in range(3)) * 2.1
    breaks = np.where(np.linalg.norm(np.diff(ordered, axis=0), axis=1) > maximum_step)[0] + 1
    return [run for run in np.split(ordered, breaks) if len(run)]


def _save_pair(figure: plt.Figure, base_path: Path, dpi: int) -> None:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    png_path = base_path.with_suffix(".png")
    figure.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    # Reuse the rendered pixels instead of making Matplotlib draw an expensive
    # 3D scene twice. PNG and TIFF therefore contain the exact same figure.
    with Image.open(png_path) as rendered:
        rendered.convert("RGB").save(
            base_path.with_suffix(".tiff"),
            format="TIFF",
            compression="tiff_lzw",
            dpi=(dpi, dpi),
        )


def save_3d_figure(
    *,
    image: np.ndarray,
    roi_mask: np.ndarray,
    bv_mask: np.ndarray,
    affine: np.ndarray,
    result: DigitLengthResult,
    threshold: float,
    output_base: Path,
    dpi: int = 300,
) -> None:
    origin, basis = _native_xy_camera_frame(affine, result)
    roi_points = np.argwhere(roi_mask)
    shown_roi = _transform(
        _voxel_to_world(
            roi_points[:: max(1, len(roi_points) // 50_000)], affine
        ),
        origin,
        basis,
    )
    roi_slices, _ = _crop(roi_mask, padding=8)
    original_bone = np.zeros_like(roi_mask, dtype=bool)
    original_bone[roi_slices] = image[roi_slices] >= threshold

    figure = plt.figure(figsize=(15, 4.15))
    axes = [
        figure.add_subplot(1, 4, index, projection="3d", computed_zorder=False)
        for index in range(1, 5)
    ]
    titles = [
        f"A  Original bone (CT ≥ {threshold:g})",
        "B  TV segmentation",
        "C  Digit length audit",
        "D  BV/TV + digit length",
    ]
    for ax, title in zip(axes, titles):
        ax.set_title(title, fontsize=10.5, fontweight="bold", pad=0)
        _equal_axes(ax, shown_roi)

    _mesh(axes[0], original_bone, affine, origin, basis, "#6F7782", 0.58)
    _mesh(axes[1], roi_mask, affine, origin, basis, COLORS["tv"], 0.40)
    _mesh(axes[2], roi_mask, affine, origin, basis, COLORS["tv"], 0.035)
    _voxel_cloud(
        axes[2], result.concavity_mask, affine, origin, basis,
        COLORS["concavity"], alpha=0.24, zorder=3,
    )
    _mesh(axes[3], roi_mask, affine, origin, basis, COLORS["tv"], 0.035)
    _voxel_cloud(
        axes[3], bv_mask, affine, origin, basis, COLORS["bv"],
        maximum_points=18_000, point_size=2.0, alpha=0.13, zorder=2,
    )

    for line_world in _counted_line_runs(result, affine):
        line = _transform(line_world, origin, basis)
        for ax in (axes[2], axes[3]):
            ax.plot(
                line[:, 0], line[:, 1], line[:, 2], color=COLORS["length"],
                linewidth=4.0, zorder=10,
            )

    landmarks = _transform(
        np.vstack((result.concavity_centroid_world_mm, result.distal_endpoint_world_mm)),
        origin,
        basis,
    )
    axes[2].scatter(
        *landmarks[0], s=100, color=COLORS["centroid"], edgecolor="black",
        linewidth=0.5, depthshade=False, zorder=20,
    )
    axes[2].scatter(
        *landmarks[1], s=100, color=COLORS["endpoint"], edgecolor="black",
        linewidth=0.5, depthshade=False, zorder=20,
    )
    legend = [
        Patch(facecolor=COLORS["tv"], alpha=0.35, label="TV"),
        Patch(facecolor=COLORS["bv"], alpha=0.8, label="BV within TV"),
        Line2D([0], [0], color=COLORS["length"], lw=3, label="Digit length (counted)"),
        Patch(facecolor=COLORS["concavity"], alpha=0.7, label="Largest concavity"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["centroid"], markeredgecolor="black", label="Concavity centroid"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["endpoint"], markeredgecolor="black", label="Distal endpoint"),
    ]
    figure.subplots_adjust(left=0.005, right=0.995, bottom=0.14, top=0.88, wspace=-0.10)
    figure.legend(
        handles=legend, loc="lower center", bbox_to_anchor=(0.5, 0.015),
        ncol=6, fontsize=7.8, frameon=False,
    )
    _save_pair(figure, output_base, dpi)


def _sample_plane(
    volume: np.ndarray,
    affine: np.ndarray,
    center: np.ndarray,
    horizontal: np.ndarray,
    vertical: np.ndarray,
    horizontal_limits: tuple[float, float],
    vertical_limits: tuple[float, float],
    step_mm: float,
    order: int,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    width = min(800, max(2, int(np.ceil((horizontal_limits[1] - horizontal_limits[0]) / step_mm))))
    height = min(800, max(2, int(np.ceil((vertical_limits[1] - vertical_limits[0]) / step_mm))))
    xs = np.linspace(*horizontal_limits, width)
    ys = np.linspace(*vertical_limits, height)
    grid_x, grid_y = np.meshgrid(xs, ys)
    world = center[:, None, None] + horizontal[:, None, None] * grid_x + vertical[:, None, None] * grid_y
    inverse = np.linalg.inv(affine)
    flat = world.reshape(3, -1)
    voxel = inverse[:3, :3] @ flat + inverse[:3, 3:4]
    sampled = ndimage.map_coordinates(volume, voxel, order=order, mode="constant", cval=0.0)
    return sampled.reshape(height, width), (xs[0], xs[-1], ys[0], ys[-1])


def _nice_scale_bar(width_mm: float) -> float:
    candidates = np.asarray([0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0])
    valid = candidates[candidates <= width_mm * 0.3]
    return float(valid[-1] if len(valid) else candidates[0])


def save_2d_figure(
    *,
    image: np.ndarray,
    roi_mask: np.ndarray,
    bv_mask: np.ndarray,
    affine: np.ndarray,
    result: DigitLengthResult,
    output_base: Path,
    dpi: int = 300,
) -> None:
    """Save parallel four-stage longitudinal and native-XY views."""
    origin, basis = _display_frame(roi_mask, affine, result)
    side, longitudinal, vertical = basis.T
    roi_world = _voxel_to_world(np.argwhere(roi_mask), affine)
    concavity_world = _voxel_to_world(np.argwhere(result.concavity_mask), affine)
    combined_world = np.vstack((roi_world, concavity_world))
    projections = (combined_world - origin) @ basis
    padding = max(0.04, float(np.max(np.ptp(projections, axis=0))) * 0.06)
    step = float(min(np.linalg.norm(affine[:3, axis]) for axis in range(3)))
    h_limits = (float(projections[:, 0].min() - padding), float(projections[:, 0].max() + padding))
    v_limits = (float(projections[:, 1].min() - padding), float(projections[:, 1].max() + padding))
    longitudinal_arrays: dict[str, np.ndarray] = {}
    extent: tuple[float, float, float, float] | None = None
    for name, volume, order in (
        ("source", image, 1),
        ("tv", roi_mask.astype(float), 0),
        ("bv", bv_mask.astype(float), 0),
        ("concavity", result.concavity_mask.astype(float), 0),
    ):
        sampled, sampled_extent = _sample_plane(
            volume, affine, origin, side, longitudinal,
            h_limits, v_limits, step, order,
        )
        longitudinal_arrays[name] = sampled
        extent = sampled_extent
    assert extent is not None

    native_k = int(np.clip(round(result.concavity_centroid_voxel[2]), 0, image.shape[2] - 1))
    native_union = roi_mask[:, :, native_k] | result.concavity_mask[:, :, native_k]
    native_coords = np.argwhere(native_union)
    if not len(native_coords):
        native_k = int(np.argmax(np.sum(roi_mask, axis=(0, 1))))
        native_coords = np.argwhere(roi_mask[:, :, native_k])
    lower = np.maximum(native_coords.min(axis=0) - 12, 0)
    upper = np.minimum(native_coords.max(axis=0) + 13, native_union.shape)
    native_slice = (slice(int(lower[0]), int(upper[0])), slice(int(lower[1]), int(upper[1])))
    line_lower = max(0, native_k - 2)
    line_upper = min(image.shape[2], native_k + 3)
    native_arrays = {
        "source": image[native_slice[0], native_slice[1], native_k].T,
        "tv": roi_mask[native_slice[0], native_slice[1], native_k].T.astype(float),
        "bv": bv_mask[native_slice[0], native_slice[1], native_k].T.astype(float),
        "concavity": result.concavity_mask[native_slice[0], native_slice[1], native_k].T.astype(float),
        "line": np.any(
            result.counted_line_mask[native_slice[0], native_slice[1], line_lower:line_upper],
            axis=2,
        ).T,
    }
    flip_native = result.distal_endpoint_voxel[0] < result.concavity_centroid_voxel[0]
    if flip_native:
        native_arrays = {name: np.fliplr(values) for name, values in native_arrays.items()}
    spacing_i = float(np.linalg.norm(affine[:3, 0]))
    spacing_j = float(np.linalg.norm(affine[:3, 1]))
    native_extent = (
        0.0,
        native_arrays["source"].shape[1] * spacing_i,
        0.0,
        native_arrays["source"].shape[0] * spacing_j,
    )

    figure, axes = plt.subplots(2, 4, figsize=(13.8, 7.2))
    titles = [
        "A  Original CT/bone",
        "B  TV segmentation",
        "C  Digit length audit",
        "D  BV/TV + digit length",
    ]

    def draw_mask(ax, values: np.ndarray, shown_extent, color: str, alpha: float) -> None:
        masked = np.ma.masked_where(values < 0.5, values)
        ax.imshow(
            masked, cmap=ListedColormap([color]), alpha=alpha,
            origin="lower", extent=shown_extent, interpolation="nearest",
        )

    def draw_tv_boundary(ax, values: np.ndarray, shown_extent) -> None:
        if np.any(values >= 0.5):
            ax.contour(
                values, levels=[0.5], colors=[COLORS["tv"]], linewidths=1.35,
                origin="lower", extent=shown_extent,
            )

    def draw_scale_bar(ax, shown_extent) -> None:
        bar = _nice_scale_bar(shown_extent[1] - shown_extent[0])
        x0 = shown_extent[0] + 0.07 * (shown_extent[1] - shown_extent[0])
        y0 = shown_extent[2] + 0.07 * (shown_extent[3] - shown_extent[2])
        ax.plot([x0, x0 + bar], [y0, y0], color="white", linewidth=3, solid_capstyle="butt")
        label = f"{bar * 1000:g} µm" if bar < 1 else f"{bar:g} mm"
        ax.text(
            x0 + bar / 2, y0 + 0.022 * (shown_extent[3] - shown_extent[2]),
            label, color="white", ha="center", va="bottom", fontsize=7.5,
        )

    for column, ax in enumerate(axes[0]):
        ax.imshow(
            longitudinal_arrays["source"], cmap="gray", vmin=0, vmax=255,
            origin="lower", extent=extent, interpolation="nearest",
        )
        if column == 1:
            draw_mask(ax, longitudinal_arrays["tv"], extent, COLORS["tv"], 0.28)
            draw_tv_boundary(ax, longitudinal_arrays["tv"], extent)
        elif column == 2:
            draw_tv_boundary(ax, longitudinal_arrays["tv"], extent)
            draw_mask(ax, longitudinal_arrays["concavity"], extent, COLORS["concavity"], 0.52)
        elif column == 3:
            draw_tv_boundary(ax, longitudinal_arrays["tv"], extent)
            draw_mask(ax, longitudinal_arrays["bv"], extent, COLORS["bv"], 0.50)
        if column in (2, 3):
            for line_world in _counted_line_runs(result, affine):
                relative_line = line_world - origin
                ax.plot(
                    relative_line @ side, relative_line @ longitudinal,
                    color=COLORS["length"], linewidth=2.2,
                )
        if column == 2:
            landmark_world = np.vstack(
                (result.concavity_centroid_world_mm, result.distal_endpoint_world_mm)
            ) - origin
            ax.scatter(
                landmark_world[0] @ side, landmark_world[0] @ longitudinal,
                s=34, color=COLORS["centroid"], edgecolor="black", linewidth=0.4, zorder=5,
            )
            ax.scatter(
                landmark_world[1] @ side, landmark_world[1] @ longitudinal,
                s=34, color=COLORS["endpoint"], edgecolor="black", linewidth=0.4, zorder=5,
            )
        if column == 0:
            draw_scale_bar(ax, extent)

    centroid_native = result.concavity_centroid_voxel.copy()
    endpoint_native = result.distal_endpoint_voxel.astype(float)
    for point in (centroid_native, endpoint_native):
        point[0] -= lower[0]
        point[1] -= lower[1]
        if flip_native:
            point[0] = native_arrays["source"].shape[1] - 1 - point[0]

    for column, ax in enumerate(axes[1]):
        ax.imshow(
            native_arrays["source"], cmap="gray", vmin=0, vmax=255,
            origin="lower", extent=native_extent, interpolation="nearest",
        )
        if column == 1:
            draw_mask(ax, native_arrays["tv"], native_extent, COLORS["tv"], 0.28)
            draw_tv_boundary(ax, native_arrays["tv"], native_extent)
        elif column == 2:
            draw_tv_boundary(ax, native_arrays["tv"], native_extent)
            draw_mask(ax, native_arrays["concavity"], native_extent, COLORS["concavity"], 0.52)
        elif column == 3:
            draw_tv_boundary(ax, native_arrays["tv"], native_extent)
            draw_mask(ax, native_arrays["bv"], native_extent, COLORS["bv"], 0.50)
        if column in (2, 3):
            line_for_display = ndimage.binary_dilation(native_arrays["line"], iterations=1)
            draw_mask(ax, line_for_display, native_extent, COLORS["length"], 0.95)
        if column == 2:
            if abs(result.concavity_centroid_voxel[2] - native_k) <= 1:
                ax.scatter(
                    (centroid_native[0] + 0.5) * spacing_i,
                    (centroid_native[1] + 0.5) * spacing_j,
                    s=34, color=COLORS["centroid"], edgecolor="black", linewidth=0.4, zorder=5,
                )
            if abs(result.distal_endpoint_voxel[2] - native_k) <= 2:
                ax.scatter(
                    (endpoint_native[0] + 0.5) * spacing_i,
                    (endpoint_native[1] + 0.5) * spacing_j,
                    s=34, color=COLORS["endpoint"], edgecolor="black", linewidth=0.4, zorder=5,
                )
        if column == 0:
            draw_scale_bar(ax, native_extent)

    for column, title in enumerate(titles):
        axes[0, column].set_title(title, fontsize=10, fontweight="bold", pad=3)
    axes[0, 0].text(
        -0.08, 0.5, "Longitudinal", transform=axes[0, 0].transAxes,
        rotation=90, ha="center", va="center", fontsize=9, fontweight="bold",
    )
    axes[1, 0].text(
        -0.08, 0.5, f"Native XY (k={native_k})", transform=axes[1, 0].transAxes,
        rotation=90, ha="center", va="center", fontsize=9, fontweight="bold",
    )
    for ax in axes.ravel():
        ax.set_axis_off()
        ax.set_aspect("equal")
    legend = [
        Line2D([0], [0], color=COLORS["tv"], lw=2, label="TV boundary"),
        Patch(facecolor=COLORS["bv"], alpha=0.5, label="BV within TV"),
        Line2D([0], [0], color=COLORS["length"], lw=2, label="Digit length (counted)"),
        Patch(facecolor=COLORS["concavity"], alpha=0.6, label="Largest concavity"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["centroid"], markeredgecolor="black", label="Concavity centroid"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["endpoint"], markeredgecolor="black", label="Distal endpoint"),
    ]
    figure.subplots_adjust(left=0.035, right=0.995, bottom=0.10, top=0.93, wspace=0.025, hspace=0.035)
    figure.legend(
        handles=legend, loc="lower center", bbox_to_anchor=(0.5, 0.012),
        ncol=6, frameon=False, fontsize=7.8,
    )
    _save_pair(figure, output_base, dpi)


def save_publication_figure(
    *,
    case_id: str,
    image: np.ndarray,
    roi_mask: np.ndarray,
    bv_mask: np.ndarray,
    affine: np.ndarray,
    result: DigitLengthResult,
    threshold: float,
    output_base: Path,
    dpi: int = 300,
) -> None:
    """Save one aligned 3D/longitudinal/cross-sectional figure per case."""
    camera_origin, camera_basis = _native_xy_camera_frame(affine, result)
    roi_points = np.argwhere(roi_mask)
    shown_roi = _transform(
        _voxel_to_world(roi_points[:: max(1, len(roi_points) // 50_000)], affine),
        camera_origin,
        camera_basis,
    )
    roi_slices, _ = _crop(roi_mask, padding=8)
    original_bone = np.zeros_like(roi_mask, dtype=bool)
    original_bone[roi_slices] = image[roi_slices] >= threshold

    plane_origin, plane_basis = _display_frame(roi_mask, affine, result)
    side, longitudinal, vertical = plane_basis.T
    roi_world = _voxel_to_world(np.argwhere(roi_mask), affine)
    concavity_world = _voxel_to_world(np.argwhere(result.concavity_mask), affine)
    projected = (np.vstack((roi_world, concavity_world)) - plane_origin) @ plane_basis
    padding = max(0.04, float(np.max(np.ptp(projected, axis=0))) * 0.06)
    step = float(min(np.linalg.norm(affine[:3, axis]) for axis in range(3)))
    side_limits = (float(projected[:, 0].min() - padding), float(projected[:, 0].max() + padding))
    length_limits = (float(projected[:, 1].min() - padding), float(projected[:, 1].max() + padding))
    longitudinal_arrays: dict[str, np.ndarray] = {}
    for name, volume, order in (
        ("source", image, 1),
        ("tv", roi_mask.astype(float), 0),
        ("bv", bv_mask.astype(float), 0),
        ("concavity", result.concavity_mask.astype(float), 0),
    ):
        sampled, _ = _sample_plane(
            volume, affine, plane_origin, side, longitudinal,
            side_limits, length_limits, step, order,
        )
        # With imshow(origin="lower"), this array rotation is the visual
        # clockwise orientation. It also matches the physical-coordinate
        # transform below, placing the digit tip on the right.
        longitudinal_arrays[name] = np.rot90(sampled, k=1)
    longitudinal_extent = (
        0.0,
        length_limits[1] - length_limits[0],
        0.0,
        side_limits[1] - side_limits[0],
    )

    native_k = int(np.clip(round(result.concavity_centroid_voxel[2]), 0, image.shape[2] - 1))
    native_union = roi_mask[:, :, native_k] | result.concavity_mask[:, :, native_k]
    native_coords = np.argwhere(native_union)
    if not len(native_coords):
        native_k = int(np.argmax(np.sum(roi_mask, axis=(0, 1))))
        native_coords = np.argwhere(roi_mask[:, :, native_k])
    lower = np.maximum(native_coords.min(axis=0) - 12, 0)
    upper = np.minimum(native_coords.max(axis=0) + 13, native_union.shape)
    native_slice = (slice(int(lower[0]), int(upper[0])), slice(int(lower[1]), int(upper[1])))
    line_lower = max(0, native_k - 2)
    line_upper = min(image.shape[2], native_k + 3)
    native_arrays = {
        "source": image[native_slice[0], native_slice[1], native_k].T,
        "tv": roi_mask[native_slice[0], native_slice[1], native_k].T.astype(float),
        "bv": bv_mask[native_slice[0], native_slice[1], native_k].T.astype(float),
        "concavity": result.concavity_mask[native_slice[0], native_slice[1], native_k].T.astype(float),
        "line": np.any(
            result.counted_line_mask[native_slice[0], native_slice[1], line_lower:line_upper],
            axis=2,
        ).T,
    }
    flip_native = result.distal_endpoint_voxel[0] < result.concavity_centroid_voxel[0]
    if flip_native:
        native_arrays = {name: np.fliplr(values) for name, values in native_arrays.items()}
    spacing_i = float(np.linalg.norm(affine[:3, 0]))
    spacing_j = float(np.linalg.norm(affine[:3, 1]))
    native_extent = (
        0.0,
        native_arrays["source"].shape[1] * spacing_i,
        0.0,
        native_arrays["source"].shape[0] * spacing_j,
    )

    figure = plt.figure(figsize=(14.2, 8.0))
    grid = figure.add_gridspec(
        3, 4, height_ratios=(0.90, 0.68, 0.76),
        left=0.042, right=0.994, bottom=0.095, top=0.952,
        wspace=0.018, hspace=-0.10,
    )
    axes_3d = [
        figure.add_subplot(grid[0, column], projection="3d", computed_zorder=False)
        for column in range(4)
    ]
    axes_long = [figure.add_subplot(grid[1, column]) for column in range(4)]
    axes_cross = [figure.add_subplot(grid[2, column]) for column in range(4)]
    titles = ("A  Original bone", "B  TV segmentation", "C  Digit length", "D  BV/TV")
    for ax, title in zip(axes_3d, titles):
        ax.set_title(title, fontsize=11, fontweight="bold", pad=-2)
        _equal_axes(ax, shown_roi)

    _mesh(
        axes_3d[0], original_bone, affine, camera_origin, camera_basis,
        "#929CA8", 0.98, max_dimension=140, shaded=True,
    )
    _mesh(axes_3d[1], roi_mask, affine, camera_origin, camera_basis, COLORS["tv"], 0.42)
    _mesh(axes_3d[2], roi_mask, affine, camera_origin, camera_basis, COLORS["tv"], 0.055)
    _voxel_cloud(
        axes_3d[2], result.concavity_mask, affine, camera_origin, camera_basis,
        COLORS["concavity"], alpha=0.28, zorder=3,
    )
    _mesh(axes_3d[3], roi_mask, affine, camera_origin, camera_basis, COLORS["tv"], 0.055)
    _voxel_cloud(
        axes_3d[3], bv_mask, affine, camera_origin, camera_basis, COLORS["bv"],
        maximum_points=20_000, point_size=2.1, alpha=0.17, zorder=2,
    )
    for line_world in _counted_line_runs(result, affine):
        line = _transform(line_world, camera_origin, camera_basis)
        axes_3d[2].plot(
            line[:, 0], line[:, 1], line[:, 2],
            color=COLORS["length"], linewidth=4.0, zorder=10,
        )
    landmarks = _transform(
        np.vstack((result.concavity_centroid_world_mm, result.distal_endpoint_world_mm)),
        camera_origin,
        camera_basis,
    )
    axes_3d[2].scatter(
        *landmarks[0], s=94, color=COLORS["centroid"], edgecolor="black",
        linewidth=0.5, depthshade=False, zorder=20,
    )
    axes_3d[2].scatter(
        *landmarks[1], s=94, color=COLORS["endpoint"], edgecolor="black",
        linewidth=0.5, depthshade=False, zorder=20,
    )

    def draw_mask(ax, values: np.ndarray, extent, color: str, alpha: float) -> None:
        masked = np.ma.masked_where(values < 0.5, values)
        ax.imshow(
            masked, cmap=ListedColormap([color]), alpha=alpha,
            origin="lower", extent=extent, interpolation="nearest",
        )

    def draw_tv_boundary(ax, values: np.ndarray, extent) -> None:
        if np.any(values >= 0.5):
            ax.contour(
                values, levels=[0.5], colors=[COLORS["tv"]], linewidths=1.35,
                origin="lower", extent=extent,
            )

    def draw_scale_bar(ax, extent) -> None:
        bar = _nice_scale_bar(extent[1] - extent[0])
        x0 = extent[0] + 0.06 * (extent[1] - extent[0])
        y0 = extent[2] + 0.07 * (extent[3] - extent[2])
        ax.plot([x0, x0 + bar], [y0, y0], color="white", linewidth=3, solid_capstyle="butt")
        label = f"{bar * 1000:g} µm" if bar < 1 else f"{bar:g} mm"
        ax.text(
            x0 + bar / 2, y0 + 0.025 * (extent[3] - extent[2]), label,
            color="white", ha="center", va="bottom", fontsize=7.4,
        )

    for column, ax in enumerate(axes_long):
        ax.imshow(
            longitudinal_arrays["source"], cmap="gray", vmin=0, vmax=255,
            origin="lower", extent=longitudinal_extent, interpolation="nearest",
        )
        if column == 1:
            draw_mask(ax, longitudinal_arrays["tv"], longitudinal_extent, COLORS["tv"], 0.28)
            draw_tv_boundary(ax, longitudinal_arrays["tv"], longitudinal_extent)
        elif column == 2:
            draw_tv_boundary(ax, longitudinal_arrays["tv"], longitudinal_extent)
            draw_mask(ax, longitudinal_arrays["concavity"], longitudinal_extent, COLORS["concavity"], 0.54)
        elif column == 3:
            draw_tv_boundary(ax, longitudinal_arrays["tv"], longitudinal_extent)
            draw_mask(ax, longitudinal_arrays["bv"], longitudinal_extent, COLORS["bv"], 0.52)
        if column == 2:
            for line_world in _counted_line_runs(result, affine):
                relative = line_world - plane_origin
                x = relative @ longitudinal - length_limits[0]
                y = side_limits[1] - relative @ side
                ax.plot(x, y, color=COLORS["length"], linewidth=2.2)
            landmark_world = np.vstack(
                (result.concavity_centroid_world_mm, result.distal_endpoint_world_mm)
            ) - plane_origin
            ax.scatter(
                landmark_world[0] @ longitudinal - length_limits[0],
                side_limits[1] - landmark_world[0] @ side,
                s=34, color=COLORS["centroid"], edgecolor="black", linewidth=0.4, zorder=5,
            )
            ax.scatter(
                landmark_world[1] @ longitudinal - length_limits[0],
                side_limits[1] - landmark_world[1] @ side,
                s=34, color=COLORS["endpoint"], edgecolor="black", linewidth=0.4, zorder=5,
            )
        if column == 0:
            draw_scale_bar(ax, longitudinal_extent)

    centroid_native = result.concavity_centroid_voxel.copy()
    endpoint_native = result.distal_endpoint_voxel.astype(float).copy()
    for point in (centroid_native, endpoint_native):
        point[0] -= lower[0]
        point[1] -= lower[1]
        if flip_native:
            point[0] = native_arrays["source"].shape[1] - 1 - point[0]
    for column, ax in enumerate(axes_cross):
        ax.imshow(
            native_arrays["source"], cmap="gray", vmin=0, vmax=255,
            origin="lower", extent=native_extent, interpolation="nearest",
        )
        if column == 1:
            draw_mask(ax, native_arrays["tv"], native_extent, COLORS["tv"], 0.28)
            draw_tv_boundary(ax, native_arrays["tv"], native_extent)
        elif column == 2:
            draw_tv_boundary(ax, native_arrays["tv"], native_extent)
            draw_mask(ax, native_arrays["concavity"], native_extent, COLORS["concavity"], 0.54)
        elif column == 3:
            draw_tv_boundary(ax, native_arrays["tv"], native_extent)
            draw_mask(ax, native_arrays["bv"], native_extent, COLORS["bv"], 0.52)
        if column == 2:
            draw_mask(
                ax, ndimage.binary_dilation(native_arrays["line"], iterations=1),
                native_extent, COLORS["length"], 0.95,
            )
            if abs(result.concavity_centroid_voxel[2] - native_k) <= 1:
                ax.scatter(
                    (centroid_native[0] + 0.5) * spacing_i,
                    (centroid_native[1] + 0.5) * spacing_j,
                    s=34, color=COLORS["centroid"], edgecolor="black", linewidth=0.4, zorder=5,
                )
            if abs(result.distal_endpoint_voxel[2] - native_k) <= 2:
                ax.scatter(
                    (endpoint_native[0] + 0.5) * spacing_i,
                    (endpoint_native[1] + 0.5) * spacing_j,
                    s=34, color=COLORS["endpoint"], edgecolor="black", linewidth=0.4, zorder=5,
                )
        if column == 0:
            draw_scale_bar(ax, native_extent)

    axes_3d[0].text2D(
        -0.07, 0.50, "3D view", transform=axes_3d[0].transAxes,
        rotation=90, ha="center", va="center", fontsize=9, fontweight="bold",
    )
    axes_long[0].text(
        -0.07, 0.50, "Longitudinal", transform=axes_long[0].transAxes,
        rotation=90, ha="center", va="center", fontsize=9, fontweight="bold",
    )
    axes_cross[0].text(
        -0.07, 0.50, "Cross Section", transform=axes_cross[0].transAxes,
        rotation=90, ha="center", va="center", fontsize=9, fontweight="bold",
    )
    for ax in (*axes_long, *axes_cross):
        ax.set_axis_off()
        ax.set_aspect("equal")

    legend = [
        Line2D([0], [0], color=COLORS["tv"], lw=2, label="TV boundary"),
        Patch(facecolor=COLORS["bv"], alpha=0.55, label="BV within TV"),
        Line2D([0], [0], color=COLORS["length"], lw=2, label="Digit length (counted)"),
        Patch(facecolor=COLORS["concavity"], alpha=0.65, label="Largest concavity"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["centroid"], markeredgecolor="black", label="Concavity centroid"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["endpoint"], markeredgecolor="black", label="Distal endpoint"),
    ]
    figure.legend(
        handles=legend, loc="lower center", bbox_to_anchor=(0.5, 0.016),
        ncol=6, frameon=False, fontsize=8,
    )
    figure.suptitle(case_id, y=0.995, fontsize=10, fontweight="normal")
    _save_pair(figure, output_base, dpi)


def save_all_figures(
    *,
    case_id: str,
    image_nii: nib.spatialimages.SpatialImage,
    image: np.ndarray,
    roi_mask: np.ndarray,
    bv_mask: np.ndarray,
    result: DigitLengthResult,
    threshold: float,
    figures_dir: Path,
    dpi: int,
) -> None:
    affine = np.asarray(image_nii.affine)
    save_publication_figure(
        case_id=case_id,
        image=image,
        roi_mask=roi_mask,
        bv_mask=bv_mask,
        affine=affine,
        result=result,
        threshold=threshold,
        output_base=figures_dir / f"{case_id}_publication",
        dpi=dpi,
    )
