"""nnU-Net predictor construction and execution."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import torch

from .constants import MODEL_CHECKPOINT
from .model import validate_model_folder


def resolve_device() -> torch.device:
    if torch.cuda.is_available():
        index = torch.cuda.current_device()
        print(
            f"Inference device: cuda ({torch.cuda.get_device_name(index)}; "
            f"PyTorch {torch.__version__}; CUDA {torch.version.cuda})",
            flush=True,
        )
        return torch.device("cuda")
    print("Inference device: cpu (CUDA is not available).", flush=True)
    return torch.device("cpu")


def nnunet_checkpoint_safe_globals() -> list[object]:
    """NumPy objects present in this nnU-Net release's trusted checkpoints."""
    if hasattr(np, "_core"):
        # The checkpoint was created with NumPy 1.x, which serialized this as
        # numpy.core.multiarray.scalar. NumPy 2.x moved the object to
        # numpy._core, so provide the original serialized name explicitly.
        scalar: object = (
            np._core.multiarray.scalar,
            "numpy.core.multiarray.scalar",
        )
    else:
        scalar = np.core.multiarray.scalar
    return [
        scalar,
        np.dtype,
        np.dtypes.Float32DType,
        np.dtypes.Float64DType,
    ]


def run_prediction(model_folder: Path, input_dir: Path, output_dir: Path) -> list[int]:
    runtime_root = output_dir.parent / "_nnunet_runtime"
    temporary_environment: list[str] = []
    for variable, folder_name in (
        ("nnUNet_raw", "raw"),
        ("nnUNet_preprocessed", "preprocessed"),
        ("nnUNet_results", "results"),
    ):
        if not os.environ.get(variable):
            target = runtime_root / folder_name
            target.mkdir(parents=True, exist_ok=True)
            os.environ[variable] = str(target)
            temporary_environment.append(variable)

    try:
        from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor

        folds = validate_model_folder(model_folder)
        device = resolve_device()
        predictor = nnUNetPredictor(
            tile_step_size=0.5,
            use_gaussian=True,
            use_mirroring=True,
            perform_everything_on_device=device.type == "cuda",
            device=device,
            verbose=False,
            verbose_preprocessing=False,
            allow_tqdm=True,
        )
        # PyTorch 2.6 changed torch.load's default to weights_only=True. nnU-Net
        # 2.5.1 checkpoints contain NumPy scalar metadata, so explicitly allow
        # the required NumPy types while retaining safe weights-only loading.
        with torch.serialization.safe_globals(nnunet_checkpoint_safe_globals()):
            predictor.initialize_from_trained_model_folder(
                str(model_folder),
                use_folds=tuple(folds),
                checkpoint_name=MODEL_CHECKPOINT,
            )
        output_dir.mkdir(parents=True, exist_ok=False)
        predictor.predict_from_files(
            str(input_dir),
            str(output_dir),
            save_probabilities=False,
            overwrite=False,
            num_processes_preprocessing=1,
            num_processes_segmentation_export=1,
        )
        return folds
    finally:
        for variable in temporary_environment:
            os.environ.pop(variable, None)
        if temporary_environment and runtime_root.exists():
            shutil.rmtree(runtime_root)

