"""Frame-to-frame person association. Track IDs are not athlete identities.

``Tracker`` is the original IoU/Hungarian baseline and is kept unchanged so
existing callers and benchmarks stay reproducible. ``ContinuityTracker`` is a
drop-in alternative (``update(boxes)`` still works) that adds constant-velocity
prediction, short-occlusion recovery, optional torso/uniform appearance gating
and rejection of ambiguous associations. When it cannot tell two people apart it
starts a new track (a fragment) instead of guessing (a switch); reconnecting
fragments is left to the identity layer and to reviewable merge proposals.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment

_REJECT = 1e3


def iou(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


class Tracker:
    def __init__(self, threshold=0.25, max_age=3):
        self.tracks = {}
        self.next_id = 1
        self.threshold, self.max_age = threshold, max_age

    def update(self, boxes):
        keys = list(self.tracks)
        assigned, result = set(), {}
        for key in keys:
            self.tracks[key]["age"] += 1
        if keys and len(boxes):
            scores = np.array(
                [[iou(self.tracks[k]["box"], b) for b in boxes] for k in keys]
            )
            # Gate before assignment so invalid pairings cannot displace valid matches.
            rows, cols = linear_sum_assignment(
                np.where(scores >= self.threshold, 1 - scores, 100)
            )
            for row, col in zip(rows, cols):
                if scores[row, col] >= self.threshold:
                    result[int(col)] = keys[row]
                    assigned.add(int(col))
        for index, box in enumerate(boxes):
            if index not in assigned:
                result[index] = self.next_id
                self.next_id += 1
            self.tracks[result[index]] = {"box": list(map(float, box)), "age": 0}
        self.tracks = {k: v for k, v in self.tracks.items() if v["age"] <= self.max_age}
        return [result[i] for i in range(len(boxes))]


def _center(box):
    return np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2], dtype=float)


def _height(box):
    return max(1e-6, float(box[3] - box[1]))


def _shift(box, offset):
    return [
        box[0] + offset[0],
        box[1] + offset[1],
        box[2] + offset[0],
        box[3] + offset[1],
    ]


def _unit(vector):
    if vector is None:
        return None
    vector = np.asarray(vector, dtype=float).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 and np.all(np.isfinite(vector)) else None


class ContinuityTracker:
    """Motion/appearance tracker with ambiguity rejection.

    ``update(boxes, descriptors=None, timestamp_ms=None)`` returns one integer
    track ID per box, in input order, like ``Tracker.update``. Without
    timestamps every call is one time unit and tracks expire after ``max_age``
    missed updates; with timestamps (strictly increasing, source milliseconds)
    tracks expire after ``max_lost_ms``. ``last_details`` explains each output.
    Descriptors are optional L2-comparable vectors (e.g. ``uniform_descriptor``);
    descriptors of boxes overlapping another detection are ignored because they
    mix two people's kit.
    """

    def __init__(
        self,
        threshold=0.25,
        max_age=6,
        max_lost_ms=1500,
        margin=0.08,
        appearance_gate=0.35,
        occlusion_iou=0.3,
        base_radius=0.6,
        radius_growth=0.35,
        max_radius=2.0,
        lost_penalty=0.04,
        provisional_penalty=0.15,
        provisional_hits=3,
    ):
        self.threshold, self.max_age, self.max_lost_ms = threshold, max_age, max_lost_ms
        self.margin, self.appearance_gate = margin, appearance_gate
        self.occlusion_iou = occlusion_iou
        self.base_radius, self.radius_growth = base_radius, radius_growth
        self.max_radius, self.lost_penalty = max_radius, lost_penalty
        self.provisional_penalty, self.provisional_hits = (
            provisional_penalty,
            provisional_hits,
        )
        self.tracks = {}
        self.next_id = 1
        self.last_details = []
        self.stats = dict(created=0, recovered=0, ambiguous_rejections=0)
        self._timed = None
        self._now = None
        self._interval = None

    # -- time -------------------------------------------------------------
    def _advance(self, timestamp_ms):
        timed = timestamp_ms is not None
        if self._timed is None:
            self._timed = timed
        elif self._timed != timed:
            raise ValueError("Use timestamps on every update or on none")
        now = (
            float(timestamp_ms)
            if timed
            else (0.0 if self._now is None else self._now + 1)
        )
        if self._now is not None:
            if now <= self._now:
                raise ValueError("Tracker updates must arrive in source-time order")
            dt = now - self._now
            self._interval = (
                dt if self._interval is None else 0.8 * self._interval + 0.2 * dt
            )
        self._now = now
        return now

    def _missed_updates(self, track, now):
        interval = self._interval or 1.0
        return max(0, int(round((now - track["last_seen"]) / interval)) - 1)

    # -- association --------------------------------------------------------
    def _predict(self, track, now):
        interval = self._interval or 1.0
        dt = min(now - track["last_seen"], 3 * interval)
        return _shift(track["box"], track["velocity"] * dt)

    def _cost(self, track, box, descriptor, now):
        predicted = self._predict(track, now)
        ratio = _height(box) / _height(track["box"])
        if not 0.6 <= ratio <= 1.67:
            return None
        scale = (_height(box) + _height(track["box"])) / 2
        distance = float(np.linalg.norm(_center(box) - _center(predicted))) / scale
        missed = self._missed_updates(track, now)
        radius = min(self.max_radius, self.base_radius + self.radius_growth * missed)
        overlap = iou(predicted, box)
        if overlap < self.threshold and distance > radius:
            return None
        geometry = 0.5 * (1 - overlap) + 0.5 * min(1.0, distance / radius)
        appearance = None
        if descriptor is not None and track["descriptor"] is not None:
            appearance = float(
                np.clip(1 - np.dot(descriptor, track["descriptor"]), 0, 2)
            )
            if appearance > self.appearance_gate:
                return None
        cost = (
            geometry
            if appearance is None
            else (0.7 * geometry + 0.3 * appearance / self.appearance_gate)
        )
        if track["provisional"] and track["hits"] < self.provisional_hits:
            # Born from an ambiguity: let the contested tracks reclaim the person
            # once motion/appearance separate them, instead of locking in a guess.
            cost += self.provisional_penalty
        return cost + self.lost_penalty * missed

    def _ambiguous(self, costs, pairs, keys, missed):
        """Return assigned (row, col) pairs whose choice is not unique by ``margin``."""
        assigned_rows = {r for r, _ in pairs}
        assigned_cols = {c for _, c in pairs}
        flagged = {}
        for r, c in pairs:
            value = costs[r, c]
            rivals = []
            # Another current detection could equally be this track.
            for c2 in range(costs.shape[1]):
                if (
                    c2 != c
                    and c2 not in assigned_cols
                    and costs[r, c2] - value < self.margin
                ):
                    rivals.append(("detection", c2))
            # An unmatched track seen at least as recently could equally own it.
            for r2 in range(costs.shape[0]):
                if (
                    r2 != r
                    and r2 not in assigned_rows
                    and missed[r2] <= missed[r]
                    and costs[r2, c] - value < self.margin
                ):
                    rivals.append(("track", keys[r2]))
            # Swapping with another assigned pair would be nearly as good.
            for r2, c2 in pairs:
                if r2 == r or costs[r, c2] >= _REJECT or costs[r2, c] >= _REJECT:
                    continue
                if costs[r, c2] + costs[r2, c] - value - costs[r2, c2] < self.margin:
                    rivals.append(("track", keys[r2]))
            if rivals:
                flagged[(r, c)] = sorted({k for kind, k in rivals if kind == "track"})
        return flagged

    def update(self, boxes, descriptors=None, timestamp_ms=None):
        boxes = [list(map(float, b)) for b in boxes]
        if descriptors is None:
            descriptors = [None] * len(boxes)
        if len(descriptors) != len(boxes):
            raise ValueError("Box and descriptor counts must agree")
        now = self._advance(timestamp_ms)
        if self._timed:
            # Expire before associating so a long absence can never be bridged here.
            self.tracks = {
                k: t
                for k, t in self.tracks.items()
                if now - t["last_seen"] <= self.max_lost_ms
            }
        clean = [
            None
            if any(
                i != j and iou(b, o) > self.occlusion_iou for j, o in enumerate(boxes)
            )
            else _unit(d)
            for i, (b, d) in enumerate(zip(boxes, descriptors))
        ]
        keys = list(self.tracks)
        missed = [self._missed_updates(self.tracks[k], now) for k in keys]
        costs = np.full((len(keys), len(boxes)), _REJECT)
        for r, key in enumerate(keys):
            for c, (box, descriptor) in enumerate(zip(boxes, clean)):
                value = self._cost(self.tracks[key], box, descriptor, now)
                if value is not None:
                    costs[r, c] = value
        pairs = []
        if keys and boxes:
            rows, cols = linear_sum_assignment(costs)
            pairs = [
                (int(r), int(c)) for r, c in zip(rows, cols) if costs[r, c] < _REJECT
            ]
        flagged = self._ambiguous(costs, pairs, keys, missed)
        result, details = {}, {}
        for r, c in pairs:
            if (r, c) in flagged:
                continue
            key = keys[r]
            track = self.tracks[key]
            dt = now - track["last_seen"]
            measured = (_center(boxes[c]) - _center(track["box"])) / dt
            track["velocity"] = (
                measured
                if track["hits"] < 2
                else (0.6 * measured + 0.4 * track["velocity"])
            )
            if clean[c] is not None:
                track["descriptor"] = (
                    clean[c]
                    if track["descriptor"] is None
                    else _unit(0.8 * track["descriptor"] + 0.2 * clean[c])
                )
            status = "recovered" if missed[r] else "continued"
            if missed[r]:
                self.stats["recovered"] += 1
            track.update(box=boxes[c], last_seen=now, hits=track["hits"] + 1)
            result[c] = key
            details[c] = dict(
                track_id=key,
                status=status,
                cost=round(float(costs[r, c]), 4),
                missed_updates=missed[r],
                ambiguous_with=[],
            )
        for (r, c), rivals in flagged.items():
            self.stats["ambiguous_rejections"] += 1
            details[c] = dict(ambiguous_with=sorted({keys[r], *rivals}))
        for c, box in enumerate(boxes):
            if c in result:
                continue
            key = self.next_id
            self.next_id += 1
            self.stats["created"] += 1
            self.tracks[key] = dict(
                box=box,
                velocity=np.zeros(2),
                descriptor=clean[c],
                last_seen=now,
                hits=1,
                provisional=c in details,
            )
            result[c] = key
            rivals = details.get(c, {}).get("ambiguous_with", [])
            details[c] = dict(
                track_id=key,
                status="ambiguous" if rivals else "new",
                cost=None,
                missed_updates=0,
                ambiguous_with=rivals,
            )
        for key, track in self.tracks.items():
            # ``age`` mirrors the baseline tracker's field: missed updates so far.
            track["age"] = (
                0 if track["last_seen"] == now else self._missed_updates(track, now) + 1
            )
        if self._timed:
            self.tracks = {
                k: t
                for k, t in self.tracks.items()
                if now - t["last_seen"] <= self.max_lost_ms
            }
        else:
            self.tracks = {
                k: t for k, t in self.tracks.items() if t["age"] <= self.max_age
            }
        self.last_details = [details[c] for c in range(len(boxes))]
        return [result[c] for c in range(len(boxes))]
