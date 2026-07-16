#!/usr/bin/env python3
"""
bmp_stack_to_nifti.py
---------------------
Convert a folder of SkyScan/Bruker microCT reconstruction slices
(*.bmp or *.tif, one 2D image per Z-slice) into a single 3D NIfTI
volume (.nii.gz) that nnU-Net v2 can read for inference.

Why this is needed
------------------
Your trained model (Dataset501_Glioblastoma, 3d_fullres) was trained on
.nii.gz volumes at 0.004 mm (4 um) ISOTROPIC voxel spacing, single grayscale
channel. Your R2 / R4 reconstructions are stacks of 2D BMP slices at the same
4 um pixel size. nnU-Net needs them as ONE 3D file named  <CASE>_0000.nii.gz
(the _0000 is the channel index) with the correct spacing baked into the header.

Usage
-----
    python bmp_stack_to_nifti.py <slice_folder> <output_dir> --case R2

    # examples
    python bmp_stack_to_nifti.py  ".../R2/R2"  ".../nnunet_input"  --case R2
    python bmp_stack_to_nifti.py  ".../R4/R4"  ".../nnunet_input"  --case R4

This writes:  <output_dir>/<case>_0000.nii.gz

Options
-------
    --case      Case name (default: folder name). Output = <case>_0000.nii.gz
    --spacing   Voxel size in MILLIMETRES, isotropic (default 0.004 = 4 um).
                Read this from the *_rec.log line "Image Pixel Size (um)"
                and divide by 1000. R2 = 4.00006 um -> 0.00400006 mm ~ 0.004.
    --pattern   Glob for slice files (default "*rec*.bmp"). SkyScan names the
                cross-sections  <prefix>_rec00000201.bmp ... so this skips the
                _spr / preview / pp images automatically. Use "*.tif" etc if
                your slices are TIFFs.
    --dtype     Output pixel type: uint8 (default) or float32. uint8 matches the
                8-bit training data; leave it unless you know you need otherwise.

Requirements
------------
    pip install SimpleITK pillow numpy natsort
"""
import argparse
import os
import sys
import glob
import numpy as np

try:
    import SimpleITK as sitk
except ImportError:
    sys.exit("Missing dependency. Run:  pip install SimpleITK pillow numpy natsort")

from PIL import Image

try:
    from natsort import natsorted
except ImportError:
    # fall back to plain sort if natsort isn't installed (SkyScan zero-padded
    # names sort correctly either way because the index is fixed-width)
    natsorted = sorted


def find_slices(folder, pattern):
    files = glob.glob(os.path.join(folder, pattern))
    # exclude obvious non-slice SkyScan artefacts if a broad pattern was used
    files = [f for f in files
             if "_spr" not in os.path.basename(f).lower()
             and "_pp" not in os.path.basename(f).lower()]
    if not files:
        sys.exit(f"No slices matched pattern '{pattern}' in:\n  {folder}")
    return natsorted(files)


def main():
    ap = argparse.ArgumentParser(description="Stack 2D microCT slices into a 3D nnU-Net NIfTI.")
    ap.add_argument("slice_folder", help="Folder containing the *_rec*.bmp cross-section slices")
    ap.add_argument("output_dir", help="Where to write <case>_0000.nii.gz")
    ap.add_argument("--case", default=None, help="Case name (default: slice folder name)")
    ap.add_argument("--spacing", type=float, default=0.004,
                    help="Isotropic voxel size in MM (default 0.004 = 4 um)")
    ap.add_argument("--pattern", default="*rec*.bmp",
                    help="Glob for slice files (default '*rec*.bmp')")
    ap.add_argument("--dtype", choices=["uint8", "float32"], default="uint8")
    args = ap.parse_args()

    case = args.case or os.path.basename(os.path.normpath(args.slice_folder))
    os.makedirs(args.output_dir, exist_ok=True)

    files = find_slices(args.slice_folder, args.pattern)
    print(f"[info] case            : {case}")
    print(f"[info] slices found    : {len(files)}")
    print(f"[info] first / last    : {os.path.basename(files[0])}  ...  {os.path.basename(files[-1])}")

    # read first slice to get in-plane size
    first = np.array(Image.open(files[0]).convert("L"))
    H, W = first.shape
    Z = len(files)
    print(f"[info] volume (Z,Y,X)  : ({Z}, {H}, {W})")

    out_dtype = np.uint8 if args.dtype == "uint8" else np.float32
    vol = np.empty((Z, H, W), dtype=out_dtype)

    for i, f in enumerate(files):
        img = np.array(Image.open(f).convert("L"))  # force single 8-bit channel
        if img.shape != (H, W):
            sys.exit(f"Slice size mismatch at {os.path.basename(f)}: "
                     f"{img.shape} vs expected {(H, W)}")
        vol[i] = img.astype(out_dtype)
        if (i + 1) % 100 == 0 or i == Z - 1:
            print(f"  read {i+1}/{Z} slices", end="\r")
    print()

    # numpy array is (Z, Y, X); SimpleITK will treat axis0 as Z.
    image = sitk.GetImageFromArray(vol)
    # SimpleITK spacing order is (X, Y, Z) — isotropic here so all equal.
    s = float(args.spacing)
    image.SetSpacing((s, s, s))
    image.SetOrigin((0.0, 0.0, 0.0))
    image.SetDirection((1, 0, 0, 0, 1, 0, 0, 0, 1))

    out_path = os.path.join(args.output_dir, f"{case}_0000.nii.gz")
    sitk.WriteImage(image, out_path, useCompression=True)

    print(f"[done] wrote  {out_path}")
    print(f"[done] spacing (mm)    : {image.GetSpacing()}  ({s*1000:.3f} um isotropic)")
    print(f"[done] size  (X,Y,Z)   : {image.GetSize()}")
    print(f"[done] dtype           : {vol.dtype}")
    print()
    print("Next: run nnU-Net inference with this folder as the input (-i).")


if __name__ == "__main__":
    main()
