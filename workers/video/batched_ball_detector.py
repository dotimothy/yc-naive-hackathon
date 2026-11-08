"""EXPERIMENTAL cross-frame batching for the RF-DETR ball/hoop detector.

Runs the *same* three production views (full frame + two fixed 60% crops) for
several sampled source frames in one ``predict()`` call. Thresholds, per-frame
same-class suppression, checkpoint and pixels are unchanged. Each frame's
observations are mapped back with that frame's own size and source timestamp
and returned separately; nothing is shared, interpolated or carried across
frames. Not used by production analysis.

Trade-off: a frame's result is only available after the last frame of its batch
has been decoded, which adds up to ``(frames_per_batch - 1) * stride / fps``
seconds of source-time latency in live/replay-paced use.
"""

import time

import cv2

from workers.video.shot_candidates import suppress

TILE_FRACTION = 0.6  # production crop width


def production_views(w, h):
    """Exactly the views BasketballDetector(tiled=True).detect uses."""
    views = [(0, 0, w, h)]
    if w / h > 1.2:
        crop = min(w, round(w * TILE_FRACTION))
        views.extend([(0, 0, crop, h), (w - crop, 0, w, h)])
    return views


def map_result(result, view, size, thresholds, classes, timestamp_ms, view_name):
    """One predict() result for one crop -> production-format observations."""
    left, top = view[0], view[1]
    w, h = size
    observations = []
    for box, class_id, confidence in zip(
        result.xyxy, result.class_id, result.confidence
    ):
        label = classes.get(int(class_id))
        confidence = float(confidence)
        if label is None or confidence < thresholds[label]:
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
                view=view_name,
            )
        )
    return observations


class BatchedBallDetector:
    """``detect_batch([(frame, timestamp_ms), ...])`` -> one kept list per frame."""

    experimental = True
    VIEW_NAMES = ("full", "left", "right")

    def __init__(self, detector, frames_per_batch=2):
        if not 1 <= frames_per_batch <= 16:
            raise ValueError("frames_per_batch must be in 1..16")
        self.detector = detector
        self.frames_per_batch = frames_per_batch
        self.last = {}

    def detect_batch(self, items):
        """Returns ([kept per frame], [raw per frame]) in input order.

        ``self.last`` records synchronized predict() seconds and host-side
        preparation/postprocessing seconds for the call.
        """
        from workers.video.basketball_detector import CLASSES
        from workers.video.runtime import synchronize

        if not 1 <= len(items) <= self.frames_per_batch:
            raise ValueError("Batch must hold 1..frames_per_batch frames")
        started = time.perf_counter()
        images, layout = [], []
        for index, (frame, timestamp_ms) in enumerate(items):
            h, w = frame.shape[:2]
            for view_index, (x1, y1, x2, y2) in enumerate(production_views(w, h)):
                images.append(cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2RGB))
                layout.append(
                    (index, (x1, y1, x2, y2), (w, h), timestamp_ms, view_index)
                )
        prepared = time.perf_counter()
        synchronize(self.detector.device)
        predict_started = time.perf_counter()
        results = self.detector.model.predict(
            images, threshold=min(self.detector.thresholds.values())
        )
        synchronize(self.detector.device)
        predict_seconds = time.perf_counter() - predict_started
        if not isinstance(results, list):
            results = [results]
        if len(results) != len(images):
            raise RuntimeError(
                "predict() returned %d results for %d images"
                % (len(results), len(images))
            )
        raw = [[] for _ in items]
        for result, (index, view, size, timestamp_ms, view_index) in zip(
            results, layout
        ):
            raw[index].extend(
                map_result(
                    result,
                    view,
                    size,
                    self.detector.thresholds,
                    CLASSES,
                    timestamp_ms,
                    self.VIEW_NAMES[view_index],
                )
            )
        # Suppression stays per source frame, exactly as in production.
        kept = [suppress(frame_raw) for frame_raw in raw]
        self.last = dict(
            frames=len(items),
            images=len(images),
            prepare_seconds=prepared - started,
            predict_seconds=predict_seconds,
            postprocess_seconds=time.perf_counter() - predict_started - predict_seconds,
        )
        return kept, raw
