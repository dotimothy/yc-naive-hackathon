import json
import time
import pytest
from backend.app import main
from workers.render.media import render_clip, verify_clip
from workers.video.tracker import Tracker


def submit(client, video, **extra):
    return client.post(
        "/api/games",
        files={"video": ("game.mp4", video.read_bytes(), "video/mp4")},
        data={"rights_confirmed": "true", **extra},
    )


def test_upload_boundaries_and_media_range(client, video):
    assert submit(client, video, rights_confirmed="false").status_code == 422
    assert submit(client, video, sport="football").status_code == 422
    assert (
        client.post(
            "/api/games",
            files={"video": ("bad.mp4", b"not video")},
            data={"rights_confirmed": "true"},
        ).status_code
        == 422
    )
    game = submit(client, video).json()
    assert game["duration_ms"] == 2000 and "path" not in game
    response = client.get(game["source_uri"], headers={"Range": "bytes=0-99"})
    assert response.status_code == 206 and len(response.content) == 100
    assert client.get("/api/media/not-registered").status_code == 404
    assert client.get("/api/profiles").json() == {"items": []}


def test_clip_provenance_and_bad_range(video, tmp_path):
    with pytest.raises(ValueError):
        render_clip(video, 1900, 2500, tmp_path / "bad.mp4")
    p = render_clip(video, 500, 1500, tmp_path / "good.mp4")
    report = verify_clip(video, p, 500, 1500)
    assert report["decoded"] and report["qa_status"] == "passed"
    with pytest.raises(ValueError):
        verify_clip(video, p, 0, 1800)


def test_idempotency_retry_and_persistence(client, video, monkeypatch):
    game = submit(client, video).json()
    calls = []

    def transient_worker(source, out, options, emit):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("injected transient failure")
        emit("phase", {"phase": "track"})
        emit(
            "tracks.batch",
            {
                "items": [
                    dict(
                        game_id=options["game_id"],
                        track_id="1",
                        timestamp_ms=0,
                        bbox=[0, 0, 0.2, 0.2],
                        keypoints=[],
                        confidence=0.9,
                    )
                ],
                "source_frame": 0,
            },
        )
        return {"test_only": True, "clips": []}

    monkeypatch.setattr(main, "process_video", transient_worker)
    url = "/api/games/" + game["game_id"] + "/analyze"
    headers = {"Idempotency-Key": "retry-test"}
    first = client.post(url, json={}, headers=headers)
    assert first.status_code == 202
    id = first.json()["job_id"]
    assert client.post(url, json={}, headers=headers).json()["job_id"] == id
    assert client.post(url, json={"stride": 1}, headers=headers).status_code == 409
    for _ in range(100):
        job = client.get("/api/jobs/" + id).json()
        if job["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.03)
    assert job["status"] == "succeeded" and job["attempts"] == 2 and len(calls) == 2
    audit = client.get("/api/jobs/" + id + "/audit").json()["items"]
    assert any(a["action"] == "analysis_error" for a in audit)
    assert (
        len(client.get("/api/games/" + game["game_id"] + "/tracks").json()["items"])
        == 1
    )
    # New SQLite connection reads persisted terminal state and ordered replay records.
    with main.connect() as db:
        assert (
            json.loads(
                db.execute(
                    "SELECT value FROM records WHERE kind='job' AND id=?", (id,)
                ).fetchone()[0]
            )["status"]
            == "succeeded"
        )
        rows = db.execute(
            "SELECT sequence FROM updates WHERE game_id=? ORDER BY sequence",
            (game["game_id"],),
        ).fetchall()
        assert len(rows) > 3 and len({r[0] for r in rows}) == len(rows)


def test_tracker_reorder_and_expiration():
    tracker = Tracker(max_age=1)
    assert tracker.update([[0, 0, 10, 10], [20, 0, 30, 10]]) == [1, 2]
    assert tracker.update([[21, 0, 31, 10], [1, 0, 11, 10]]) == [2, 1]
    tracker.update([])
    tracker.update([])
    assert tracker.update([[1, 0, 11, 10]]) == [3]


def test_unavailable_device_rejected_before_queue(client, video, monkeypatch):
    game = submit(client, video).json()
    original = main.select_device

    def reject_unavailable(device=None):
        if device == "cuda:9999":
            raise ValueError("Requested CUDA device unavailable")
        return original(device)

    monkeypatch.setattr(main, "select_device", reject_unavailable)
    response = client.post(
        "/api/games/" + game["game_id"] + "/analyze",
        json={"device": "cuda:9999"},
        headers={"Idempotency-Key": "bad-device"},
    )
    assert response.status_code == 422
    assert client.get("/api/games/" + game["game_id"] + "/jobs").json()["items"] == []
