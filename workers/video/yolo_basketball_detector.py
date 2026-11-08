"""Native local YOLOE-26 Nano ball/hoop observations, with pinned class semantics.

The official prompt-free model uses RAM++ vocabulary, not basketball-specific
fine-tuning. Only its exact basketball and basketball-hoop classes are exposed;
COCO sports-ball and backboard are never silently relabeled as a hoop.
"""

import hashlib
import json
import os
from pathlib import Path
import time

import numpy as np
import torch

from scripts.download_yolo_basketball_model import (
    MODEL_NAME,
    MODEL_SHA256,
    MODEL_BYTES,
    MANIFEST_NAME,
)
from workers.video.runtime import select_device, synchronize
from workers.video.shot_candidates import suppress

SOURCE_CLASSES = {310: ("basketball", 0), 315: ("hoop", 1)}
EXPECTED_NAMES = {310: "basketball", 315: "basketball hoop"}


def verified_class_map(names):
    if isinstance(names, list):
        names = dict(enumerate(names))
    if not isinstance(names, dict):
        raise ValueError("YOLOE checkpoint has no verifiable class vocabulary")
    try:
        names = {int(k): v for k, v in names.items()}
    except (ValueError, TypeError) as exc:
        raise ValueError("YOLOE checkpoint has invalid source class IDs") from exc
    if len(names) != 4585 or any(names.get(k) != v for k, v in EXPECTED_NAMES.items()):
        raise ValueError("YOLOE basketball/hoop class vocabulary mismatch")
    return dict(SOURCE_CLASSES)


class YoloBasketballDetector:
    """Match BasketballDetector's constructor and detect(frame, source_ms) contract."""

    def __init__(
        self,
        model_folder,
        device="auto",
        ball_confidence=0.25,
        hoop_confidence=0.35,
        tiled=False,
    ):
        if not (0 < ball_confidence <= 1 and 0 < hoop_confidence <= 1):
            raise ValueError("Confidence thresholds must be in (0,1]")
        self.device = select_device(device)
        if (
            self.device == "mps"
            and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"
        ):
            raise ValueError(
                "Disable PYTORCH_ENABLE_MPS_FALLBACK for native MPS inference"
            )
        folder = Path(model_folder)
        self.weights = folder / MODEL_NAME
        if not self.weights.is_file():
            raise FileNotFoundError(
                "Run scripts/download_yolo_basketball_model.py first"
            )
        if (
            self.weights.stat().st_size != MODEL_BYTES
            or hashlib.sha256(self.weights.read_bytes()).hexdigest() != MODEL_SHA256
        ):
            raise ValueError("YOLOE-26 basketball state hash mismatch")
        self.manifest = json.loads((folder / MANIFEST_NAME).read_text())
        if (
            self.manifest.get("sha256") != MODEL_SHA256
            or self.manifest.get("model") != "ultralytics/yoloe-26n-seg-pf"
        ):
            raise ValueError("YOLOE-26 basketball provenance mismatch")
        os.environ["YOLO_OFFLINE"] = "true"
        os.environ.setdefault("YOLO_CONFIG_DIR", str(folder / "ultralytics-config"))
        from ultralytics import YOLOE, settings

        settings.update({"sync": False})
        torch.set_num_threads(4)
        self.model = YOLOE(str(self.weights))
        self.class_map = verified_class_map(self.model.names)
        self.model.to(self.device)
        if next(self.model.model.parameters()).device.type != self.device.split(":")[0]:
            raise RuntimeError("Basketball model did not move to the requested device")
        architecture = type(self.model.model).__name__
        head = type(self.model.model.model[-1]).__name__
        if architecture != "YOLOESegModel" or head != "YOLOESegment26":
            raise ValueError("YOLOE-26 Nano architecture mismatch")
        self.image_size = 1280
        self.thresholds = {
            "basketball": float(ball_confidence),
            "hoop": float(hoop_confidence),
        }
        self.tiled = bool(tiled)
        self.last_inference_seconds = 0.0
        self.last_transfer_seconds = 0.0
        self.manifest.update(
            input_size=self.image_size,
            nms=False,
            quantization=None,
            view_strategy="full_plus_two_tiles" if self.tiled else "full_frame",
            source_classes={str(k): EXPECTED_NAMES[k] for k in self.class_map},
            actual_architecture=architecture,
            actual_head=head,
        )

    def detect(self, frame, timestamp_ms):
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ValueError("Basketball detection requires a nonempty BGR frame")
        h, w = frame.shape[:2]
        views = [(0, 0, w, h)]
        if self.tiled and w / h > 1.2:
            crop = min(w, round(w * 0.6))
            views.extend([(0, 0, crop, h), (w - crop, 0, w, h)])
        # Ultralytics ndarray input is BGR; it performs its own RGB conversion.
        images = [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in views]
        synchronize(self.device)
        started = time.perf_counter()
        results = self.model.predict(
            source=images,
            device=self.device,
            imgsz=self.image_size,
            conf=min(self.thresholds.values()),
            classes=list(self.class_map),
            max_det=30,
            nms=False,
            quantize=None,
            rect=True,
            verbose=False,
            save=False,
        )
        synchronize(self.device)
        self.last_inference_seconds = time.perf_counter() - started
        if not isinstance(results, list):
            results = [results]
        if len(results) != len(views):
            raise ValueError("Basketball results do not match source views")
        started = time.perf_counter()
        observations = []
        for result, (left, top, _, _) in zip(results, views):
            if result.boxes.data.device.type != self.device.split(":")[0]:
                raise RuntimeError(
                    "Basketball results left the requested device before transfer"
                )
            data = result.boxes.data.detach().cpu().numpy()
            for row in data:
                x1, y1, x2, y2, confidence, source_id = map(float, row[:6])
                source_id = int(source_id)
                if source_id not in self.class_map:
                    continue
                label, class_id = self.class_map[source_id]
                if (
                    not np.isfinite([x1, y1, x2, y2, confidence]).all()
                    or confidence < self.thresholds[label]
                ):
                    continue
                x1 = max(0, min(w, left + x1))
                x2 = max(0, min(w, left + x2))
                y1 = max(0, min(h, top + y1))
                y2 = max(0, min(h, top + y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                observations.append(
                    dict(
                        class_name=label,
                        class_id=class_id,
                        source_class_id=source_id,
                        confidence=confidence,
                        bbox=[x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h],
                        timestamp_ms=timestamp_ms,
                        origin="yoloe_26n_prompt_free",
                        verified=False,
                    )
                )
        self.last_transfer_seconds = time.perf_counter() - started
        return suppress(observations)
