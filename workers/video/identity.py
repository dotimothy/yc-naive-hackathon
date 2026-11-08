"""Conservative, anonymous game-level association above temporary track IDs.

Uses uniform/body colour (no face crops), motion and one-to-one assignment. These
are provisional visual associations, never named identities or calibrated certainty.
"""

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment
from workers.video.tracker import iou


def uniform_descriptor(frame, box):
    """L2-normalized torso/leg colour signature, excluding the upper quarter/head."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(float, box)
    left, right = max(0, int(x1 + 0.2 * (x2 - x1))), min(w, int(x2 - 0.2 * (x2 - x1)))
    top, bottom = max(0, int(y1 + 0.25 * (y2 - y1))), min(h, int(y1 + 0.9 * (y2 - y1)))
    if right - left < 4 or bottom - top < 8:
        return None
    crop = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    zones = np.array_split(crop, 2, axis=0)
    features = []
    for zone in zones:
        hs = cv2.calcHist([zone], [0, 1], None, [12, 4], [0, 180, 0, 256]).reshape(-1)
        value = cv2.calcHist([zone], [2], None, [8], [0, 256]).reshape(-1)
        features.extend((hs / max(1, float(hs.sum()))).tolist())
        features.extend((value / max(1, float(value.sum()))).tolist())
    vector = np.asarray(features, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def appearance_distance(a, b):
    if a is None or b is None:
        return None
    return float(np.clip(1 - np.dot(a, b), 0, 1))


def footpoint(box):
    return np.array([(box[0] + box[2]) / 2, box[3]], dtype=float)


class PlayerIdentityTracker:
    """One instance per full source game; retains all identity prototypes until EOF.

    EXPERIMENTAL, opt-in. With ``continuity=True`` a raw track that keeps feeding
    the same provisional player stays with that player unless there is contrary
    evidence: a strong uniform-colour contradiction (``split_distance``;
    same-person uniform distances on development footage stayed below 0.49) or
    an implausible jump. Colour similarity is never used as positive identity
    for a continued track. New or fragmented raw tracks still go through the
    conservative re-identification below, which holds ties as ``ambiguous``.

    ``continuity=False`` (default) keeps the original v1 behaviour. On held-out
    TrackID3x3 clips v2 cut final-player ID switches 144 -> 56 but raised
    contaminated observations 18 -> 76, because it inherits raw-track swaps; it
    is therefore not the default. See docs/TRACKING_HANDOFF.md.

    ``reconnection`` controls links between *different* raw tracks (and a raw
    track ID that reappears after more than ``short_gap_ms``):

    * ``"review"`` (default): never applied automatically. The fragment gets a
      new provisional player with status ``ambiguous`` and the withheld link in
      ``identity_candidates``; ``reconnection_suggestions()`` lists the evidence
      for a reviewer. Same raw-track continuation is unchanged.
    * ``"automatic"``: legacy behaviour (status ``reidentified``). Kept so the
      existing evaluator comparison is reproducible; not for stat attribution.
      Of 27 checkable automatic reconnections on dev + held-out TrackID3x3 clips,
      4 joined two different people. See docs/IDENTITY_REVIEW_HANDOFF.md.
    """

    RECONNECTION_POLICIES = ("review", "automatic")

    def __init__(
        self,
        game_id,
        short_gap_ms=2000,
        margin=0.12,
        continuity=False,
        split_distance=0.6,
        reconnection="review",
    ):
        if reconnection not in self.RECONNECTION_POLICIES:
            raise ValueError(
                "reconnection must be one of %s" % (self.RECONNECTION_POLICIES,)
            )
        self.game_id = game_id
        self.short_gap_ms = short_gap_ms
        self.margin = margin
        self.continuity = continuity
        self.split_distance = split_distance
        self.reconnection = reconnection
        self.continuity_splits = 0
        self.withheld_reconnections = 0
        self.suggestions = []
        self._suggested = set()
        self._player_times = {}
        self._tracklet_times = {}
        self._player_tracklets = {}
        self.players = {}
        self.bindings = {}
        self.segments = {}
        self.next_id = 1
        self.reconnections = 0
        self.ambiguous_observations = 0
        self.last_timestamp = -1

    def _cost(self, player, box, descriptor, timestamp, bound):
        gap = timestamp - player["last_ms"]
        if gap <= 0:
            return None
        distance = appearance_distance(player["descriptor"], descriptor)
        scale = max(12.0, (player["box"][3] - player["box"][1] + box[3] - box[1]) / 2)
        if gap <= self.short_gap_ms:
            prediction = player["point"] + player["velocity"] * min(gap, 750)
            displacement = float(np.linalg.norm(footpoint(box) - prediction)) / scale
            radius = 0.75 + min(gap / 1000, 2) * 1.75
            if displacement > radius or (distance is not None and distance > 0.30):
                return None
            if distance is None:
                # Missing appearance cannot establish a new cross-track identity.
                if not bound or gap > 500 or displacement > 0.5:
                    return None
                appearance = 0.1
            else:
                appearance = distance
            cost = 0.6 * min(1, displacement / radius) + 0.4 * min(1, appearance / 0.30)
            if bound:
                cost = max(0, cost - 0.10)
            return cost
        # A returning player after a long absence needs very distinctive evidence.
        # All game identities remain in memory, but no jersey/face identity is guessed.
        if distance is None or distance > 0.035 or player["observations"] < 3:
            return None
        return 0.15 + 0.65 * distance / 0.035

    def _continues(self, track, box, descriptor, timestamp):
        """Player still owned by this raw track, or None if there is contrary evidence."""
        pid = self.bindings.get(track)
        player = self.players.get(pid)
        if player is None or player.get("track") != track:
            return None
        gap = timestamp - player["last_ms"]
        if gap <= 0 or gap > self.short_gap_ms:
            return None
        scale = max(12.0, (player["box"][3] - player["box"][1] + box[3] - box[1]) / 2)
        jump = float(np.linalg.norm(footpoint(box) - player["point"])) / scale
        distance = appearance_distance(player["descriptor"], descriptor)
        if jump > 1.0 + 2.0 * min(gap / 1000, 2) or (
            distance is not None and distance >= self.split_distance
        ):
            self.continuity_splits += 1
            return None
        return pid

    def _same_track(self, pid, track, timestamp):
        """True only for a short-gap continuation of the raw track this player owns."""
        player = self.players[pid]
        return (
            self.bindings.get(track) == pid
            and player.get("track") == track
            and 0 < timestamp - player["last_ms"] <= self.short_gap_ms
        )

    def _suggest(self, tracklet, track, pid, withheld, costs, keys, col, timestamp):
        """Record a withheld cross-track link once per new fragment, for review."""
        if tracklet in self._suggested:
            return
        self._suggested.add(tracklet)
        self.withheld_reconnections += 1
        ranked = sorted(withheld, key=lambda k: costs[keys.index(k), col])
        self.suggestions.append(
            dict(
                track_id=tracklet,
                raw_track_id=track,
                provisional_player_id=pid,
                timestamp_ms=timestamp,
                candidates=[
                    dict(
                        player_id=k,
                        player_label=self.players[k]["label"],
                        score=round(float(costs[keys.index(k), col]), 4),
                        gap_ms=int(timestamp - self.players[k]["last_ms"]),
                        last_track_id=self.players[k].get("track"),
                    )
                    for k in ranked
                ],
                status="needs_review",
                method="withheld-reconnection-v1",
                identity_confirmed=False,
                requires_review=True,
            )
        )

    def reconnection_suggestions(self):
        """Withheld cross-track links (``reconnection="review"``) for a reviewer.

        Never applied automatically. Call after the last frame: a candidate whose
        player was observed at the same source time as the fragment is a different
        person and is moved to ``excluded_candidates``; a suggestion left without
        candidates is dropped. Remaining candidates are ranked by score, which is
        not a calibrated probability and must not be auto-accepted. Each candidate
        lists its ``track_ids`` (tracklet keys), so an accepted link is a reviewer
        group of those plus ``track_id``.
        """
        out = []
        for s in self.suggestions:
            times = self._tracklet_times.get(s["track_id"], set())
            valid, excluded = [], []
            for c in s["candidates"]:
                seen = self._player_times.get(c["player_id"], set())
                c = dict(
                    c, track_ids=sorted(self._player_tracklets.get(c["player_id"], ()))
                )
                (excluded if seen & times else valid).append(c)
            if valid:
                out.append(
                    dict(
                        s,
                        candidates=valid,
                        excluded_candidates=[
                            dict(c, reason="visible_at_same_time") for c in excluded
                        ],
                    )
                )
        return out

    def update(self, track_ids, boxes, descriptors, timestamp_ms):
        if timestamp_ms <= self.last_timestamp:
            raise ValueError("Identity observations must arrive in source-time order")
        self.last_timestamp = timestamp_ms
        if not (len(track_ids) == len(boxes) == len(descriptors)):
            raise ValueError("Track, box and descriptor counts must agree")
        if len(boxes) == 0:
            return []
        # Avoid contaminating an identity prototype with an overlapping person's kit.
        descriptors = [
            None
            if any(i != j and iou(box, other) > 0.35 for j, other in enumerate(boxes))
            else descriptor
            for i, (box, descriptor) in enumerate(zip(boxes, descriptors))
        ]
        continued = {}
        if self.continuity:
            for col, (track, box, descriptor) in enumerate(
                zip(track_ids, boxes, descriptors)
            ):
                pid = self._continues(str(track), box, descriptor, timestamp_ms)
                if pid is not None and pid not in continued.values():
                    continued[col] = pid
        keys = list(self.players)
        costs = np.full((len(keys), len(boxes)), 100.0, dtype=float)
        for row, pid in enumerate(keys):
            if pid in continued.values():
                continue  # visible elsewhere in this frame: cannot be anyone else
            for col, (track, box, descriptor) in enumerate(
                zip(track_ids, boxes, descriptors)
            ):
                if col in continued:
                    continue
                value = self._cost(
                    self.players[pid],
                    box,
                    descriptor,
                    timestamp_ms,
                    self.bindings.get(str(track)) == pid,
                )
                if value is not None:
                    costs[row, col] = value
        proposals = {}
        for col, pid in continued.items():
            value = self._cost(
                self.players[pid], boxes[col], descriptors[col], timestamp_ms, True
            )
            proposals[col] = (pid, value, "continued")
        # Pairs that would join two different raw tracks (or a raw ID reused after a
        # long gap). Under the review policy they stay in ``costs`` so they still
        # count as rivals in the ambiguity test, but they can never be assigned.
        cross = np.zeros(costs.shape, dtype=bool)
        for row, pid in enumerate(keys):
            for col, track in enumerate(track_ids):
                cross[row, col] = not self._same_track(pid, str(track), timestamp_ms)
        assignable = costs
        if self.reconnection == "review":
            assignable = np.where(cross, 100.0, costs)
        if keys and len(continued) < len(boxes):
            rows, cols = linear_sum_assignment(assignable)
            for row, col in zip(rows, cols):
                value = assignable[row, col]
                if value >= 0.72 or col in continued:
                    continue
                # Ambiguity is assessed in both directions, including simultaneous
                # same-uniform people. A binding bonus alone cannot resolve a tie.
                alternatives = np.delete(costs[:, col], row)
                rivals = np.delete(costs[row, :], col)
                second = min(
                    float(alternatives.min()) if alternatives.size else 100.0,
                    float(rivals.min()) if rivals.size else 100.0,
                )
                player = self.players[keys[row]]
                gap = timestamp_ms - player["last_ms"]
                bound = self.bindings.get(str(track_ids[col])) == keys[row]
                if second - value < self.margin:
                    if not (
                        bound
                        and player.get("unresolved")
                        and gap <= 500
                        and value < 0.2
                    ):
                        continue
                if gap > self.short_gap_ms:
                    # Long-gap matches must also be unique by appearance, not just position.
                    appearance = appearance_distance(
                        player["descriptor"], descriptors[col]
                    )
                    other_distances = [
                        appearance_distance(
                            self.players[k]["descriptor"], descriptors[col]
                        )
                        for k in keys
                        if k != keys[row]
                    ]
                    if any(
                        d is not None and d - appearance < 0.08 for d in other_distances
                    ):
                        continue
                proposals[int(col)] = (
                    keys[row],
                    value,
                    "continued"
                    if bound and gap <= self.short_gap_ms
                    else "reidentified",
                )
        results = []
        for col, (track, box, descriptor) in enumerate(
            zip(track_ids, boxes, descriptors)
        ):
            track = str(track)
            assignment = proposals.get(col)
            plausible = [keys[i] for i in range(len(keys)) if costs[i, col] < 0.72]
            withheld = []
            if assignment:
                pid, cost, status = assignment
                if status == "reidentified":
                    self.reconnections += 1
                # A continued track may lack a motion/appearance cost (e.g. after a
                # turn); its confidence is then unknown rather than invented.
                confidence = None if cost is None else round(1 - cost, 4)
            else:
                pid = self.game_id + "-player-" + str(self.next_id)
                label = "Player " + str(self.next_id)
                self.next_id += 1
                status = "ambiguous" if plausible else "new"
                if plausible:
                    self.ambiguous_observations += 1
                withheld = [
                    keys[i]
                    for i in range(len(keys))
                    if costs[i, col] < 0.72 and cross[i, col]
                ]
                confidence = None
                self.players[pid] = dict(
                    label=label,
                    descriptor=None,
                    box=np.asarray(box, dtype=float),
                    point=footpoint(box),
                    velocity=np.zeros(2),
                    last_ms=timestamp_ms,
                    observations=0,
                    unresolved=bool(plausible),
                )
            player = self.players[pid]
            old_pid = self.bindings.get(track)
            if old_pid != pid:
                self.segments[track] = self.segments.get(track, 0) + 1
            segment = self.segments[track]
            tracklet = track if segment == 1 else track + "~" + str(segment)
            dt = timestamp_ms - player["last_ms"]
            point = footpoint(box)
            if dt > 0 and dt <= self.short_gap_ms:
                measured = (point - player["point"]) / dt
                player["velocity"] = 0.5 * player["velocity"] + 0.5 * measured
            elif dt > self.short_gap_ms:
                player["velocity"] = np.zeros(2)
            if descriptor is not None:
                if player["descriptor"] is None:
                    player["descriptor"] = descriptor.copy()
                else:
                    blended = 0.9 * player["descriptor"] + 0.1 * descriptor
                    player["descriptor"] = blended / max(1e-8, np.linalg.norm(blended))
            player.update(
                point=point,
                box=np.asarray(box, dtype=float),
                last_ms=timestamp_ms,
                observations=player["observations"] + 1,
                track=track,
            )
            self.bindings[track] = pid
            self._player_times.setdefault(pid, set()).add(timestamp_ms)
            self._tracklet_times.setdefault(tracklet, set()).add(timestamp_ms)
            self._player_tracklets.setdefault(pid, set()).add(tracklet)
            if withheld and self.reconnection == "review":
                self._suggest(
                    tracklet, track, pid, withheld, costs, keys, col, timestamp_ms
                )
            results.append(
                dict(
                    player_id=pid,
                    player_label=player["label"],
                    raw_track_id=track,
                    track_id=tracklet,
                    identity_source="automatic",
                    identity_status=status,
                    association_confidence=confidence,
                    identity_confirmed=False,
                    identity_candidates=plausible if status == "ambiguous" else [],
                )
            )
        return results

    def summary(self):
        return dict(
            method="track-continuity-v2"
            if self.continuity
            else "uniform-colour-motion-v1",
            players=len(self.players),
            continuity_splits=self.continuity_splits,
            reconnection_policy=self.reconnection,
            reconnections=self.reconnections,
            withheld_reconnections=self.withheld_reconnections,
            ambiguous_observations=self.ambiguous_observations,
            identity_confirmed=False,
        )


def _tracklets(samples, key, aspect):
    grouped = {}
    for sample in samples:
        grouped.setdefault(str(sample[key]), []).append(sample)
    tracklets = {}
    for name, items in grouped.items():
        items.sort(key=lambda s: s["timestamp_ms"])
        feet = [
            np.array(
                [
                    (s["bbox"][0] + s["bbox"][2] / 2) * aspect,
                    s["bbox"][1] + s["bbox"][3],
                ]
            )
            for s in items
        ]
        velocity = np.zeros(2)
        if len(items) >= 2:
            dt = items[-1]["timestamp_ms"] - items[-2]["timestamp_ms"]
            if dt > 0:
                velocity = (feet[-1] - feet[-2]) / dt
        tracklets[name] = dict(
            start=items[0]["timestamp_ms"],
            end=items[-1]["timestamp_ms"],
            first=feet[0],
            last=feet[-1],
            velocity=velocity,
            first_height=items[0]["bbox"][3],
            last_height=items[-1]["bbox"][3],
            observations=len(items),
        )
    return tracklets


def propose_tracklet_merges(
    samples,
    descriptors=None,
    teams=None,
    key="track_id",
    aspect=16 / 9,
    max_gap_ms=3000,
    appearance_gate=0.30,
    margin=0.10,
):
    """Suggest which tracklets may be the same person, with evidence, for review.

    ``samples`` use the pipeline format (normalized ``bbox`` [x, y, w, h],
    ``timestamp_ms``, ``track_id``). Optional ``descriptors`` map tracklet IDs to a
    representative uniform descriptor, and ``teams`` map them to TeamGrouper
    assignments. A merge is only considered when the later tracklet starts after
    the earlier one ends, motion and box size are consistent, appearance (when
    known) is close and anonymous team labels do not conflict. Pairs are made
    one-to-one; near ties are reported as ``ambiguous`` with alternatives.
    Proposals are never confirmed identities and always require review.
    """
    tracklets = _tracklets(samples, key, aspect)
    names = sorted(tracklets, key=lambda n: (tracklets[n]["start"], n))
    descriptors = {str(k): v for k, v in (descriptors or {}).items()}
    teams = {str(k): v for k, v in (teams or {}).items()}
    evidence = {}
    costs = np.full((len(names), len(names)), 100.0)
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            A, B = tracklets[a], tracklets[b]
            gap = B["start"] - A["end"]
            if a == b or gap <= 0 or gap > max_gap_ms:
                continue
            size = B["first_height"] / max(1e-6, A["last_height"])
            if not 0.6 <= size <= 1.67:
                continue
            team_a = (teams.get(a) or {}).get("team", "unknown")
            team_b = (teams.get(b) or {}).get("team", "unknown")
            if "unknown" not in (team_a, team_b) and team_a != team_b:
                continue
            scale = (A["last_height"] + B["first_height"]) / 2
            predicted = A["last"] + A["velocity"] * min(gap, 750)
            displacement = float(np.linalg.norm(B["first"] - predicted)) / max(
                scale, 1e-6
            )
            radius = 1.0 + 1.5 * min(gap / 1000, 2)
            if displacement > radius:
                continue
            distance = appearance_distance(
                _unit_or_none(descriptors.get(a)), _unit_or_none(descriptors.get(b))
            )
            if distance is not None and distance > appearance_gate:
                continue
            appearance_term = 0.5 if distance is None else distance / appearance_gate
            costs[i, j] = 0.6 * displacement / radius + 0.4 * appearance_term
            evidence[(i, j)] = dict(
                gap_ms=round(gap),
                displacement_heights=round(displacement, 3),
                radius_heights=round(radius, 3),
                size_ratio=round(size, 3),
                appearance_distance=None if distance is None else round(distance, 4),
                team_source=team_a,
                team_target=team_b,
                source_observations=A["observations"],
                target_observations=B["observations"],
            )
    proposals = []
    if evidence:
        rows, cols = linear_sum_assignment(costs)
        for i, j in zip(rows, cols):
            value = costs[i, j]
            if value >= 100:
                continue
            alternatives = [
                names[r]
                for r in range(len(names))
                if r != i and costs[r, j] - value < margin
            ] + [
                names[c]
                for c in range(len(names))
                if c != j and costs[i, c] - value < margin
            ]
            proposals.append(
                dict(
                    source_track_id=names[i],
                    target_track_id=names[j],
                    status="ambiguous" if alternatives else "proposed",
                    score=round(float(value), 4),
                    alternatives=sorted(set(alternatives)),
                    evidence=evidence[(i, j)],
                    method="tracklet-merge-proposal-v1",
                    identity_confirmed=False,
                    requires_review=True,
                )
            )
    proposals.sort(
        key=lambda p: (tracklets[p["target_track_id"]]["start"], p["target_track_id"])
    )
    return proposals


def _unit_or_none(vector):
    if vector is None:
        return None
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None
