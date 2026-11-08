"""Conservative camera-shot boundaries and reviewable basketball-view labels.

This is a CPU image-change heuristic, not camera identity or a trained gameplay
classifier. A cut means a strong discontinuity was observed; labels remain
unverified. Uniform exposure flashes are ignored and pan/zoom alignment is used
only to veto likely continuous camera motion.
"""

from collections import defaultdict
from copy import deepcopy
import math

import cv2
import numpy as np


class ShotDetector:
    """Feed every decoded BGR frame, in strictly increasing source milliseconds.

    update() returns boundary evidence immediately on a strong hard cut. The
    current frame belongs to the new integer shot_id. finalize() returns
    half-open source ranges without modifying the detector, so it is idempotent.
    """

    def __init__(self, width=160, height=90, min_shot_ms=500):
        self.size = (width, height)
        self.min_shot_ms = min_shot_ms
        self.shot_id = 0
        self.last_timestamp = -1
        self._start = 0
        self._previous = None
        self._closed = []
        self._boundary = None
        self._summary = self._empty_summary()
        self._window = cv2.createHanningWindow(self.size, cv2.CV_32F)

    @staticmethod
    def _empty_summary():
        return dict(decoded_frames=0, flat_frames=0, exposure_frames=0, edge_sum=0.0)

    def _features(self, frame):
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise ValueError("Shot detection requires a nonempty BGR frame")
        small = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        mean, std = float(gray.mean()), float(gray.std())
        normalized = np.clip((gray.astype(np.float32) - mean) / max(std, 10), -2, 2) / 4
        hist = cv2.calcHist([hsv], [0, 1], None, [24, 8], [0, 180, 0, 256])
        hist = hist / max(1.0, float(hist.sum()))
        mask = (hsv[:, :, 1] >= 32).astype(np.uint8) * 255
        hue = cv2.calcHist([hsv], [0], mask, [24], [0, 180])
        hue = hue / max(1.0, float(hue.sum()))
        return dict(
            gray=gray,
            normalized=normalized,
            hist=hist,
            hue=hue,
            chroma=float(np.mean(hsv[:, :, 1])) / 255,
            mean=mean,
            std=std,
            exposure=std < 3 and (mean > 250 or mean < 3),
            edge_density=float(np.mean(cv2.Canny(gray, 60, 140) > 0)),
        )

    @staticmethod
    def _aligned_error(previous, current, matrix):
        height, width = previous.shape
        shifted = cv2.warpAffine(
            previous, matrix, (width, height), flags=cv2.INTER_LINEAR
        )
        mask = (
            cv2.warpAffine(
                np.ones_like(previous), matrix, (width, height), flags=cv2.INTER_NEAREST
            )
            > 0.5
        )
        overlap = float(mask.mean())
        if overlap < 0.55:
            return 1.0
        return float(np.mean(np.abs(shifted[mask] - current[mask])))

    def _continuous_motion(self, before, after):
        a, b = before["normalized"], after["normalized"]
        if before["std"] < 5 or after["std"] < 5:
            return False
        shift, response = cv2.phaseCorrelate(a, b, self._window)
        if response >= 0.2:
            matrix = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
            if self._aligned_error(a, b, matrix) < 0.10:
                return True
        points = cv2.goodFeaturesToTrack(
            before["gray"], maxCorners=80, qualityLevel=0.02, minDistance=4
        )
        if points is None or len(points) < 12:
            return False
        tracked, status, _ = cv2.calcOpticalFlowPyrLK(
            before["gray"], after["gray"], points, None
        )
        if tracked is None or status is None:
            return False
        valid = status.reshape(-1) > 0
        if int(valid.sum()) < 12:
            return False
        matrix, inliers = cv2.estimateAffinePartial2D(
            points[valid], tracked[valid], method=cv2.RANSAC, ransacReprojThreshold=1.5
        )
        if matrix is None or inliers is None or float(inliers.mean()) < 0.65:
            return False
        scale = math.hypot(matrix[0, 0], matrix[1, 0])
        angle = abs(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
        return (
            0.9 <= scale <= 1.1
            and angle <= 4
            and self._aligned_error(a, b, matrix) < 0.10
        )

    def _range(self, end_ms):
        summary = self._summary
        frames = summary["decoded_frames"]
        return dict(
            shot_id=self.shot_id,
            start_ms=self._start,
            end_ms=end_ms,
            boundary_evidence=deepcopy(self._boundary),
            visual_summary=dict(
                decoded_frames=frames,
                flat_fraction=summary["flat_frames"] / frames if frames else 0,
                exposure_fraction=summary["exposure_frames"] / frames if frames else 0,
                mean_edge_density=summary["edge_sum"] / frames if frames else 0,
            ),
        )

    def update(self, frame, timestamp_ms):
        if (
            isinstance(timestamp_ms, bool)
            or not isinstance(timestamp_ms, (int, np.integer))
            or timestamp_ms < 0
        ):
            raise ValueError(
                "Source timestamp must be a nonnegative integer millisecond"
            )
        if timestamp_ms <= self.last_timestamp:
            raise ValueError("Shot frame timestamps must strictly increase")
        current = self._features(frame)
        event = None
        previous = self._previous
        if (
            previous is not None
            and not current["exposure"]
            and timestamp_ms - self._start >= self.min_shot_ms
        ):
            colour = float(
                cv2.compareHist(
                    previous["hist"], current["hist"], cv2.HISTCMP_BHATTACHARYYA
                )
            )
            hue = float(
                cv2.compareHist(
                    previous["hue"], current["hue"], cv2.HISTCMP_BHATTACHARYYA
                )
            )
            structure = float(
                np.mean(np.abs(previous["normalized"] - current["normalized"]))
            )
            flat_colour_cut = (
                previous["std"] < 5
                and current["std"] < 5
                and hue > 0.8
                and min(previous["chroma"], current["chroma"]) > 0.2
            )
            candidate = (
                (colour > 0.5 and structure > 0.13)
                or structure > 0.23
                or flat_colour_cut
            )
            if candidate and not self._continuous_motion(previous, current):
                event = dict(
                    timestamp_ms=int(timestamp_ms),
                    method="hard-cut-heuristic-v1",
                    colour_distance=round(colour, 4),
                    structure_difference=round(structure, 4),
                    verified=False,
                    requires_review=True,
                )
                self._closed.append(self._range(int(timestamp_ms)))
                self.shot_id += 1
                self._start = int(timestamp_ms)
                self._boundary = event
                self._summary = self._empty_summary()
        self._summary["decoded_frames"] += 1
        self._summary["flat_frames"] += current["std"] < 5
        self._summary["exposure_frames"] += current["exposure"]
        self._summary["edge_sum"] += current["edge_density"]
        # A clipped white/black exposure loses structure; retain the informative
        # anchor so a flash back to the same shot does not create two false cuts.
        if not current["exposure"] or self._previous is None:
            self._previous = current
        self.last_timestamp = int(timestamp_ms)
        return event

    def finalize(self, duration_ms):
        if duration_ms < 0 or (
            self.last_timestamp >= 0 and duration_ms <= self.last_timestamp
        ):
            raise ValueError("Source duration must follow the last observed frame")
        if self.last_timestamp < 0 or duration_ms == 0:
            return []
        return deepcopy(self._closed + [self._range(int(duration_ms))])


def classify_shots(shots, samples, visual_summaries=None):
    """Return unverified shot categories, never athlete or outcome claims.

    Missing person detections alone cannot establish non-gameplay. A wide view
    with several small people supports gameplay; a sustained dominant close-up
    supports non-gameplay. Both remain reviewer-correctable candidates.
    """
    result = deepcopy(shots)
    for shot in result:
        grouped = defaultdict(list)
        for sample in samples:
            if shot["start_ms"] <= sample["timestamp_ms"] < shot["end_ms"]:
                if "shot_id" not in sample or str(sample["shot_id"]) == str(
                    shot["shot_id"]
                ):
                    grouped[sample["timestamp_ms"]].append(sample)
        wide, close = 0, 0
        for frame in grouped.values():
            boxes = [s["bbox"] for s in frame]
            heights = [b[3] for b in boxes]
            centres = [b[0] + b[2] / 2 for b in boxes]
            wide += (
                len(boxes) >= 3
                and float(np.median(heights)) < 0.6
                and max(centres) - min(centres) > 0.25
            )
            close += len(boxes) <= 2 and any(
                b[3] >= 0.65 and b[2] >= 0.25 for b in boxes
            )
        observed = len(grouped)
        summary = (visual_summaries or {}).get(
            shot["shot_id"], shot.get("visual_summary", {})
        )
        duration = shot["end_ms"] - shot["start_ms"]
        category, reasons = (
            "uncertain",
            ["Insufficient evidence to classify this camera shot"],
        )
        if observed >= 3 and duration >= 1000 and wide / observed >= 0.6:
            category, reasons = (
                "gameplay_candidate",
                ["Several small people are spread across a wide view"],
            )
        elif observed >= 3 and duration >= 700 and close / observed >= 0.8:
            category, reasons = (
                "non_gameplay_candidate",
                ["Sustained dominant close-up; full play is not visible"],
            )
        elif (
            not observed
            and duration >= 800
            and summary.get("decoded_frames", 0) >= 6
            and summary.get("flat_fraction", 0) >= 0.95
            and summary.get("mean_edge_density", 1) < 0.01
        ):
            category, reasons = (
                "non_gameplay_candidate",
                [
                    "Nearly uniform frames without detected people; possible slate or transition"
                ],
            )
        shot.update(
            category=category,
            category_reasons=reasons,
            category_verified=False,
            requires_review=True,
            classification_method="basketball-view-heuristic-v1",
            category_evidence=dict(
                observed_frames=observed,
                wide_frames=int(wide),
                closeup_frames=int(close),
            ),
        )
    return result
