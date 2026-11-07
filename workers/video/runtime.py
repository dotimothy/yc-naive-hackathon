"""GPU-aware model lifecycle, explicit device policy, and synchronized timing."""

import os
import threading
import time
from functools import lru_cache
import torch

# A single inference worker owns this cache. No model state is shared across processes.
MODEL_LOCK = threading.RLock()


def available_devices():
    devices = ["cpu"]
    if torch.backends.mps.is_available():
        devices.append("mps")
    if torch.cuda.is_available():
        devices.extend("cuda:%d" % i for i in range(torch.cuda.device_count()))
    return devices


def select_device(requested=None):
    requested = requested or os.environ.get("SCOUTFILM_DEVICE", "auto")
    if requested == "auto":
        return (
            "cuda:0"
            if torch.cuda.is_available()
            else "mps"
            if torch.backends.mps.is_available()
            else "cpu"
        )
    if requested == "cuda":
        requested = "cuda:0"
    if requested not in available_devices():
        raise ValueError(
            "Requested device %s is unavailable; available: %s"
            % (requested, ", ".join(available_devices()))
        )
    return requested


def synchronize(device):
    if device.startswith("cuda"):
        torch.cuda.synchronize(torch.device(device))
    elif device == "mps":
        torch.mps.synchronize()


class MPSStage(torch.nn.Module):
    """Offload a convolution stage, keeping dynamic ROI/NMS work on CPU."""

    def __init__(self, module):
        super().__init__()
        self.module = module.to("mps")

    def forward(self, tensor):
        result = self.module(tensor.to("mps"))
        if isinstance(result, dict):
            return {k: v.cpu() for k, v in result.items()}
        return result.cpu()


@lru_cache(maxsize=1)
def load_model(device):
    from torchvision.models.detection import (
        keypointrcnn_resnet50_fpn,
        KeypointRCNN_ResNet50_FPN_Weights,
    )

    torch.set_num_threads(4)
    model = (
        keypointrcnn_resnet50_fpn(
            weights=KeypointRCNN_ResNet50_FPN_Weights.COCO_V1,
            min_size=480,
            max_size=854,
            box_detections_per_img=30,
        )
        .eval()
        .to("cpu" if device == "mps" else device)
    )
    if device == "mps":
        model.backbone = MPSStage(model.backbone)
        # Keep ROIAlign and keypoint coordinate decoding on CPU; offload learned convolutions.
        model.roi_heads.keypoint_head = MPSStage(
            torch.nn.Sequential(
                model.roi_heads.keypoint_head, model.roi_heads.keypoint_predictor
            )
        )
        model.roi_heads.keypoint_predictor = torch.nn.Identity()
    synchronize(device)
    return model


def get_model(device):
    with MODEL_LOCK:
        before = load_model.cache_info().hits
        started = time.perf_counter()
        model = load_model(device)
        return (
            model,
            time.perf_counter() - started,
            load_model.cache_info().hits > before,
        )


def infer(model, tensor, device):
    # Flush prior transfers before timing and wait for GPU completion afterward.
    synchronize(device)
    started = time.perf_counter()
    with torch.inference_mode():
        prediction = model([tensor])[0]
    synchronize(device)
    seconds = time.perf_counter() - started
    transfer_started = time.perf_counter()
    result = {key: value.cpu() for key, value in prediction.items()}
    return result, seconds, time.perf_counter() - transfer_started
