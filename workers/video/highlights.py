"""Explainable highlight selection. Visual activity is not basketball skill/statistics."""

from collections import defaultdict
import math
import numpy as np
from workers.render.media import render_clip, verify_clip

EVENT_WEIGHTS = {"made_shot": 90, "assist": 85, "rebound": 70, "steal": 85, "block": 85}
SCORING_VERSION = "visual-v1"


def overlap(a, b):
    intersection = max(
        0, min(a["end_ms"], b["end_ms"]) - max(a["start_ms"], b["start_ms"])
    )
    return intersection / max(
        1, min(a["end_ms"] - a["start_ms"], b["end_ms"] - b["start_ms"])
    )


def visual_components(samples, interval_ms):
    motion = []
    arms = []
    for previous, current in zip(samples, samples[1:]):
        dt = (current["timestamp_ms"] - previous["timestamp_ms"]) / 1000
        if dt <= 0:
            continue
        a, b = previous["bbox"], current["bbox"]
        # Image-relative displacement in bounding-box heights, not calibrated speed.
        dx = (b[0] + b[2] / 2) - (a[0] + a[2] / 2)
        dy = (b[1] + b[3]) - (a[1] + a[3])
        motion.append(
            min(1, math.hypot(dx, dy) / max(0.01, (a[3] + b[3]) / 2) / dt / 2.5)
        )
    for sample in samples:
        points = {p["name"]: p for p in sample.get("keypoints", [])}
        raised = False
        for side in ["left", "right"]:
            wrist, shoulder = (
                points.get(side + "_wrist"),
                points.get(side + "_shoulder"),
            )
            if (
                wrist
                and shoulder
                and wrist.get("visibility", 0) > 0
                and shoulder.get("visibility", 0) > 0
            ):
                raised |= wrist["y"] < shoulder["y"] - 0.1 * sample["bbox"][3]
        arms.append(int(raised))
    expected = max(
        1,
        round((samples[-1]["timestamp_ms"] - samples[0]["timestamp_ms"]) / interval_ms)
        + 1,
    )
    return {
        "movement": round(35 * float(np.percentile(motion, 75)) if motion else 0, 2),
        "raised_arm_pose": round(20 * float(np.mean(arms)), 2),
        "detection_quality": round(
            25 * float(np.mean([s["confidence"] for s in samples])), 2
        ),
        "track_coverage": round(20 * min(1, len(samples) / expected), 2),
    }


def summarize(bundle, groups=None, reviews=None):
    groups = groups if groups is not None else bundle.get("roster_groups", [])
    reviews = reviews or {}
    manual = {t: g for g in groups for t in g["track_ids"]}
    automatic = {
        t: g for g in bundle.get("automatic_players", []) for t in g["track_ids"]
    }
    assignments = {**automatic, **manual}
    for candidate in bundle["candidates"]:
        group = assignments.get(candidate["track_id"])
        candidate["player_id"] = (
            group["player_id"] if group else "track-" + candidate["track_id"]
        )
        review = reviews.get(candidate["candidate_id"], candidate.get("review", {}))
        candidate["review"] = review
        candidate["review_status"] = review.get("decision", "pending")
        candidate["event_type"] = (
            review.get("event_type", "visual_activity")
            if review.get("decision") == "approved"
            else "visual_activity"
        )
        candidate["origin"] = (
            "reviewer_confirmed"
            if review.get("decision") == "approved"
            else "visual_heuristic"
        )
        candidate["score"] = (
            round(
                0.8 * EVENT_WEIGHTS[candidate["event_type"]]
                + 0.2 * candidate["automatic_score"],
                2,
            )
            if candidate["event_type"] in EVENT_WEIGHTS
            else candidate["automatic_score"]
        )
        candidate["selected"] = False
    # Rank clips within each player, not players by merit. Rejects never enter selection.
    pools = defaultdict(list)
    for candidate in sorted(
        bundle["candidates"], key=lambda c: (-c["score"], c["candidate_id"])
    ):
        if candidate["review_status"] == "rejected":
            continue
        selected = pools[candidate["player_id"]]
        if len(selected) < bundle["top_per_player"] and all(
            overlap(candidate, other) < 0.5 for other in selected
        ):
            selected.append(candidate)
    # Round-robin grants one highlight per group before spending budget on second picks.
    budget = bundle["max_clips"]
    for rank in range(bundle["top_per_player"]):
        round_candidates = sorted(
            [v[rank] for v in pools.values() if len(v) > rank],
            key=lambda c: (-c["score"], c["candidate_id"]),
        )
        for c in round_candidates:
            if budget <= 0:
                break
            c["selected"] = True
            budget -= 1
    track_stats = bundle.get("track_stats", {})
    players = {}
    for track_id, stats in track_stats.items():
        group = assignments.get(track_id)
        id = group["player_id"] if group else "track-" + track_id
        player = players.setdefault(
            id,
            dict(
                player_id=id,
                label=group["label"] if group else "Track " + track_id,
                track_ids=[],
                identity_confirmed=track_id in manual,
                identity_source="reviewer"
                if track_id in manual
                else "automatic"
                if group
                else "track",
                association_status="confirmed"
                if track_id in manual
                else group.get("association_status", "provisional")
                if group
                else "unlinked",
                association_confidence=group.get("association_confidence")
                if group
                else None,
                highlight_score=None,
                verified_play_count=0,
                candidate_count=0,
                selected_count=0,
                observed_ms=0,
            ),
        )
        player["track_ids"].append(track_id)
        if track_id not in manual:
            player["identity_confirmed"] = False
        # Union times below prevents simultaneous fragments from inflating observed time.
    for player in players.values():
        times = set(
            t for track in player["track_ids"] for t in track_stats[track]["timestamps"]
        )
        player["observed_ms"] = min(
            bundle["duration_ms"], round(len(times) * bundle["sample_interval_ms"])
        )
        candidates = [
            c
            for c in bundle["candidates"]
            if c["player_id"] == player["player_id"]
            and c["review_status"] != "rejected"
        ]
        scores = sorted(
            [c["score"] for c in pools.get(player["player_id"], [])], reverse=True
        )[:3]
        player["highlight_score"] = round(float(np.mean(scores)), 2) if scores else None
        player["candidate_count"] = len(candidates)
        player["selected_count"] = sum(c["selected"] for c in candidates)
        player["verified_play_count"] = sum(
            c["review_status"] == "approved" and c["event_type"] in EVENT_WEIGHTS
            for c in pools.get(player["player_id"], [])
        )
    bundle["players"] = sorted(
        players.values(), key=lambda p: (not p["identity_confirmed"], p["label"])
    )
    bundle["roster_groups"] = groups
    return bundle


