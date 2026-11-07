import time
from backend.app import main
from backend.tests.test_local import submit
from workers.video.highlights import build_highlights, summarize, clear_clips, overlap


def samples(track="1", moving=True):
    return [
        dict(
            game_id="test",
            track_id=track,
            timestamp_ms=t,
            bbox=[0.1 + (0.05 * i if moving else 0), 0.2, 0.1, 0.3],
            keypoints=[],
            confidence=0.95,
        )
        for i, t in enumerate(range(0, 2000, 250))
    ]


def seed_game(client, video):
    game = submit(client, video).json()
    id = "analysis1"
    main.put(
        "job",
        id,
        dict(
            job_id=id,
            game_id=game["game_id"],
            status="succeeded",
            phase="complete",
            attempts=1,
            error=None,
            artifacts=[],
            options={"stride": 5},
        ),
    )
    main.put(
        "batch",
        "batch1",
        dict(job_id=id, game_id=game["game_id"], items=samples() + samples("2", False)),
    )
    return game, id, "/api/games/" + game["game_id"] + "/highlights"


def test_visual_scores_bounds_and_evidence():
    bundle = build_highlights(samples() + samples("2", False), 2000, 20, 5, "analysis1")
    assert bundle["candidates"]
    assert all(
        0 <= c["start_ms"] < c["end_ms"] <= 2000 and 0 <= c["score"] <= 100
        for c in bundle["candidates"]
    )
    assert all(
        c["event_type"] == "visual_activity" and c["review_status"] == "pending"
        for c in bundle["candidates"]
    )
    inactive = next(p for p in bundle["players"] if p["track_ids"] == ["2"])
    assert inactive["highlight_score"] is None and inactive["candidate_count"] == 0
    assert bundle["candidates"][0]["components"]["movement"] > 0


def test_selection_budget_overlap_and_rejection():
    source = samples() * 1
    # Long tracks give several overlapping windows; two different actors get first picks.
    long = []
    for track in ["1", "2"]:
        for i in range(48):
            long.append(
                dict(
                    source[i % 8],
                    track_id=track,
                    timestamp_ms=i * 250,
                    bbox=[0.1 + (i % 8) * 0.05, 0.2, 0.1, 0.3],
                )
            )
    bundle = build_highlights(long, 12000, 20, 5, "test", max_clips=2, top_per_player=2)
    selected = [c for c in bundle["candidates"] if c["selected"]]
    assert len(selected) == 2 and len({c["player_id"] for c in selected}) == 2
    for c in bundle["candidates"]:
        c["review"] = {"decision": "rejected", "event_type": "visual_activity"}
    summarize(bundle)
    assert not any(c["selected"] for c in bundle["candidates"])
    assert all(p["highlight_score"] is None for p in bundle["players"])
    assert (
        overlap({"start_ms": 0, "end_ms": 5000}, {"start_ms": 1000, "end_ms": 6000})
        == 0.8
    )


def test_confirmed_outcome_and_grouped_identity():
    bundle = build_highlights(samples() + samples("2"), 2000, 20, 5, "job")
    c = bundle["candidates"][0]
    original = c["automatic_score"]
    c["review"] = {"decision": "approved", "event_type": "made_shot"}
    summarize(
        clear_clips(bundle),
        groups=[
            dict(
                player_id="jersey12",
                label="Jersey 12",
                track_ids=["1", "2"],
                confirmed=True,
            )
        ],
    )
    assert len(bundle["players"]) == 1 and bundle["players"][0]["identity_confirmed"]
    assert (
        bundle["players"][0]["observed_ms"] == 2000
    )  # overlapping fragments aren't double-counted
    assert c["score"] == round(90 * 0.8 + original * 0.2, 2)
    assert c["origin"] == "reviewer_confirmed" and c["clip_uri"] is None
    assert bundle["status"] == "not_generated"


