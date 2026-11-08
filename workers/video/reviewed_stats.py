"""Basketball counts backed by reviewed source events; no composite player rating."""

from collections import Counter

BASKETBALL_EVENTS = {
    "pass",
    "possession",
    "made_shot",
    "missed_shot",
    "made_free_throw",
    "missed_free_throw",
    "assist",
    "rebound",
    "steal",
    "block",
    "turnover",
}


def attach_basketball_stats(bundle):
    """Recompute from the review ledger, never from selected clips or pose confidence."""
    for player in bundle.get("players", []):
        candidates = [
            c for c in bundle["candidates"] if c["player_id"] == player["player_id"]
        ]
        accepted = []
        held = []
        for candidate in candidates:
            review = candidate.get("review", {})
            if (
                review.get("decision") != "approved"
                or review.get("event_type") not in BASKETBALL_EVENTS
            ):
                continue
            if (
                not player.get("identity_confirmed")
                and len(player.get("track_ids", [])) > 1
            ):
                held.append(
                    dict(
                        candidate_id=candidate["candidate_id"],
                        reason="Correct and confirm this uncertain player group before attributing events",
                    )
                )
                continue
            if not (player.get("identity_confirmed") or review.get("actor_confirmed")):
                held.append(
                    dict(
                        candidate_id=candidate["candidate_id"],
                        reason="Confirm which player performed this play",
                    )
                )
                continue
            timestamp = review.get("event_time_ms")
            if (
                timestamp is None
                or not candidate["start_ms"] <= timestamp < candidate["end_ms"]
            ):
                held.append(
                    dict(
                        candidate_id=candidate["candidate_id"],
                        reason="Confirm the event time in the source video",
                    )
                )
                continue
            if review.get("event_type") == "possession" and not (
                isinstance(review.get("possession_end_ms"), int)
                and timestamp < review["possession_end_ms"] <= candidate["end_ms"]
            ):
                held.append(
                    dict(
                        candidate_id=candidate["candidate_id"],
                        reason="Confirm the possession end time inside the source range",
                    )
                )
                continue
            accepted.append((timestamp, candidate))
        evidence = []
        seen = set()
        for timestamp, candidate in sorted(
            accepted, key=lambda v: (v[0], v[1]["candidate_id"])
        ):
            review = candidate["review"]
            event = review["event_type"]
            key = (event, timestamp)
            if key in seen:
                held.append(
                    dict(
                        candidate_id=candidate["candidate_id"],
                        reason="Duplicate event type and source time; counted once",
                    )
                )
                continue
            # Close, same-type reviews may be duplicate windows of one action. Ask rather than guess.
            close = any(
                e["event_type"] == event and abs(e["event_time_ms"] - timestamp) < 1500
                for e in evidence
            )
            if (
                close
                and event != "possession"
                and not review.get("distinct_event_confirmed")
            ):
                held.append(
                    dict(
                        candidate_id=candidate["candidate_id"],
                        reason="Possible duplicate nearby play; confirm it is distinct",
                    )
                )
                continue
            seen.add(key)
            points = (
                1
                if event == "made_free_throw"
                else review.get("points")
                if event == "made_shot"
                else 0
            )
            evidence.append(
                dict(
                    candidate_id=candidate["candidate_id"],
                    event_type=event,
                    event_time_ms=timestamp,
                    possession_end_ms=review.get("possession_end_ms")
                    if event == "possession"
                    else None,
                    points=points,
                    track_id=candidate["track_id"],
                    clip_uri=candidate.get("clip_uri"),
                )
            )
        counts = Counter(e["event_type"] for e in evidence)
        intervals = sorted(
            (e["event_time_ms"], e["possession_end_ms"])
            for e in evidence
            if e["event_type"] == "possession"
        )
        possession_ms = 0
        previous_end = -1
        for start, end in intervals:
            possession_ms += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
        unvalued = sum(
            e["event_type"] == "made_shot" and e["points"] is None for e in evidence
        )
        player["basketball_stats"] = dict(
            scope="reviewed_events",
            has_evidence=bool(evidence),
            reviewed_events_count=len(evidence),
            passes=counts["pass"] if counts["pass"] else None,
            possession_ms=possession_ms if intervals else None,
            field_goals_made=counts["made_shot"],
            field_goals_attempted=counts["made_shot"] + counts["missed_shot"],
            free_throws_made=counts["made_free_throw"],
            free_throws_attempted=counts["made_free_throw"]
            + counts["missed_free_throw"],
            points=None if unvalued else sum(e["points"] or 0 for e in evidence),
            unvalued_makes=unvalued,
            assists=counts["assist"],
            rebounds=counts["rebound"],
            steals=counts["steal"],
            blocks=counts["block"],
            turnovers=counts["turnover"],
            pending_candidates=sum(
                c.get("review_status", "pending") == "pending" for c in candidates
            ),
            held_events_count=len(held),
            evidence=evidence,
            held_events=held,
        )
    return bundle
