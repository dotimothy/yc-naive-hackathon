from workers.video.reviewed_stats import attach_basketball_stats
from backend.tests.test_highlights import seed_game


def ledger(events, confirmed=False):
    candidates = []
    for i, event in enumerate(events):
        review = dict(
            decision="approved",
            event_type=event,
            event_time_ms=i * 2000,
            actor_confirmed=True,
            points=2,
        )
        candidates.append(
            dict(
                candidate_id=str(i),
                player_id="p",
                track_id="1",
                start_ms=0,
                end_ms=30000,
                review=review,
                review_status="approved",
                selected=False,
            )
        )
    return dict(
        players=[dict(player_id="p", identity_confirmed=confirmed)],
        candidates=candidates,
    )


def stats(bundle):
    return attach_basketball_stats(bundle)["players"][0]["basketball_stats"]


def test_reviewed_counts_independent_of_clip_budget():
    b = ledger(
        ["made_shot"] * 4
        + ["missed_shot"] * 3
        + ["assist", "rebound", "turnover", "made_free_throw"]
    )
    s = stats(b)
    assert (s["field_goals_made"], s["field_goals_attempted"], s["points"]) == (4, 7, 9)
    assert s["assists"] == s["rebounds"] == s["turnovers"] == 1
    assert len(s["evidence"]) == 11
    b["candidates"][0]["selected"] = True
    assert stats(b) == s


def test_missing_evidence_actor_time_and_shot_value():
    b = ledger(["made_shot", "assist", "rebound"])
    b["candidates"][0]["review"]["points"] = None
    b["candidates"][1]["review"].pop("actor_confirmed")
    b["candidates"][2]["review"].pop("event_time_ms")
    s = stats(b)
    assert s["points"] is None and s["field_goals_made"] == 1
    assert s["held_events_count"] == 2
    b["candidates"][0]["review"]["decision"] = "pending"
    assert not stats(b)["has_evidence"]


def test_duplicates_require_distinct_confirmation():
    b = ledger(["rebound"] * 3)
    for i, c in enumerate(b["candidates"]):
        c["review"]["event_time_ms"] = [100, 100, 500][i]
    assert stats(b)["rebounds"] == 1
    b["candidates"][2]["review"]["distinct_event_confirmed"] = True
    assert stats(b)["rebounds"] == 2
    b["candidates"][1]["review"]["distinct_event_confirmed"] = True
    assert stats(b)["rebounds"] == 2


def test_api_review_validates_source_time_and_persists_evidence(client, video):
    _, _, path = seed_game(client, video)
    bundle = client.get(path).json()
    c = bundle["candidates"][0]
    route = path + "/candidates/" + c["candidate_id"] + "/review"
    body = dict(
        decision="approved",
        event_type="made_shot",
        event_time_ms=c["start_ms"],
        points=2,
        actor_confirmed=True,
    )
    assert (
        client.put(route, json={**body, "event_time_ms": c["end_ms"]}).status_code
        == 422
    )
    response = client.put(route, json=body)
    assert response.status_code == 200, response.text
    s = next(
        p["basketball_stats"]
        for p in response.json()["players"]
        if p["basketball_stats"]["has_evidence"]
    )
    assert s["points"] == 2 and s["field_goals_attempted"] == 1
    assert client.get(path).json()["players"] == response.json()["players"]


def test_short_clip_possession_union_and_pass_evidence():
    b = ledger(["possession", "possession", "pass"])
    b["candidates"][0]["review"].update(event_time_ms=0, possession_end_ms=3000)
    b["candidates"][1]["review"].update(event_time_ms=1000, possession_end_ms=5000)
    s = stats(b)
    assert s["possession_ms"] == 5000  # Overlapping review windows count once.
    assert s["passes"] == 1
    b["candidates"][0]["review"]["actor_confirmed"] = False
    assert stats(b)["possession_ms"] == 4000
    b["candidates"][1]["review"]["possession_end_ms"] = 40000
    assert stats(b)["possession_ms"] is None
    assert stats(ledger(["rebound"]))["passes"] is None


def test_possession_review_requires_valid_interval(client, video):
    _, _, path = seed_game(client, video)
    c = client.get(path).json()["candidates"][0]
    route = path + "/candidates/" + c["candidate_id"] + "/review"
    body = dict(
        decision="approved",
        event_type="possession",
        actor_confirmed=True,
        event_time_ms=c["start_ms"],
        possession_end_ms=c["end_ms"],
    )
    assert (
        client.put(route, json={**body, "possession_end_ms": c["start_ms"]}).status_code
        == 422
    )
    assert (
        client.put(
            route, json={**body, "possession_end_ms": c["end_ms"] + 1}
        ).status_code
        == 422
    )
    result = client.put(route, json=body)
    assert result.status_code == 200
    assert any(
        p["basketball_stats"]["possession_ms"] == c["end_ms"] - c["start_ms"]
        for p in result.json()["players"]
    )
    result = client.put(
        route,
        json=dict(
            decision="approved",
            event_type="pass",
            actor_confirmed=True,
            event_time_ms=c["start_ms"],
        ),
    )
    assert result.status_code == 200
    assert any(p["basketball_stats"]["passes"] == 1 for p in result.json()["players"])
