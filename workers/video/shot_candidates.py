"""Conservative ball/hoop trajectory proposals. Never reports made/missed shots."""

from collections import defaultdict
import math
import numpy as np
from scipy.optimize import linear_sum_assignment
from workers.video.tracker import iou


def xyxy(box):
    x, y, w, h = box
    return [x, y, x + w, y + h]


def center(item):
    x, y, w, h = item["bbox"]
    return np.array([x + w / 2, y + h / 2])


def suppress(detections, threshold=0.5):
    kept = []
    for item in sorted(detections, key=lambda d: -d["confidence"]):
        if all(
            item["class_name"] != other["class_name"]
            or iou(xyxy(item["bbox"]), xyxy(other["bbox"])) < threshold
            for other in kept
        ):
            kept.append(item)
    return kept


class BallTrajectories:
    def __init__(self, max_gap_ms=200, max_speed=3.0):
        self.max_gap_ms = max_gap_ms
        self.max_speed = max_speed
        self.active = {}
        self.next_id = 1

    def update(self, balls, timestamp_ms):
        self.active = {
            k: v
            for k, v in self.active.items()
            if 0 < timestamp_ms - v["timestamp_ms"] <= self.max_gap_ms
        }
        keys = list(self.active)
        matches = {}
        costs = np.full((len(keys), len(balls)), 100.0)
        for r, key in enumerate(keys):
            old = self.active[key]
            dt = (timestamp_ms - old["timestamp_ms"]) / 1000
            predicted = center(old) + old["velocity"] * dt
            for col, ball in enumerate(balls):
                if np.linalg.norm(center(ball) - center(old)) > max(
                    0.025, self.max_speed * dt
                ):
                    continue
                costs[r, col] = np.linalg.norm(center(ball) - predicted)
        if keys and balls:
            rows, cols = linear_sum_assignment(costs)
            for row, col in zip(rows, cols):
                if costs[row, col] >= 100:
                    continue
                # Ambiguous competing balls start a new segment instead of forcing continuity.
                rivals = np.delete(costs[:, col], row)
                others = np.delete(costs[row, :], col)
                second = min(
                    float(rivals.min()) if rivals.size else 100,
                    float(others.min()) if others.size else 100,
                )
                if second - costs[row, col] < 0.01:
                    continue
                matches[col] = keys[row]
        result = []
        for col, ball in enumerate(balls):
            key = matches.get(col)
            if key is None:
                key = "ball-" + str(self.next_id)
                self.next_id += 1
            old = self.active.get(key)
            velocity = (
                (center(ball) - center(old))
                / ((timestamp_ms - old["timestamp_ms"]) / 1000)
                if old
                else np.zeros(2)
            )
            value = dict(ball, ball_track_id=key, timestamp_ms=timestamp_ms)
            self.active[key] = dict(value, velocity=velocity)
            result.append(value)
        return result


def stationary_hoops(observations, min_frames=3):
    clusters = []
    for item in sorted(observations, key=lambda v: -v["confidence"]):
        match = next(
            (g for g in clusters if iou(xyxy(item["bbox"]), xyxy(g[0]["bbox"])) > 0.5),
            None,
        )
        if match is None:
            clusters.append([item])
        else:
            match.append(item)
    result = []
    for cluster in clusters:
        times = {i["timestamp_ms"] for i in cluster}
        if len(times) < min_frames:
            continue
        result.append(
            dict(
                hoop_id="hoop-" + str(len(result) + 1),
                bbox=np.median([i["bbox"] for i in cluster], axis=0).tolist(),
                confidence=float(np.median([i["confidence"] for i in cluster])),
                observed_frames=len(times),
                origin="model_consensus",
                verified=False,
            )
        )
    return result


