import json
from backend.app import main
from backend.tests.test_highlights import seed_game


def test_ball_candidate_import_requires_current_tracks_and_review(
    client, video, tmp_path
):
    game, parent, path = seed_game(client, video)
    root = path.removesuffix("/highlights") + "/basketball"
    assert client.get(root).json()["status"] == "not_analyzed"
    proposal = dict(
        candidate_id="shot1",
        start_ms=0,
        end_ms=1900,
        approach_ms=1000,
        evidence_timestamps=[500, 750, 1000],
        evidence_quality=0.8,
    )
    main.put(
        "basketball_job",
        "ball1",
        dict(
            run_id="ball1",
            game_id=game["game_id"],
            parent_job_id=parent,
            status="succeeded",
            created_at=1,
            output_dir=str(tmp_path),
            report=dict(shot_candidates=[proposal]),
        ),
    )
    (tmp_path / "objects.json").write_text(
        json.dumps(
            [
                dict(
                    class_name="basketball", timestamp_ms=0, bbox=[0.1, 0.1, 0.1, 0.1]
                ),
                dict(class_name="player"),
            ]
        )
    )
    assert len(client.get(root + "/objects").json()["items"]) == 1
    route = root + "/candidates/shot1/review"
    assert client.post(route, json=dict(track_id="1")).status_code == 422
    assert (
        client.post(route, json=dict(track_id="999", actor_confirmed=True)).status_code
        == 422
    )
    response = client.post(route, json=dict(track_id="1", actor_confirmed=True))
    assert response.status_code == 201, response.text
    c = next(
        c
        for c in response.json()["candidates"]
        if c["candidate_id"].startswith("ball-")
    )
    assert c["review_status"] == "pending" and c["event_type"] == "visual_activity"
    assert not any(
        p["basketball_stats"]["has_evidence"] for p in response.json()["players"]
    )
    assert len(
        client.post(route, json=dict(track_id="1", actor_confirmed=True)).json()[
            "candidates"
        ]
    ) == len(response.json()["candidates"])
    saved = main.get("basketball_job", "ball1")
    saved["parent_job_id"] = "old"
    main.put("basketball_job", "ball1", saved)
    assert (
        client.post(route, json=dict(track_id="1", actor_confirmed=True)).status_code
        == 409
    )


def test_split_rebuilds_fragments_and_invalidates_ball_actor_import(client, video):
    game, parent, path = seed_game(client, video)
    source = main.get("batch", "batch1")
    c = client.get(path).json()["candidates"][0]
    assert (
        client.put(
            path + "/candidates/" + c["candidate_id"] + "/review",
            json=dict(
                decision="approved",
                event_type="rebound",
                event_time_ms=500,
                actor_confirmed=True,
            ),
        ).status_code
        == 200
    )
    response = client.post(
        path + "/identity/splits", json=dict(track_id="1", split_ms=510)
    )
    assert response.status_code == 200, response.text
    assert response.json()["identity_revision"] == 1
    assert all(c["review_status"] == "pending" for c in response.json()["candidates"])
    tracks = client.get(path.removesuffix("/highlights") + "/tracks").json()["items"]
    assert {"1~before", "1~500"} <= {s["track_id"] for s in tracks}
    assert main.get("batch", "batch1") == source
    root = path.removesuffix("/highlights") + "/basketball"
    main.put(
        "basketball_job",
        "oldball",
        dict(
            run_id="oldball",
            game_id=game["game_id"],
            parent_job_id=parent,
            status="succeeded",
            created_at=1,
            identity_revision=0,
            report=dict(shot_candidates=[]),
        ),
    )
    assert (
        client.post(
            root + "/candidates/any/review",
            json=dict(track_id="1~before", actor_confirmed=True),
        ).status_code
        == 409
    )
    assert client.delete(path + "/identity/splits").status_code == 200
    tracks = client.get(path.removesuffix("/highlights") + "/tracks").json()["items"]
    assert {s["track_id"] for s in tracks} == {"1", "2"}