def test_review_mapping_generation_and_persistence(client, video):
    game, id, url = seed_game(client, video)
    bundle = client.get(url).json()
    candidate = bundle["candidates"][0]
    assert "track_stats" not in bundle and bundle["status"] == "not_generated"
    assert (
        client.put(
            url + "/candidates/" + candidate["candidate_id"] + "/review",
            json={"decision": "pending", "event_type": "made_shot"},
        ).status_code
        == 422
    )
    approved = client.put(
        url + "/candidates/" + candidate["candidate_id"] + "/review",
        json={"decision": "approved", "event_type": "made_shot"},
    )
    assert (
        approved.status_code == 200
        and approved.json()["candidates"][0]["origin"] == "reviewer_confirmed"
    )
    group = dict(
        player_id="jersey12", label="Jersey 12", track_ids=["1"], confirmed=True
    )
    assert (
        client.put(
            url + "/players", json={"players": [dict(group, track_ids=["unknown"])]}
        ).status_code
        == 422
    )
    assert (
        client.put(
            url + "/players", json={"players": [dict(group, confirmed=False)]}
        ).status_code
        == 422
    )
    assert (
        client.put(
            url + "/players", json={"players": [group, dict(group, player_id="other")]}
        ).status_code
        == 422
    )
    assert client.put(url + "/players", json={"players": [group]}).status_code == 200
    headers = {"Idempotency-Key": "generate-first"}
    response = client.post(
        url, json={"max_clips": 1, "top_per_player": 1}, headers=headers
    )
    assert response.status_code == 202
    for _ in range(100):
        result = client.get(url).json()
        if result["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert result["status"] == "succeeded", result
    clip = next(c for c in result["candidates"] if c["clip_uri"])
    assert clip["qa_status"] == "passed" and clip["review_status"] == "approved"
    assert (
        client.get(clip["clip_uri"], headers={"Range": "bytes=0-99"}).status_code == 206
    )
    generation = result["generation_id"]
    assert (
        client.post(
            url, json={"max_clips": 1, "top_per_player": 1}, headers=headers
        ).json()["generation_id"]
        == generation
    )
    assert client.post(url, json={"max_clips": 2}, headers=headers).status_code == 409
    rejected = client.put(
        url + "/candidates/" + candidate["candidate_id"] + "/review",
        json={"decision": "rejected", "event_type": "visual_activity"},
    ).json()
    assert rejected["status"] == "not_generated" and all(
        c["clip_uri"] is None for c in rejected["candidates"]
    )
    assert client.get("/api/games/" + game["game_id"] + "/clips").json()["items"] == []
    assert main.get("highlights", id)["candidates"][0]["review_status"] == "rejected"
    client.put(
        url + "/candidates/" + candidate["candidate_id"] + "/review",
        json={"decision": "pending"},
    )
    client.post(
        url,
        json={"max_clips": 1, "top_per_player": 1},
        headers={"Idempotency-Key": "second-generation"},
    )
    for _ in range(100):
        regenerated = client.get(url).json()
        if regenerated["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert regenerated["status"] == "succeeded"
    current = next(c for c in regenerated["candidates"] if c["clip_uri"])
    assert current["clip_uri"] != clip["clip_uri"]
    event = client.get("/api/games/" + game["game_id"] + "/events").json()["items"][0]
    assert event["clip_uri"] == current["clip_uri"]


def test_busy_and_stale_analysis_boundaries(client, video):
    game, id, url = seed_game(client, video)
    bundle = build_highlights(samples(), 2000, 20, 5, id)
    bundle["status"] = "running"
    main.put("highlights", id, bundle)
    assert client.put(url + "/players", json={"players": []}).status_code == 409
    assert (
        client.post(
            "/api/games/" + game["game_id"] + "/analyze",
            json={},
            headers={"Idempotency-Key": "new-analysis"},
        ).status_code
        == 409
    )
    bundle["status"] = "succeeded"
    main.put("highlights", id, bundle)
    main.put("job", "analysis2", dict(main.get("job", id), job_id="analysis2"))
    assert (
        client.put(
            url + "/candidates/" + bundle["candidates"][0]["candidate_id"] + "/review",
            json={"decision": "approved"},
        ).status_code
        == 404
    )


def test_target_marker_preserves_decodable_source_clip(video, tmp_path):
    import cv2
    from workers.render.media import render_clip, verify_clip

    target = render_clip(
        video,
        0,
        1000,
        tmp_path / "marked.mp4",
        overlays=[dict(timestamp_ms=0, end_ms=500, bbox=[0.1, 0.2, 0.2, 0.3])],
    )
    qa = verify_clip(video, target, 0, 1000)
    assert qa["qa_status"] == "passed"
    cap = cv2.VideoCapture(str(target))
    ok, frame = cap.read()
    cap.release()
    assert ok
    border = frame[36:39, 33:94]
    yellow = (border[:, :, 0] < 120) & (border[:, :, 1] > 170) & (border[:, :, 2] > 170)
    assert yellow.mean() > 0.7
