from backend.app import main
from backend.tests.test_highlights import seed_game


def test_shot_review_changes_eligibility_preserves_source_and_rejects_stale_job(
    client, video
):
    game, id, path = seed_game(client, video)
    root = path.removesuffix("/highlights") + "/shots"
    main.put(
        "shots",
        id,
        dict(
            job_id=id,
            items=[
                dict(
                    shot_id="0",
                    start_ms=0,
                    end_ms=2000,
                    category="uncertain",
                    requires_review=True,
                )
            ],
        ),
    )
    assert client.get(path).json()["candidates"] == []
    assert client.get(root).json()["items"][0]["review_status"] == "pending"
    assert (
        client.put(
            root + "/0/review", json=dict(job_id="old", decision="gameplay")
        ).status_code
        == 409
    )
    result = client.put(root + "/0/review", json=dict(job_id=id, decision="gameplay"))
    assert result.status_code == 200, result.text
    assert result.json()["items"][0]["review_status"] == "confirmed_gameplay"
    candidates = client.get(path).json()["candidates"]
    assert candidates and all(
        c["shot_id"] == "0" and c["end_ms"] <= 2000 for c in candidates
    )
    assert (
        client.put(
            root + "/0/review", json=dict(job_id=id, decision="non_gameplay")
        ).status_code
        == 200
    )
    assert client.get(path).json()["candidates"] == []
    assert (
        client.get(game["source_uri"], headers={"Range": "bytes=0-99"}).status_code
        == 206
    )
    assert (
        client.put(
            root + "/missing/review", json=dict(job_id=id, decision="gameplay")
        ).status_code
        == 404
    )


def test_ball_proposal_cannot_use_an_actor_from_another_camera(client, video, tmp_path):
    game, id, path = seed_game(client, video)
    batch = main.get("batch", "batch1")
    for s in batch["items"]:
        s["shot_id"] = "0" if s["timestamp_ms"] < 1000 else "1"
        if s["shot_id"] == "1":
            s["track_id"] = "s1:" + s["track_id"]
    main.put("batch", "batch1", batch)
    main.put(
        "shots",
        id,
        dict(
            job_id=id,
            items=[
                dict(
                    shot_id=str(i),
                    start_ms=i * 1000,
                    end_ms=(i + 1) * 1000,
                    category="gameplay_candidate",
                )
                for i in range(2)
            ],
        ),
    )
    proposal = dict(
        candidate_id="p",
        shot_id="0",
        start_ms=0,
        end_ms=900,
        approach_ms=500,
        evidence_timestamps=[0, 250, 500],
        evidence_quality=0.8,
    )
    main.put(
        "basketball_job",
        "b",
        dict(
            run_id="b",
            game_id=game["game_id"],
            parent_job_id=id,
            created_at=1,
            status="succeeded",
            identity_revision=0,
            report=dict(shot_candidates=[proposal]),
        ),
    )
    route = path.removesuffix("/highlights") + "/basketball/candidates/p/review"
    assert (
        client.post(route, json=dict(track_id="s1:1", actor_confirmed=True)).status_code
        == 422
    )
    response = client.post(route, json=dict(track_id="1", actor_confirmed=True))
    assert response.status_code == 201, response.text
    imported = next(
        c
        for c in response.json()["candidates"]
        if c["candidate_id"].startswith("ball-")
    )
    assert imported["shot_id"] == "0" and imported["end_ms"] <= 1000
    root = path.removesuffix("/highlights") + "/shots"
    assert (
        client.put(
            root + "/0/review", json=dict(job_id=id, decision="non_gameplay")
        ).status_code
        == 200
    )
    assert (
        client.post(route, json=dict(track_id="1", actor_confirmed=True)).status_code
        == 409
    )


def test_ball_state_and_hoops_are_separate_on_camera_change(tmp_path, monkeypatch):
    from workers.video import basketball_detector
    from workers.video.basketball_pass import BasketballPass
    import numpy as np

    class Detector:
        manifest = {}
        device = "cpu"

        def __init__(self, *a):
            pass

        def detect(self, frame, timestamp):
            return [
                dict(
                    class_name=c,
                    timestamp_ms=timestamp,
                    bbox=[0.2, 0.3, 0.03, 0.03],
                    confidence=0.9,
                )
                for c in ["basketball", "hoop"]
            ]

    monkeypatch.setattr(basketball_detector, "BasketballDetector", Detector)
    ball = BasketballPass(
        dict(duration_ms=300), "hash", tmp_path, "cpu", 1, lambda *a: None
    )
    for t in [0, 50, 100]:
        ball.observe(np.zeros((2, 2, 3)), t)
    ball.begin_shot("1", 150)
    for t in [150, 200, 250]:
        ball.observe(np.zeros((2, 2, 3)), t)
    report = ball.finish(
        [],
        6,
        shots=[
            dict(shot_id="0", start_ms=0, end_ms=150),
            dict(shot_id="1", start_ms=150, end_ms=300),
        ],
    )
    assert len({b["ball_track_id"] for b in ball.balls}) == 2
    assert len(report["stable_hoops"]) == 2
    assert {h["shot_id"] for h in report["stable_hoops"]} == {"0", "1"}
    assert report["shot_candidates"] == []


def test_manual_camera_clip_export_is_bounded_playable_and_idempotent(client, video):
    game, id, path = seed_game(client, video)
    main.put(
        "shots",
        id,
        dict(
            job_id=id,
            items=[
                dict(shot_id="0", start_ms=0, end_ms=1000, category="uncertain"),
                dict(
                    shot_id="1",
                    start_ms=1000,
                    end_ms=2000,
                    category="non_gameplay_candidate",
                ),
            ],
        ),
    )
    route = path.removesuffix("/highlights") + "/shots/1/clip"
    assert client.post(route, json=dict(job_id="stale")).status_code == 409
    r = client.post(route, json=dict(job_id=id))
    assert r.status_code == 201, r.text
    clip = r.json()
    assert (clip["source_start_ms"], clip["source_end_ms"]) == (1000, 2000)
    assert clip["kind"] == "camera_shot" and clip["identity_verified"] is False
    assert clip["qa_status"] == "passed" and "path" not in clip
    assert client.get(clip["uri"], headers={"Range": "bytes=0-99"}).status_code == 206
    assert client.post(route, json=dict(job_id=id)).json()["clip_id"] == clip["clip_id"]
