"""Input submits the durable worker without a follow-up browser Analyze action."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.app import main


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main, "POOL", SimpleNamespace(submit=lambda *args: calls.append(args))
    )
    return calls


def upload(client, video, **values):
    return client.post(
        "/api/games",
        files={"video": ("test.mp4", video.read_bytes(), "video/mp4")},
        data={"rights_confirmed": "true", **values},
    )


def sample_file(tmp_path, video, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    target = tmp_path / "data/trackid3x3/outdoor_stationary_12s.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.read_bytes())


def test_autonomous_upload_queues_before_return_and_worker_is_browser_independent(
    client, video, queued, monkeypatch
):
    response = upload(client, video, auto_analyze="true")
    assert response.status_code == 201
    game = response.json()
    id = game["analysis_job_id"]
    assert queued == [(main.run_job, id)]
    assert main.get("job", id)["status"] == "queued"
    assert main.get("job", id)["options"]["basketball"] is True

    def worker(source, output, options, emit):
        assert Path(source).is_file()
        assert options["game_id"] == game["game_id"]
        return {"test_only": True, "clips": []}

    monkeypatch.setattr(main, "process_video", worker)
    # Run the submitted executor callback after the ingest request has finished.
    # No second HTTP request, polling connection, or user confirmation starts it.
    queued[0][0](*queued[0][1:])
    assert main.get("job", id)["status"] == "succeeded"
    assert main.get("job", id)["attempts"] == 1


def test_legacy_upload_does_not_start_job_without_opt_in(client, video, queued):
    response = upload(client, video)
    assert response.status_code == 201
    assert "analysis_job_id" not in response.json()
    assert queued == []
    assert main.all_records("job") == []


@pytest.mark.parametrize("invalid", ["rights", "media", "sport"])
def test_invalid_input_never_schedules_analysis(client, video, queued, invalid):
    values = {"auto_analyze": "true"}
    if invalid == "rights":
        values["rights_confirmed"] = "false"
    if invalid == "sport":
        values["sport"] = "football"
    if invalid == "media":
        video.write_bytes(b"not a video")
    assert upload(client, video, **values).status_code == 422
    assert not queued
    assert main.all_records("game") == []
    assert main.all_records("job") == []


def test_sample_reuses_queued_running_and_succeeded_jobs(
    client, video, queued, tmp_path, monkeypatch
):
    sample_file(tmp_path, video, monkeypatch)
    first = client.post("/api/games/sample?auto_analyze=true").json()
    id = first["analysis_job_id"]
    for status in ("queued", "running", "cancelling", "succeeded"):
        main.set_job(id, status=status)
        other = client.post("/api/games/sample?auto_analyze=true").json()
        assert other["game_id"] == first["game_id"]
        assert other["analysis_job_id"] == id
    assert len(queued) == 1
    assert len(main.all_records("game")) == 1
    assert len(main.all_records("job")) == 1


def test_concurrent_sample_requests_queue_one_job(
    client, video, queued, tmp_path, monkeypatch
):
    sample_file(tmp_path, video, monkeypatch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(
                lambda _: client.post("/api/games/sample?auto_analyze=true"), range(3)
            )
        )
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()["analysis_job_id"] for r in responses}) == 1
    assert len(queued) == 1
    assert len(main.all_records("game")) == 1


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_explicit_sample_input_can_retry_terminal_failure(
    client, video, queued, tmp_path, monkeypatch, terminal
):
    sample_file(tmp_path, video, monkeypatch)
    first = client.post("/api/games/sample?auto_analyze=true").json()
    main.set_job(first["analysis_job_id"], status=terminal)
    second = client.post("/api/games/sample?auto_analyze=true").json()
    assert second["game_id"] == first["game_id"]
    assert second["analysis_job_id"] != first["analysis_job_id"]
    assert len(queued) == 2
    assert (
        client.post("/api/games/sample?auto_analyze=true").json()["analysis_job_id"]
        == second["analysis_job_id"]
    )
    assert len(queued) == 2


def test_enqueue_failure_keeps_registered_media_and_durable_visible_error(
    client, video, monkeypatch
):
    def fail(*_):
        raise RuntimeError("executor failure with private /machine/path")

    monkeypatch.setattr(main, "POOL", SimpleNamespace(submit=fail))
    response = upload(client, video, auto_analyze="true")
    assert response.status_code == 201
    game = response.json()
    assert "analysis_error" in game and "/machine/path" not in game["analysis_error"]
    stored = main.get("game", game["game_id"])
    assert stored["analysis_error"] == game["analysis_error"]
    assert Path(stored["path"]).is_file()
    assert client.get(game["source_uri"]).status_code == 200
    job = main.get("job", game["analysis_job_id"])
    assert job["status"] == "failed" and job["error"] == game["analysis_error"]
    assert any(
        a["action"] == "analysis_enqueue_failed" for a in main.all_records("audit")
    )
    assert (
        client.get("/api/games").json()["items"][0]["analysis_error"]
        == game["analysis_error"]
    )


def test_device_error_preserves_game_and_can_recover_on_next_sample_input(
    client, video, queued, tmp_path, monkeypatch
):
    sample_file(tmp_path, video, monkeypatch)
    original = main.select_device
    monkeypatch.setattr(
        main,
        "select_device",
        lambda _: (_ for _ in ()).throw(ValueError("Requested device is unavailable")),
    )
    first = client.post("/api/games/sample?auto_analyze=true").json()
    assert first["analysis_error"] == "Requested device is unavailable"
    assert "analysis_job_id" not in first
    assert queued == []
    assert Path(main.get("game", first["game_id"])["path"]).is_file()
    monkeypatch.setattr(main, "select_device", original)
    second = client.post("/api/games/sample?auto_analyze=true").json()
    assert "analysis_job_id" in second and "analysis_error" not in second
    assert "analysis_error" not in main.get("game", first["game_id"])


def test_automatic_upload_can_be_cancelled_without_retry_or_lost_source(
    client, video, monkeypatch
):
    import threading
    from backend.tests.test_cancellation import wait_for

    ready = threading.Event()
    release = threading.Event()
    calls = []
    partial_uri = []

    def worker(source, output, options, emit):
        calls.append(options["job_id"])
        emit(
            "tracks.batch",
            dict(
                source_frame=0,
                items=[
                    dict(
                        track_id="1",
                        timestamp_ms=0,
                        bbox=[0, 0, 0.2, 0.2],
                        keypoints=[],
                        confidence=0.9,
                    )
                ],
            ),
        )
        clip = dict(
            clip_id="auto-partial",
            event_id="event1",
            path=str(source),
            source_start_ms=0,
            source_end_ms=1000,
            qa_status="passed",
        )
        emit("clip.updated", clip)
        partial_uri.append(clip["uri"])
        ready.set()
        while not release.wait(0.01):
            # The real output emitter checks the durable cancel marker before writing.
            emit("analysis.progress", dict(processed_frames=1, source_timestamp_ms=0))
        return {"test_only": True, "clips": []}

    monkeypatch.setattr(main, "process_video", worker)
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(main, "POOL", pool)
        try:
            response = upload(client, video, auto_analyze="true")
            assert response.status_code == 201
            game = response.json()
            id = game["analysis_job_id"]
            assert ready.wait(3)
            result = client.post("/api/jobs/" + id + "/cancel")
            assert result.json()["status"] in ("cancelling", "cancelled")
            state = wait_for(
                lambda: (
                    j if (j := main.get("job", id))["status"] == "cancelled" else None
                )
            )
            assert (
                state["attempts"] == 1
                and state["artifacts"] == []
                and state["report"] is None
            )
            assert calls == [id]
            assert (
                client.get("/api/games/" + game["game_id"] + "/tracks").json()["items"]
                == []
            )
            assert (
                client.get("/api/games/" + game["game_id"] + "/clips").json()["items"]
                == []
            )
            assert client.get(partial_uri[0]).status_code == 404
            assert client.get(game["source_uri"]).status_code == 200
            assert (
                client.post("/api/jobs/" + id + "/cancel").json()["status"]
                == "cancelled"
            )
            assert not any(
                a["action"] == "analysis_error" for a in main.all_records("audit")
            )
        finally:
            release.set()


def test_automatic_upload_exhausts_bounded_retries_and_reports_failure(
    client, video, queued, monkeypatch
):
    calls = []

    def worker(*_):
        calls.append(1)
        raise OSError("injected persistent worker failure")

    monkeypatch.setattr(main, "process_video", worker)
    game = upload(client, video, auto_analyze="true").json()
    id = game["analysis_job_id"]
    queued[0][0](*queued[0][1:])
    job = main.get("job", id)
    assert job["status"] == "failed" and job["attempts"] == 3
    assert len(calls) == 3
    assert job["artifacts"] == []
    assert (
        len([a for a in main.all_records("audit") if a["action"] == "analysis_error"])
        == 3
    )
    assert client.get(game["source_uri"]).status_code == 200