def build_highlights(
    samples, duration_ms, fps, stride, job_id, max_clips=8, top_per_player=2
):
    interval = 1000 * stride / fps
    tracks = defaultdict(dict)
    for sample in samples:
        tracks[str(sample["track_id"])][sample["timestamp_ms"]] = sample
    candidates = []
    stats = {}
    for track_id, by_time in sorted(tracks.items()):
        ordered = [by_time[t] for t in sorted(by_time)]
        stats[track_id] = {"timestamps": sorted(by_time)}
        segments = []
        for sample in ordered:
            if (
                not segments
                or sample["timestamp_ms"] - segments[-1][-1]["timestamp_ms"]
                > interval * 2.5
            ):
                segments.append([])
            segments[-1].append(sample)
        count = 0
        for segment in segments:
            if (
                len(segment) < 4
                or segment[-1]["timestamp_ms"] - segment[0]["timestamp_ms"] < 1000
            ):
                continue
            # Sliding four-second evidence windows. Clip adds context around the actual evidence.
            first, last = segment[0]["timestamp_ms"], segment[-1]["timestamp_ms"]
            starts = list(range(first, max(first + 1, last - 999), 2000))
            for start in starts:
                window = [
                    s for s in segment if start <= s["timestamp_ms"] <= start + 4000
                ]
                if (
                    len(window) < 4
                    or window[-1]["timestamp_ms"] - window[0]["timestamp_ms"] < 1000
                ):
                    continue
                components = visual_components(window, interval)
                if components["movement"] + components["raised_arm_pose"] < 6:
                    continue
                score = round(sum(components.values()), 2)
                reasons = []
                if components["movement"] >= 6:
                    reasons.append("Visible movement in image coordinates")
                if components["raised_arm_pose"] >= 3:
                    reasons.append("Raised-arm pose; basketball outcome unknown")
                reasons.append("Detection quality and observed track coverage")
                a, b = window[0]["timestamp_ms"], window[-1]["timestamp_ms"]
                id = job_id + "-highlight-" + track_id + "-" + str(count)
                count += 1
                candidates.append(
                    dict(
                        candidate_id=id,
                        track_id=track_id,
                        player_id="track-" + track_id,
                        start_ms=max(0, a - 1500),
                        end_ms=min(duration_ms, b + 2000),
                        action_start_ms=a,
                        action_end_ms=b,
                        score=score,
                        automatic_score=score,
                        components=components,
                        reasons=reasons,
                        spotlight=[
                            dict(
                                timestamp_ms=sample["timestamp_ms"],
                                end_ms=min(
                                    b + 2000,
                                    round(sample["timestamp_ms"] + min(interval, 250)),
                                ),
                                bbox=sample["bbox"],
                            )
                            for sample in window
                            if sample["timestamp_ms"] < a + 1500
                        ],
                        review_status="pending",
                        event_type="visual_activity",
                        origin="visual_heuristic",
                        selected=False,
                        clip_id=None,
                        clip_uri=None,
                        qa_status=None,
                    )
                )
    bundle = dict(
        job_id=job_id,
        generation_id=job_id,
        status="not_generated",
        error=None,
        scoring_version=SCORING_VERSION,
        score_kind="highlight_priority",
        duration_ms=duration_ms,
        sample_interval_ms=interval,
        track_stats=stats,
        players=[],
        candidates=candidates,
        roster_groups=[],
        max_clips=max_clips,
        top_per_player=top_per_player,
    )
    attach_player_identities(bundle, samples)
    return summarize(bundle)


