"""EXPERIMENTAL adaptive-crop ball/hoop views. Not used by production analysis.

The production detector (``basketball_detector.py``) runs the full frame plus two
fixed 60%-wide crops on every sampled frame. This module chooses fewer views from
the strategy's *own* earlier model observations:

* a crop the size of a production tile, centred on the stationary hoop and the
  last observed ball position (merged into one crop when both fit);
* the full fixed-view set on a periodic refresh, while bootstrapping, without a
  hoop consensus, and when no ball has been observed recently (conservative
  rediscovery/fallback).

Crops never create positions: a ball outside every view is simply not observed.
Observations keep the production format plus the ``view`` that produced them.
"""

import time

from workers.video.shot_candidates import center, stationary_hoops, suppress

TILE_FRACTION = 0.6  # production crop width
BALL_MAX_AGE_MS = 200  # production ball-segment gap


def full_view(w, h):
    return [("full", (0, 0, w, h))]


def fixed_tiles(w, h):
    crop = min(w, round(w * TILE_FRACTION))
    return [("left", (0, 0, crop, h)), ("right", (w - crop, 0, w, h))]


def production_views(w, h):
    """Same views BasketballDetector(tiled=True) uses."""
    return full_view(w, h) + (fixed_tiles(w, h) if w / h > 1.2 else [])


class AdaptiveCropPolicy:
    def __init__(
        self,
        crop_fraction=TILE_FRACTION,
        bootstrap_frames=3,
        refresh_every=10,
        reacquire_every=1,
        keep_full=True,
        ball_max_age_ms=BALL_MAX_AGE_MS,
        hoop_window=60,
    ):
        if not 0.2 <= crop_fraction <= 1:
            raise ValueError("crop_fraction must be in [0.2, 1]")
        if min(bootstrap_frames, refresh_every, reacquire_every, hoop_window) < 1:
            raise ValueError("Frame counts must be positive")
        self.crop_fraction = crop_fraction
        self.bootstrap_frames = bootstrap_frames
        self.refresh_every = refresh_every
        self.reacquire_every = reacquire_every
        self.keep_full = keep_full
        self.ball_max_age_ms = ball_max_age_ms
        self.hoop_window = hoop_window
        self.hoops = []
        self.ball = (
            None  # (x_center, timestamp_ms, x_velocity_per_s) of last observation
        )
        self.frames_without_ball = 0
        self.frame_number = 0

    @property
    def params(self):
        return dict(
            crop_fraction=self.crop_fraction,
            bootstrap_frames=self.bootstrap_frames,
            refresh_every=self.refresh_every,
            reacquire_every=self.reacquire_every,
            keep_full=self.keep_full,
            ball_max_age_ms=self.ball_max_age_ms,
            hoop_window=self.hoop_window,
        )

    def plan(self, timestamp_ms, w, h):
        """Views for the next frame and the reason they were chosen."""
        n = self.frame_number
        if w / h <= 1.2:
            return full_view(w, h), "not_wide"  # production does not tile either
        fallback = production_views(w, h)
        if n < self.bootstrap_frames or n % self.refresh_every == 0:
            return fallback, "refresh"
        hoops = stationary_hoops(self.hoops[-self.hoop_window :])
        if not hoops:
            return fallback, "no_hoop"
        hoop = max(hoops, key=lambda v: v["observed_frames"])
        targets = [float(center(hoop)[0])]
        if self.ball and timestamp_ms - self.ball[1] <= self.ball_max_age_ms:
            x, seen, vx = self.ball
            targets.append(min(1.0, max(0.0, x + vx * (timestamp_ms - seen) / 1000)))
        elif self.frames_without_ball % self.reacquire_every == 0:
            return fallback, "reacquire"
        crops = self.crops(targets, w, h)
        return (full_view(w, h) if self.keep_full else []) + crops, "roi"

    def crops(self, targets, w, h):
        width = min(w, round(w * self.crop_fraction))
        # Each target stays at least 15% of the crop width away from its edge.
        span = (width / w) * 0.7
        groups = []
        for x in sorted(targets):
            if groups and x - groups[-1][0] <= span:
                groups[-1].append(x)
            else:
                groups.append([x])
        views = []
        for i, group in enumerate(groups):
            middle = (group[0] + group[-1]) / 2 * w
            left = int(min(max(0, round(middle - width / 2)), w - width))
            views.append(("roi%d" % i, (left, 0, left + width, h)))
        return views

    def update(self, observations, timestamp_ms, raw=None):
        """Feed this strategy's own kept (and optionally raw per-view) observations."""
        self.frame_number += 1
        self.hoops.extend(d for d in observations if d["class_name"] == "hoop")
        self.hoops = self.hoops[-self.hoop_window :]
        balls = [d for d in observations if d["class_name"] == "basketball"]
        if not balls:
            self.frames_without_ball += 1
            return
        best = max(balls, key=lambda d: d["confidence"])
        x = float(center(best)[0])
        vx = 0.0
        if self.ball and 0 < timestamp_ms - self.ball[1] <= self.ball_max_age_ms:
            vx = (x - self.ball[0]) / ((timestamp_ms - self.ball[1]) / 1000)
        self.ball = (x, timestamp_ms, vx)
        self.frames_without_ball = 0


