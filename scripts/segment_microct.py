#!/usr/bin/env python3
"""
segment_microct.py  —  end-to-end microCT segmentation with a trained nnU-Net v2 model
=======================================================================================
One command: a folder of reconstructed microCT slices (SkyScan *.bmp / *.tif stack)
-> a 3D segmentation mask (.nii.gz) + ROI volume + a preview overlay PNG.

This is the *validated* R2 workflow, generalized and GPU-ready. On a machine with a
CUDA GPU it auto-uses the GPU (minutes per volume); on CPU it still works (slow).

--------------------------------------------------------------------------------------
QUICK START (on a GPU machine)
--------------------------------------------------------------------------------------
1) Environment (once):
     # CUDA build of PyTorch — pick the command for your CUDA from pytorch.org, e.g.:
     pip install torch --index-url https://download.pytorch.org/whl/cu121
     pip install nnunetv2 SimpleITK pillow numpy natsort

2) Run one dataset:
     python segment_microct.py \
       --slices  "D:/path/to/R4/R4" \
       --model   "D:/path/to/Dataset501_Glioblastoma_nnUNetTrainer_nnUNetPlans_3d_fullres/Dataset501_Glioblastoma/nnUNetTrainer__nnUNetPlans__3d_fullres" \
       --case    R4 \
       --out     "D:/path/to/seg_out"

   The --model folder is the one that DIRECTLY contains plans.json, dataset.json,
   and fold_0 ... fold_4.

3) Outputs land in --out:
     R4_0000.nii.gz   the stacked input volume (4 um isotropic)
     R4.nii.gz        the segmentation mask (0 = background, 1 = ROI)
     R4_preview.png   mid-ROI slice with the mask overlaid in red
     R4_result.json   ROI voxel count + physical volume

--------------------------------------------------------------------------------------
COMMON OPTIONS
--------------------------------------------------------------------------------------
  --folds 0                 use a single fold (fast, default). Use "0 1 2 3 4" for the
                            full 5-fold ensemble (most accurate, ~5x slower), or "all".
  --tta                     enable test-time augmentation (mirroring). More accurate,
                            slower. Off by default.
  --step 0.5                sliding-window overlap. 0.5 = default/most accurate.
                            0.7 is ~2-3x faster with a small accuracy cost.
  --device auto             auto | cuda | cpu   (auto picks CUDA if available)
  --spacing 0.004           voxel size in MM (4 um). Read "Image Pixel Size (um)" from the
                            *_rec.log and divide by 1000 if your scan differs.
  --pattern "*rec*.bmp"     glob for slice files (use "*.tif" for TIFF stacks). Files with
                            _spr / _pp in the name (NRecon previews) are skipped.
  --checkpoint checkpoint_final.pth

--------------------------------------------------------------------------------------
Validated on R2 (SkyScan 1272, 459 x 1128 x 1128, 4.00 um, 8-bit) against
Dataset501_Glioblastoma (trained at 4 um isotropic, ZScore norm, Dice 0.968).
Because the scan resolution matches the training resolution, no resampling loss occurs.
"""
import argparse, glob, json, os, sys, time


def log(*a):
    print(*a, flush=True)


def find_slices(folder, pattern):
    files = [f for f in glob.glob(os.path.join(folder, pattern))
             if "_spr" not in os.path.basename(f).lower()
             and "_pp" not in os.path.basename(f).lower()]
    try:
        from natsort import natsorted
        files = natsorted(files)
    except ImportError:
        files.sort()
    if not files:
        sys.exit(f"No slices matched '{pattern}' in {folder}")
    return files


def stack_to_nifti(folder, pattern, spacing, out_path):
    import numpy as np, SimpleITK as sitk
    from PIL import Image
    files = find_slices(folder, pattern)
    Z = len(files)
    H, W = np.array(Image.open(files[0]).convert("L")).shape
    log(f"[convert] {Z} slices, {H}x{W} -> volume (Z,Y,X)=({Z},{H},{W})")
    vol = np.empty((Z, H, W), dtype=np.uint8)
    for i, f in enumerate(files):
        vol[i] = np.array(Image.open(f).convert("L"))
        if (i + 1) % 200 == 0:
            log(f"[convert] {i+1}/{Z}")
    img = sitk.GetImageFromArray(vol)
    img.SetSpacing((spacing, spacing, spacing))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sitk.WriteImage(img, out_path, useCompression=True)
    log(f"[convert] wrote {out_path}")