def attach_player_identities(bundle, samples):
    """Persistent anonymous IDs group fragments; reviewer mappings always override them."""
    players = {}
    track_owners = {}
    for sample in samples:
        pid = sample.get("player_id")
        if not pid:
            continue
        track = str(sample["track_id"])
        track_owners.setdefault(track, set()).add(pid)
        player = players.setdefault(
            pid,
            dict(
                player_id=pid,
                label=sample.get("player_label", pid),
                track_ids=set(),
                association_status="provisional",
                association_confidence=None,
            ),
        )
        player["track_ids"].add(track)
        if sample.get("identity_status") == "ambiguous":
            player["association_status"] = "needs_review"
        value = sample.get("association_confidence")
        if value is not None:
            previous = player["association_confidence"]
            player["association_confidence"] = (
                min(previous, value) if previous is not None else value
            )
    # Old/malformed evidence with one track claimed by two players is never silently merged.
    for player in players.values():
        player["track_ids"] = sorted(
            t for t in player["track_ids"] if len(track_owners[t]) == 1
        )
    bundle["automatic_players"] = [p for p in players.values() if p["track_ids"]]
    return bundle


def clear_clips(bundle):
    for c in bundle["candidates"]:
        c.update(clip_id=None, clip_uri=None, qa_status=None)
    bundle.update(status="not_generated", error=None)
    return bundle


def add_spotlights(bundle, samples):
    by_track = defaultdict(list)
    for sample in samples:
        by_track[str(sample["track_id"])].append(sample)
    for candidate in bundle["candidates"]:
        a = candidate["action_start_ms"]
        candidate["spotlight"] = [
            dict(
                timestamp_ms=s["timestamp_ms"],
                end_ms=min(
                    candidate["end_ms"],
                    round(s["timestamp_ms"] + min(bundle["sample_interval_ms"], 250)),
                ),
                bbox=s["bbox"],
            )
            for s in sorted(
                by_track[candidate["track_id"]], key=lambda s: s["timestamp_ms"]
            )
            if a <= s["timestamp_ms"] < a + 1500
        ]
    return bundle


def render_highlights(source, output_dir, bundle, game_id, emit):
    bundle["status"] = "running"
    emit("highlights.updated", bundle)
    clips, events = [], []
    for i, candidate in enumerate(c for c in bundle["candidates"] if c["selected"]):
        start, end = candidate["start_ms"], candidate["end_ms"]
        path = render_clip(
            source,
            start,
            end,
            output_dir / ("highlight_%d.mp4" % i),
            overlays=candidate.get("spotlight", []),
        )
        qa = verify_clip(source, path, start, end)
        clip = dict(
            clip_id=bundle["generation_id"] + "-clip-" + str(i),
            event_id=candidate["candidate_id"],
            candidate_id=candidate["candidate_id"],
            game_id=game_id,
            kind="highlight",
            spotlight_applied=bool(candidate.get("spotlight")),
            generation_id=bundle["generation_id"],
            track_id=candidate["track_id"],
            player_id=candidate["player_id"],
            highlight_score=candidate["score"],
            review_status=candidate["review_status"],
            source_start_ms=start,
            source_end_ms=end,
            path=str(path.resolve()),
            identity_verified=next(
                p["identity_confirmed"]
                for p in bundle["players"]
                if p["player_id"] == candidate["player_id"]
            ),
            **qa,
        )
        emit("clip.updated", clip)
        candidate.update(
            clip_id=clip["clip_id"], clip_uri=clip.get("uri"), qa_status=qa["qa_status"]
        )
        event = dict(
            event_id=candidate["candidate_id"],
            game_id=game_id,
            chunk_id=str(i),
            start_ms=start,
            end_ms=end,
            event_type=candidate["event_type"],
            track_id=candidate["track_id"],
            jersey_candidate=None,
            confidence=None,
            evidence_uri=None,
            clip_uri=clip.get("uri"),
            verified=candidate["review_status"] == "approved",
            origin=candidate["origin"],
        )
        emit("event.created", event)
        events.append(event)
        clips.append(clip)
    bundle["status"] = "succeeded"
    emit("highlights.updated", bundle)
    return clips, events
