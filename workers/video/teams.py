"""Anonymous jersey-colour grouping and conservative non-player flags.

Everything here is a reviewable visual heuristic, not identity or roster truth:

* Colour comes only from the torso (shoulders to hips, or a fixed band of the box
  below the head). No head or face pixels are read.
* Teams are labelled ``Team A`` / ``Team B`` by first appearance, never by colour,
  jersey number or roster. Tracks that do not clearly belong to a dominant,
  cohesive kit group stay ``unknown``; mixed-colour sides stay unknown.
* Non-player flags never delete data. ``likely_non_player`` needs at least two
  independent signals including positional evidence; one signal means
  ``review_required``; otherwise the role is ``unknown``. There is no court
  calibration; an image-space ``court_polygon`` is used only when supplied.
"""

import cv2
import numpy as np

UNKNOWN = "unknown"
TEAM_LABELS = ("Team A", "Team B")
# COCO keypoint indices used by the pose model in this repository.
_SHOULDERS, _HIPS = (5, 6), (11, 12)


def torso_box(box, keypoints=None, min_visibility=0.5):
    """Pixel torso rectangle inside ``box``; excludes head/neck by construction.

    ``keypoints`` is optional COCO-ordered ``(17, 2|3)`` pixel coordinates. If
    both shoulders and hips are usable the torso spans them, otherwise a fixed
    band (22%-52% of box height, middle half of width) is used.
    """
    x1, y1, x2, y2 = map(float, box)
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    if keypoints is not None:
        points = np.asarray(keypoints, dtype=float)
        if points.ndim == 2 and points.shape[0] >= 13 and points.shape[1] >= 2:
            chosen = points[list(_SHOULDERS + _HIPS)]
            visible = (
                chosen[:, 2] >= min_visibility
                if points.shape[1] >= 3
                else np.ones(4, bool)
            )
            inside = (
                (chosen[:, 0] >= x1)
                & (chosen[:, 0] <= x2)
                & (chosen[:, 1] >= y1)
                & (chosen[:, 1] <= y2)
            )
            if visible.all() and inside.all():
                shoulder_y = chosen[:2, 1].mean()
                hip_y = chosen[2:, 1].mean()
                left, right = chosen[:, 0].min(), chosen[:, 0].max()
                if hip_y - shoulder_y > 0.15 * h and right - left > 0.1 * w:
                    pad = 0.15 * (right - left)
                    top = shoulder_y + 0.1 * (hip_y - shoulder_y)
                    return [left + pad, top, right - pad, hip_y]
    return [x1 + 0.25 * w, y1 + 0.22 * h, x2 - 0.25 * w, y1 + 0.52 * h]


def torso_colour_feature(frame, box, keypoints=None):
    """Robust torso colour as weighted CIELAB ``[0.5 L*, a*, b*]`` (ΔE-like units).

    Uses the per-channel median of torso pixels so arms, background slivers and
    numbers have little effect. Lightness is half-weighted to reduce
    illumination sensitivity while still separating white from dark kits.
    Returns ``None`` when the torso crop is too small to be meaningful.
    """
    region = torso_box(box, keypoints)
    if region is None:
        return None
    height, width = frame.shape[:2]
    left, top = max(0, int(round(region[0]))), max(0, int(round(region[1])))
    right, bottom = (
        min(width, int(round(region[2]))),
        min(height, int(round(region[3]))),
    )
    if right - left < 3 or bottom - top < 3:
        return None
    lab = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2LAB).reshape(-1, 3)
    median = np.median(lab.astype(float), axis=0)
    return np.array(
        [0.5 * median[0] * 100 / 255, median[1] - 128, median[2] - 128], dtype=float
    )