def predict_views(detector, frame, timestamp_ms, views):
    """Mirror of BasketballDetector.detect for arbitrary views; keeps each raw view.

    Returns (raw observations before cross-view suppression, synchronized seconds).
    """
    import cv2
    from workers.video.basketball_detector import CLASSES
    from workers.video.runtime import synchronize

    h, w = frame.shape[:2]
    images = [
        cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
        for _, (x1, y1, x2, y2) in views
    ]
    synchronize(detector.device)
    started = time.perf_counter()
    results = detector.model.predict(
        images, threshold=min(detector.thresholds.values())
    )
    synchronize(detector.device)
    seconds = time.perf_counter() - started
    if not isinstance(results, list):
        results = [results]
    raw = []
    for result, (name, (left, top, _, _)) in zip(results, views):
        for box, class_id, confidence in zip(
            result.xyxy, result.class_id, result.confidence
        ):
            label = CLASSES.get(int(class_id))
            confidence = float(confidence)
            if label is None or confidence < detector.thresholds[label]:
                continue
            x1, y1, x2, y2 = map(float, box)
            x1 = max(0, min(w, left + x1))
            x2 = max(0, min(w, left + x2))
            y1 = max(0, min(h, top + y1))
            y2 = max(0, min(h, top + y2))
            if x2 <= x1 or y2 <= y1:
                continue
            raw.append(
                dict(
                    class_name=label,
                    class_id=int(class_id),
                    confidence=confidence,
                    bbox=[x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h],
                    timestamp_ms=timestamp_ms,
                    origin="ebard_rfdetr",
                    view=name,
                )
            )
    return raw, seconds


class AdaptiveBallDetector:
    """Drop-in ``detect(frame, timestamp_ms)`` wrapper around a loaded BasketballDetector."""

    experimental = True

    def __init__(self, detector, policy=None):
        self.detector = detector
        self.policy = policy or AdaptiveCropPolicy()
        self.last_views = []
        self.last_reason = None
        self.last_seconds = 0.0

    def detect(self, frame, timestamp_ms):
        h, w = frame.shape[:2]
        views, reason = self.policy.plan(timestamp_ms, w, h)
        raw, seconds = predict_views(self.detector, frame, timestamp_ms, views)
        kept = suppress(raw)
        self.policy.update(kept, timestamp_ms, raw)
        self.last_views, self.last_reason, self.last_seconds = views, reason, seconds
        return kept


class TileSelectionPolicy(AdaptiveCropPolicy):
    """Prior-position selection among the *production* views only.

    Free-placed crops change RF-DETR's output (the outdoor hoop was found in
    about half of hoop-centred crops but in nearly every fixed tile), so this
    variant never invents a crop: each frame runs a subset of {full, left, right},
    keeping the tile(s) that contain the stationary hoop and the last observed
    ball with ``margin`` (normalized) clearance, plus the same conservative
    refresh/bootstrap/no-hoop/reacquire fallback to all three views.
    """

    def __init__(self, margin=0.05, learn_hoop_tiles=False, **kwargs):
        super().__init__(**kwargs)
        if not 0 <= margin < 0.2:
            raise ValueError("margin must be in [0, 0.2)")
        self.margin = margin
        # When True, the hoop is only trusted to tiles that found it on >=95% of
        # both-tile frames: keeps per-frame hoop observations at extra view cost.
        self.learn_hoop_tiles = learn_hoop_tiles
        # Per-tile hoop hits on frames where both tiles ran (observed, not assumed).
        self.hoop_hits = {"left": 0, "right": 0}
        self.both_tile_frames = 0
        self.last_planned = set()

    def update(self, observations, timestamp_ms, raw=None):
        super().update(observations, timestamp_ms, raw)
        if raw is None or not {"left", "right"} <= self.last_planned:
            return
        self.both_tile_frames += 1
        for name in ("left", "right"):
            self.hoop_hits[name] += name in {
                d["view"] for d in raw if d["class_name"] == "hoop"
            }

    def plan(self, timestamp_ms, w, h):
        views, reason = super().plan(timestamp_ms, w, h)
        self.last_planned = {n for n, _ in views}
        return views, reason

    def reliable_hoop_tiles(self):
        """Tiles that found the hoop on >= 95% of both-tile frames (needs >= 3 frames)."""
        if not self.learn_hoop_tiles or self.both_tile_frames < 3:
            return None
        return {
            n
            for n, hits in self.hoop_hits.items()
            if hits >= 0.95 * self.both_tile_frames
        }

    @property
    def params(self):
        return dict(
            super().params,
            margin=self.margin,
            learn_hoop_tiles=self.learn_hoop_tiles,
            views="production_tiles_only",
        )

    def crops(self, targets, w, h):
        tiles = fixed_tiles(w, h)
        spans = {name: (x1 / w, x2 / w) for name, (x1, _, x2, _) in tiles}
        chosen = set()
        reliable = self.reliable_hoop_tiles()
        hoop_x, targets = targets[0], targets[1:]
        if reliable is None:
            targets = [
                hoop_x
            ] + targets  # no evidence yet: treat the hoop like any target
        for x in targets:
            inside = [
                name
                for name, (lo, hi) in spans.items()
                if lo + self.margin <= x <= hi - self.margin
                or (lo == 0 and x <= hi - self.margin)
                or (hi == 1 and x >= lo + self.margin)
            ]
            if not inside:  # near a seam: keep both tiles
                chosen.update(spans)
            elif not chosen.intersection(inside):
                chosen.add(
                    inside[0]
                    if len(inside) == 1
                    else self._prefer(inside, targets, spans)
                )
        if reliable is not None and not chosen & reliable:
            # Add a tile that has reliably found the hoop, or both tiles if none has.
            chosen.update(sorted(reliable)[:1] if reliable else spans)
        return [t for t in tiles if t[0] in chosen]

    def _prefer(self, names, targets, spans):
        # In the overlap, pick the tile covering the most targets with margin.
        def covered(name):
            lo, hi = spans[name]
            return sum(lo + self.margin <= x <= hi - self.margin for x in targets)

        return max(names, key=covered)