def release_actor(ball, players, tolerance_ms=250):
    nearby = [
        s
        for s in players
        if abs(s["timestamp_ms"] - ball["timestamp_ms"]) <= tolerance_ms
    ]
    if not nearby:
        return dict(
            track_id=None,
            player_id=None,
            reason="No player evidence near possible release",
        )
    nearest_time = min(abs(s["timestamp_ms"] - ball["timestamp_ms"]) for s in nearby)
    nearby = [
        s
        for s in nearby
        if abs(s["timestamp_ms"] - ball["timestamp_ms"]) == nearest_time
    ]
    point = center(ball)
    choices = []
    for p in nearby:
        x, y, w, h = p["bbox"]
        wrists = [
            np.array([k["x"], k["y"]])
            for k in p.get("keypoints", [])
            if k["name"] in ("left_wrist", "right_wrist") and k.get("visibility", 0) > 0
        ]
        distance = min(
            [float(np.linalg.norm(point - k)) for k in wrists],
            default=math.hypot(
                max(x - point[0], 0, point[0] - x - w),
                max(y - point[1], 0, point[1] - y - h),
            ),
        )
        if distance <= max(0.025, h * 0.5):
            choices.append((distance, p))
    choices.sort(key=lambda v: v[0])
    if not choices or (len(choices) > 1 and choices[1][0] - choices[0][0] < 0.02):
        return dict(
            track_id=None, player_id=None, reason="Release attribution ambiguous"
        )
    p = choices[0][1]
    return dict(
        track_id=p["track_id"],
        player_id=p.get("player_id"),
        identity_confirmed=False,
        reason="Nearest visible player to possible release; requires review",
    )


def propose_shots(ball_observations, hoops, duration_ms, players=(), max_gap_ms=200):
    tracks = defaultdict(list)
    candidates = []
    for item in ball_observations:
        tracks[item["ball_track_id"]].append(item)
    for track_id, observations in tracks.items():
        points = sorted(observations, key=lambda p: p["timestamp_ms"])
        for hoop in hoops:
            hx, hy, hw, hh = hoop["bbox"]
            cooldown = -10000
            for index, item in enumerate(points):
                timestamp = item["timestamp_ms"]
                if timestamp - cooldown < 2000:
                    continue
                history = [
                    p
                    for p in points[: index + 1]
                    if 0 <= timestamp - p["timestamp_ms"] <= 2000
                ]
                if len(history) < 4 or timestamp - history[0]["timestamp_ms"] < 150:
                    continue
                if any(
                    b["timestamp_ms"] - a["timestamp_ms"] > max_gap_ms
                    for a, b in zip(history, history[1:])
                ):
                    continue
                centers = [center(p) for p in history]
                ascending = any(
                    b[1] < a[1] - 0.002 for a, b in zip(centers, centers[1:])
                )
                descending = centers[-1][1] > centers[-2][1] + 0.001
                above = any(p[1] < hy for p in centers[:-1])
                near = (
                    abs(centers[-1][0] - (hx + hw / 2)) < max(hw * 2, 0.025)
                    and hy - hh <= centers[-1][1] <= hy + hh * 3
                )
                if not (ascending and descending and above and near):
                    continue
                candidate = dict(
                    candidate_id=track_id
                    + "-"
                    + hoop["hoop_id"]
                    + "-"
                    + str(timestamp),
                    event_type="shot_attempt_candidate",
                    outcome="unknown",
                    verified=False,
                    review_required=True,
                    origin="ball_hoop_trajectory",
                    ball_track_id=track_id,
                    hoop_id=hoop["hoop_id"],
                    start_ms=max(0, history[0]["timestamp_ms"] - 1500),
                    end_ms=min(duration_ms, timestamp + 1800),
                    approach_ms=timestamp,
                    evidence_timestamps=[p["timestamp_ms"] for p in history],
                    evidence_quality=float(min(p["confidence"] for p in history)),
                    actor=release_actor(history[0], players),
                    reason="Observed ascent and descent approaching hoop region; outcome and possession unverified",
                )
                candidates.append(candidate)
                cooldown = timestamp
    return sorted(candidates, key=lambda c: c["approach_ms"])