class TeamGrouper:
    """Accumulate torso colours per key (track or provisional player) and name
    anonymous kit groups only when the evidence is clear.

    Per-key median colours are grouped into cohesive colour modes (complete
    linkage: every pair within ``cohesion`` weighted-ΔE). Modes are ranked by
    support (observation count, roughly person-time). Two modes become Team A/B
    when both are supported by at least ``min_members`` keys, are at least
    ``min_separation`` apart, and the second clearly dominates any third mode
    (``dominance`` ×). If only the largest mode dominates, it alone is labelled
    and the rest stay unknown. Anything else remains unknown. Exclude likely
    non-players (spectators often share dark colours) before assigning.
    """

    def __init__(
        self,
        min_observations=3,
        cohesion=30.0,
        min_separation=18.0,
        margin=8.0,
        dominance=1.5,
        min_members=2,
    ):
        self.min_observations, self.cohesion = min_observations, cohesion
        self.min_separation, self.margin = min_separation, margin
        self.dominance, self.min_members = dominance, min_members
        self.features = {}
        self.first_seen = {}
        self._order = 0

    def observe(self, key, feature, timestamp_ms=None):
        key = str(key)
        self._order += 1
        if key not in self.first_seen:
            self.first_seen[key] = (
                float("inf") if timestamp_ms is None else float(timestamp_ms),
                self._order,
            )
        if feature is None:
            return
        feature = np.asarray(feature, dtype=float)
        if feature.shape != (3,) or not np.all(np.isfinite(feature)):
            raise ValueError("Expected a finite torso_colour_feature vector")
        self.features.setdefault(key, []).append(feature)

    def _modes(self, keys, profiles):
        """Deterministic complete-linkage clustering; order of keys is irrelevant."""
        clusters = [[k] for k in sorted(keys, key=lambda k: self.first_seen[k])]

        def diameter(a, b):
            return max(np.linalg.norm(profiles[x] - profiles[y]) for x in a for y in b)

        while len(clusters) > 1:
            best = None
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    d = diameter(clusters[i], clusters[j])
                    if d <= self.cohesion and (best is None or d < best[0]):
                        best = (d, i, j)
            if best is None:
                break
            _, i, j = best
            clusters[i] = clusters[i] + clusters.pop(j)
        return clusters

    def assign(self, exclude=()):
        """Return ``({key: assignment}, summary)`` for every observed key.

        Each assignment has ``team`` (``Team A``/``Team B``/``unknown``),
        ``reason`` and distance evidence. It is never a confirmed identity.
        """
        exclude = {str(k) for k in exclude}
        result = {
            key: dict(
                team=UNKNOWN,
                reason="excluded" if key in exclude else "insufficient_observations",
                observations=len(self.features.get(key, [])),
            )
            for key in self.first_seen
        }
        profiles = {
            key: np.median(np.stack(values), axis=0)
            for key, values in self.features.items()
            if key not in exclude and len(values) >= self.min_observations
        }
        summary = dict(
            method="torso-lab-median-modes-v2",
            separable=False,
            separation=None,
            groups={},
            identity_confirmed=False,
        )
        modes = []
        for members in self._modes(profiles, profiles):
            support = sum(len(self.features[k]) for k in members)
            weights = np.array([len(self.features[k]) for k in members], dtype=float)
            centre = np.average(
                np.stack([profiles[k] for k in members]), axis=0, weights=weights
            )
            first = min(self.first_seen[k] for k in members)
            modes.append(
                dict(members=members, support=support, centre=centre, first=first)
            )
        modes.sort(key=lambda m: (-m["support"], m["first"]))
        supports = [m["support"] for m in modes] + [0, 0, 0]
        eligible = [len(m["members"]) >= self.min_members for m in modes] + [False] * 3
        labelled = []
        if (
            len(modes) >= 2
            and eligible[0]
            and eligible[1]
            and supports[1] >= self.dominance * supports[2]
            and np.linalg.norm(modes[0]["centre"] - modes[1]["centre"])
            >= self.min_separation
        ):
            labelled = modes[:2]
            summary["reason"] = "two_dominant_colour_groups"
            summary["separable"] = True
        elif (
            len(modes) >= 2
            and eligible[0]
            and supports[0] >= self.dominance * supports[1]
        ):
            labelled = modes[:1]
            summary["reason"] = "one_dominant_colour_group_only"
        else:
            summary["reason"] = "no_dominant_colour_groups"
        if len(labelled) == 2:
            summary["separation"] = round(
                float(np.linalg.norm(labelled[0]["centre"] - labelled[1]["centre"])), 2
            )
        # Anonymous naming by first appearance of a group member, never by colour.
        labelled.sort(key=lambda m: m["first"])
        names = {id(m): TEAM_LABELS[i] for i, m in enumerate(labelled)}
        for mode in modes:
            name = names.get(id(mode))
            for key in mode["members"]:
                own = float(np.linalg.norm(profiles[key] - mode["centre"]))
                others = [
                    float(np.linalg.norm(profiles[key] - m["centre"]))
                    for m in labelled
                    if m is not mode
                ]
                evidence = dict(distance_own=round(own, 2))
                if others:
                    evidence["distance_other"] = round(min(others), 2)
                if name is None:
                    reason = (
                        "colour_outlier"
                        if len(mode["members"]) == 1
                        else "unlabelled_colour_group"
                    )
                    result[key].update(reason=reason, **evidence)
                elif others and min(others) - own < self.margin:
                    result[key].update(reason="between_groups", **evidence)
                else:
                    result[key].update(team=name, reason="clear_group", **evidence)
        summary["colour_modes"] = [
            dict(
                label=names.get(id(m), UNKNOWN),
                members=len(m["members"]),
                support=m["support"],
                centre=[round(float(v), 1) for v in m["centre"]],
            )
            for m in modes
        ]
        for m in labelled:
            summary["groups"][names[id(m)]] = dict(members=len(m["members"]))
        return result, summary


