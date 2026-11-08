"""Optional, pinned RF-DETR basketball detector. No hosted inference or API keys."""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
import cv2
import torch
from workers.video.runtime import select_device, synchronize
from workers.video.shot_candidates import suppress

CLASSES = {0: "basketball", 1: "hoop", 2: "player", 3: "referee"}
REVISION = "aae99b56aadd2f1c40f1a402fd465dd666fbc8da"
MODEL_SHA = "759968ecf8f83663c85de3d881713e072f4a9dd0ea2f136b082878b1e4fe2400"


class BasketballDetector:
    def __init__(
        self,
        model_folder,
        device="auto",
        ball_confidence=0.25,
        hoop_confidence=0.35,
        tiled=True,
        precision=None,
    ):
        if not (0 < ball_confidence <= 1 and 0 < hoop_confidence <= 1):
            raise ValueError("Confidence thresholds must be in (0,1]")
        folder = Path(model_folder)
        self.weights = folder / "ebard-rfdetr-nano-state.pth"
        self.manifest = json.loads(
            (folder / "ebard-rfdetr-nano-manifest.json").read_text()
        )
        if (
            self.manifest["sha256"] != MODEL_SHA
            or self.manifest["revision"] != REVISION
        ):
            raise ValueError("Unexpected model provenance")
        if (
            hashlib.sha256(self.weights.read_bytes()).hexdigest()
            != self.manifest["sanitized_sha256"]
        ):
            raise ValueError("Detector state hash mismatch")
        from rfdetr import RFDETRNano

        self.device = select_device(device)
        if self.device.startswith("cuda"):
            torch.cuda.set_device(int(self.device.split(":")[1]))
        torch.set_num_threads(4)
        self.model = RFDETRNano(
            pretrain_weights=str(self.weights),
            device="cuda" if self.device.startswith("cuda") else self.device,
            resolution=704,
        )
        requested = precision or os.environ.get("SCOUTFILM_BALL_PRECISION", "auto")
        if requested not in ("auto", "float16", "float32"):
            raise ValueError("Ball precision must be auto, float16 or float32")
        self.inference_runtime = dict(
            requested_precision=requested,
            precision="float32",
            optimized=False,
            compiled=False,
            optimization_seconds=0.0,
            fallback_reason=None,
        )
        # Validated on MPS. CPU and unbenchmarked CUDA retain their existing default.
        if requested == "float16" or (requested == "auto" and self.device == "mps"):
            before = time.perf_counter()
            try:
                # RF-DETR 1.3's traced detection export includes a None mask.
                # Eager export supports both one-view and three-view inputs.
                self.model.optimize_for_inference(compile=False, dtype=torch.float16)
                self.inference_runtime.update(precision="float16", optimized=True)
            except Exception as exc:
                self._fallback_to_float32(exc)
            self.inference_runtime["optimization_seconds"] = (
                time.perf_counter() - before
            )
        self.thresholds = {
            "basketball": ball_confidence,
            "hoop": hoop_confidence,
            "player": 0.5,
            "referee": 0.5,
        }
        self.tiled = tiled

    def _fallback_to_float32(self, exc):
        self.model.remove_optimized_model()
        self.inference_runtime.update(
            precision="float32", optimized=False, fallback_reason=type(exc).__name__
        )
        logging.warning("RF-DETR float16 failed; using float32: %s", exc)

    def detect(self, frame, timestamp_ms):
        h, w = frame.shape[:2]
        views = [(0, 0, w, h)]
        if self.tiled and w / h > 1.2:
            crop = min(w, round(w * 0.6))
            views.extend([(0, 0, crop, h), (w - crop, 0, w, h)])
        images = [
            cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
            for x1, y1, x2, y2 in views
        ]
        synchronize(self.device)
        try:
            results = self.model.predict(
                images, threshold=min(self.thresholds.values())
            )
        except Exception as exc:
            if not getattr(self, "inference_runtime", {}).get("optimized"):
                raise
            self._fallback_to_float32(exc)
            results = self.model.predict(
                images, threshold=min(self.thresholds.values())
            )
        synchronize(self.device)
        if not isinstance(results, list):
            results = [results]
        observations = []
        for result, (left, top, right, bottom) in zip(results, views):
            for box, class_id, confidence in zip(
                result.xyxy, result.class_id, result.confidence
            ):
                label = CLASSES.get(int(class_id))
                confidence = float(confidence)
                if label is None or confidence < self.thresholds[label]:
                    continue
                x1, y1, x2, y2 = map(float, box)
                x1 = max(0, min(w, left + x1))
                x2 = max(0, min(w, left + x2))
                y1 = max(0, min(h, top + y1))
                y2 = max(0, min(h, top + y2))
                if x2 <= x1 or y2 <= y1:
                    continue
                observations.append(
                    dict(
                        class_name=label,
                        class_id=int(class_id),
                        confidence=confidence,
                        bbox=[x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h],
                        timestamp_ms=timestamp_ms,
                        origin="ebard_rfdetr",
                    )
                )
        return suppress(observations)
