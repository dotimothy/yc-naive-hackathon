"""Attribution gates: uncertain fragments cannot become a player's stat line by accident."""

from backend.app import main
from backend.tests.test_highlights import seed_game
from workers.video.highlights import build_highlights, summarize


def fragments(overlap=False):
    records = []
    for track, start in [("1", 0), ("2", 0 if overlap else 2500)]:
        for i in range(8):
            records.append(
                dict(
                    track_id=track,
                    player_id="auto-player",
                    player_label="Player 1",
                    identity_status="ambiguous",
                    association_confidence=None,
                    timestamp_ms=start + i * 250,
                    bbox=[0.1 + i * 0.05, 0.2, 0.1, 0.3],
                    keypoints=[],
                    confidence=0.95,
                )
            )
    return records


def approve(candidate):
    candidate["review"] = dict(
        decision="approved",
        event_type="made_shot",
        event_time_ms=candidate["action_start_ms"],
        points=2,
        actor_confirmed=True,
    )


def test_event_actor_confirmation_does_not_confirm_automatic_fragment_merges():
    bundle = build_highlights(fragments(), 5000, 20, 5, "job")
    assert bundle["players"][0]["association_status"] == "needs_review"
    approve(bundle["candidates"][0])
    summarize(bundle)
    stats = bundle["players"][0]["basketball_stats"]
    assert not stats["has_evidence"]
    assert stats["held_events_count"] == 1
    assert "group" in stats["held_events"][0]["reason"].lower()


def test_single_fragment_actor_can_be_reviewed_without_confirming_sibling_fragments():
    records = [record for record in fragments() if record["track_id"] == "1"]
    bundle = build_highlights(records, 5000, 20, 5, "job")
    approve(bundle["candidates"][0])
    summarize(bundle)
    assert bundle["players"][0]["basketball_stats"]["points"] == 2


def test_confirmed_group_allows_explicitly_reviewed_fragment_events():
    bundle = build_highlights(fragments(), 5000, 20, 5, "job")
    approve(bundle["candidates"][0])
    summarize(
        bundle,
        groups=[
            dict(
                player_id="corrected",
                label="Corrected",
                track_ids=["1", "2"],
                confirmed=True,
            )
        ],
    )
    # Group correction invalidates old actor approval; the reviewer can approve it again.
    approve(bundle["candidates"][0])
    summarize(bundle)
    assert bundle["players"][0]["basketball_stats"]["points"] == 2


def test_manual_merge_rejects_tracks_visible_at_the_same_source_time(client, video):
    _, job_id, path = seed_game(client, video)
    response = client.put(
        path + "/players",
        json={
            "players": [
                dict(
                    player_id="merged",
                    label="Merged",
                    track_ids=["1", "2"],
                    confirmed=True,
                )
            ]
        },
    )
    assert response.status_code == 422
    assert any(
        word in response.json()["detail"].lower()
        for word in ["simultaneous", "concurrent", "overlap"]
    )
    # The invalid merge never changes the saved bundle.
    assert not main.all_records("highlights")


def test_regrouping_reviewed_event_requires_reconfirmation(client, video):
    _, _, path = seed_game(client, video)
    group = dict(
        player_id="original", label="Original", track_ids=["1"], confirmed=True
    )
    assert client.put(path + "/players", json={"players": [group]}).status_code == 200
    candidate = next(
        c for c in client.get(path).json()["candidates"] if c["track_id"] == "1"
    )
    approve(candidate)
    review_route = path + "/candidates/" + candidate["candidate_id"] + "/review"
    assert client.put(review_route, json=candidate["review"]).status_code == 200
    corrected = dict(
        player_id="corrected", label="Corrected", track_ids=["1"], confirmed=True
    )
    response = client.put(path + "/players", json={"players": [corrected]})
    assert response.status_code == 200
    changed = next(
        c
        for c in response.json()["candidates"]
        if c["candidate_id"] == candidate["candidate_id"]
    )
    assert changed["review_status"] == "pending"
    assert not changed["review"].get("actor_confirmed", False)
    assert not any(
        player["basketball_stats"]["has_evidence"]
        for player in response.json()["players"]
    )