def _footpoints(observations, aspect):
    return np.array(
        [[(b[0] + b[2] / 2) * aspect, b[1] + b[3]] for _, b in observations]
    )


def flag_non_players(
    samples,
    key="track_id",
    aspect=16 / 9,
    court_polygon=None,
    team_assignments=None,
    min_span_ms=3000,
    min_observations=6,
    stationary_heights=0.4,
    seated_aspect=0.75,
    edge_margin=0.005,
):
    """Flag likely spectators/bench/staff from pipeline samples (normalized bbox).

    Signals per key: ``stationary`` (footpoint spread under ``stationary_heights``
    box heights over at least ``min_span_ms``), ``seated_posture`` (median
    width/height in pixels at least ``seated_aspect``), ``frame_edge`` (box touches
    the image border in most observations), ``outside_court`` (only with an
    image-space ``court_polygon``) and ``colour_outlier`` (from TeamGrouper).
    ``likely_non_player`` requires two signals including ``stationary`` or
    ``outside_court``; one signal or a short observation window is never enough,
    and ``colour_outlier`` alone never even triggers review.
    Referees cannot be distinguished reliably and are not targeted.
    """
    grouped = {}
    for sample in samples:
        grouped.setdefault(str(sample[key]), []).append(
            (float(sample["timestamp_ms"]), [float(v) for v in sample["bbox"]])
        )
    polygon = (
        None if court_polygon is None else np.asarray(court_polygon, dtype=np.float32)
    )
    flags = {}
    for name, observations in grouped.items():
        observations.sort(key=lambda item: item[0])
        span = observations[-1][0] - observations[0][0]
        heights = np.array([b[3] for _, b in observations])
        evidence = dict(observations=len(observations), span_ms=round(span))
        if len(observations) < min_observations or span < min_span_ms:
            flags[name] = dict(
                role_status=UNKNOWN,
                signals=[],
                reason="insufficient_observations",
                evidence=evidence,
            )
            continue
        feet = _footpoints(observations, aspect)
        spread = np.percentile(feet, 90, axis=0) - np.percentile(feet, 10, axis=0)
        movement = float(np.linalg.norm(spread) / np.median(heights))
        ratio = float(
            np.median([b[2] * aspect / max(b[3], 1e-6) for _, b in observations])
        )
        edge = float(
            np.mean(
                [
                    b[0] <= edge_margin
                    or b[1] <= edge_margin
                    or b[0] + b[2] >= 1 - edge_margin
                    or b[1] + b[3] >= 1 - edge_margin
                    for _, b in observations
                ]
            )
        )
        evidence.update(
            movement_heights=round(movement, 3),
            width_height_ratio=round(ratio, 3),
            edge_fraction=round(edge, 3),
        )
        signals = []
        if movement < stationary_heights:
            signals.append("stationary")
        if ratio >= seated_aspect:
            signals.append("seated_posture")
        if edge >= 0.5:
            signals.append("frame_edge")
        if polygon is not None:
            outside = float(
                np.mean(
                    [
                        cv2.pointPolygonTest(
                            polygon, (b[0] + b[2] / 2, b[1] + b[3]), False
                        )
                        < 0
                        for _, b in observations
                    ]
                )
            )
            evidence["outside_court_fraction"] = round(outside, 3)
            if outside >= 0.8:
                signals.append("outside_court")
        team = (team_assignments or {}).get(name)
        if team and team.get("reason") == "colour_outlier":
            signals.append("colour_outlier")
        positional = {"stationary", "outside_court"} & set(signals)
        visual = set(signals) - {"colour_outlier"}
        if len(signals) >= 2 and positional:
            status, reason = "likely_non_player", "multiple_independent_signals"
        elif visual:
            status, reason = "review_required", "insufficient_independent_signals"
        else:
            # Colour alone (e.g. a player in an unusual shirt) never flags a role.
            status, reason = UNKNOWN, "no_positional_signal"
        flags[name] = dict(
            role_status=status, signals=signals, reason=reason, evidence=evidence
        )
    return flags
