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


POSE_MODEL = "ultralytics/yolo26n-pose"
POSE_SHA256 = "eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9"
POSE_IMAGE_SIZE = 1280
KEYPOINT_THRESHOLD = 0.5


class YoloPoseModel:
    """YOLO26 end-to-end pose; learned model and tensor postprocessing on the selected device."""

    def __init__(self, device):
        if device == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
            raise ValueError(
                "Disable PYTORCH_ENABLE_MPS_FALLBACK for native MPS pose inference"
            )
        import hashlib
        import json
        from pathlib import Path

        folder = Path(__file__).resolve().parents[2] / "data/models"
        checkpoint = folder / "yolo26n-pose.pt"
        if not checkpoint.is_file():
            raise FileNotFoundError(
                "Run .venv/bin/python scripts/download_pose_model.py first"
            )
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != POSE_SHA256:
            raise ValueError("YOLO26 pose checkpoint checksum mismatch")
        self.manifest = json.loads((folder / "yolo26n-pose-manifest.json").read_text())
        if self.manifest.get("sha256") != POSE_SHA256:
            raise ValueError("YOLO26 pose manifest mismatch")
        # Keep prediction offline; settings live with ignored model data, not account-wide config.
        os.environ["YOLO_OFFLINE"] = "true"
        os.environ.setdefault("YOLO_CONFIG_DIR", str(folder / "ultralytics-config"))
        from ultralytics import YOLO, settings

        settings.update({"sync": False})
        self.device = device
        self.confidence = 0.25
        self.image_size = POSE_IMAGE_SIZE
        self.model = YOLO(str(checkpoint), task="pose")
        self.model.to(device)
        if next(self.model.model.parameters()).device.type != device.split(":")[0]:
            raise RuntimeError("Pose model did not move to the requested device")

    def predict(self, frame):
        # Legacy tools may supply RGB CHW tensors; the shared decoder supplies BGR frames directly.
        if isinstance(frame, torch.Tensor):
            frame = (
                (frame.detach().cpu().permute(1, 2, 0).numpy()[:, :, ::-1] * 255)
                .clip(0, 255)
                .astype("uint8")
            )
        result = self.model.predict(
            source=frame,
            device=self.device,
            imgsz=self.image_size,
            conf=self.confidence,
            max_det=30,
            nms=False,
            quantize=None,
            rect=True,
            verbose=False,
            save=False,
        )[0]
        if result.boxes.data.device.type != self.device.split(":")[0]:
            raise RuntimeError("Pose results left the requested device before transfer")
        data = result.keypoints.data
        visibility = (data[:, :, 2] >= KEYPOINT_THRESHOLD).to(data.dtype).unsqueeze(-1)
        return dict(
            boxes=result.boxes.xyxy,
            scores=result.boxes.conf,
            keypoints=torch.cat((data[:, :, :2], visibility), dim=-1),
            keypoint_scores=data[:, :, 2],
        )


@lru_cache(maxsize=1)
def load_model(device):
    torch.set_num_threads(4)
    model = YoloPoseModel(device)
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
        prediction = (
            model.predict(tensor)
            if isinstance(model, YoloPoseModel)
            else model([tensor])[0]
        )
    synchronize(device)
    seconds = time.perf_counter() - started
    transfer_started = time.perf_counter()
    result = {key: value.cpu() for key, value in prediction.items()}
    return result, seconds, time.perf_counter() - transfer_started