def parse_folds(s):
    s = s.strip()
    if s.lower() == "all":
        return "all"
    return tuple(int(x) for x in s.replace(",", " ").split())


def make_preview(gray, seg, out_png):
    import numpy as np
    from PIL import Image
    areas = (seg == 1).reshape(seg.shape[0], -1).sum(1)
    z = int(areas.argmax()) if areas.max() > 0 else seg.shape[0] // 2
    g = gray[z].astype(np.uint8)
    rgb = np.stack([g, g, g], axis=-1)
    m = seg[z] == 1
    rgb[m] = (rgb[m] * 0.35).astype(np.uint8)
    rgb[..., 0][m] = 255
    Image.fromarray(rgb).save(out_png)
    log(f"[preview] slice {z} (largest ROI) -> {out_png}")
    return z


def main():
    ap = argparse.ArgumentParser(description="microCT stack -> nnU-Net segmentation",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--slices", required=True, help="Folder of reconstructed slices")
    ap.add_argument("--model", required=True, help="nnU-Net model folder (contains plans.json, fold_0..)")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--case", default=None, help="Case name (default: slices folder name)")
    ap.add_argument("--folds", default="0", help='Folds: "0" | "0 1 2 3 4" | "all"')
    ap.add_argument("--tta", action="store_true", help="Enable test-time mirroring")
    ap.add_argument("--step", type=float, default=0.5, help="Sliding-window step (overlap)")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--spacing", type=float, default=0.004, help="Voxel size in mm")
    ap.add_argument("--pattern", default="*rec*.bmp", help="Slice filename glob")
    ap.add_argument("--checkpoint", default="checkpoint_final.pth")
    args = ap.parse_args()

    case = args.case or os.path.basename(os.path.normpath(args.slices))
    os.makedirs(args.out, exist_ok=True)
    in_nii = os.path.join(args.out, f"{case}_0000.nii.gz")
    out_nii = os.path.join(args.out, f"{case}.nii.gz")

    # nnU-Net expects these env vars to exist (not used for pure inference)
    for k in ("nnUNet_raw", "nnUNet_preprocessed", "nnUNet_results"):
        d = os.path.join(args.out, "_env", k); os.makedirs(d, exist_ok=True)
        os.environ.setdefault(k, d)

    import torch
    if args.device == "auto":
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        dev = args.device
    log(f"=== device: {dev} " + (f"({torch.cuda.get_device_name(0)})" if dev == "cuda" else "(CPU — slow)"))
    if dev == "cpu":
        torch.set_num_threads(os.cpu_count() or 8)

    # 1) convert
    stack_to_nifti(args.slices, args.pattern, args.spacing, in_nii)

    # 2) predict
    from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor
    from nnunetv2.imageio.simpleitk_reader_writer import SimpleITKIO
    predictor = nnUNetPredictor(
        tile_step_size=args.step, use_gaussian=True, use_mirroring=args.tta,
        device=torch.device(dev), verbose=False, allow_tqdm=True,
    )
    predictor.initialize_from_trained_model_folder(
        args.model, use_folds=parse_folds(args.folds), checkpoint_name=args.checkpoint)
    log(f"=== model loaded (folds={args.folds}, tta={args.tta}, step={args.step}); predicting ===")

    rw = SimpleITKIO()
    img, props = rw.read_images([in_nii])
    t0 = time.time()
    seg = predictor.predict_single_npy_array(img, props, None, None, False)
    dt = time.time() - t0
    rw.write_seg(seg, out_nii, props)
    log(f"=== prediction done in {dt:.0f}s -> {out_nii}")

    # 3) measure + preview
    import numpy as np
    n = int((seg == 1).sum())
    vox_mm3 = args.spacing ** 3
    z = make_preview(img[0], seg, os.path.join(args.out, f"{case}_preview.png"))
    result = {"case": case, "device": dev, "roi_voxels": n,
              "roi_mm3": round(n * vox_mm3, 6), "roi_um3": round(n * vox_mm3 * 1e9, 1),
              "seg_shape": list(seg.shape), "best_slice": z,
              "predict_seconds": round(dt, 1), "folds": args.folds, "tta": args.tta}
    json.dump(result, open(os.path.join(args.out, f"{case}_result.json"), "w"), indent=2)
    log(f"[result] {case}: ROI = {n:,} voxels = {n*vox_mm3:.4f} mm^3 "
        f"({n*vox_mm3*1e9:,.0f} um^3)")
    log("DONE")


if __name__ == "__main__":
    main()
