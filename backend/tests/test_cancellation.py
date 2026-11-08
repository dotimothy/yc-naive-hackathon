"""Cancellation owns terminal state, partial evidence visibility and worker shutdown."""

import os
import threading
import time

import pytest

from backend.app import main
from backend.tests.test_local import submit
from backend.tests.test_highlights import seed_game
from backend.tests.test_coordinated import worker_script
from workers.video import coordinated
from workers.video.cancellation import AnalysisCancelled, MARKER


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for cancellation state")


def test_queued_cancel_never_starts_worker_and_is_idempotent(
    client, video, monkeypatch
):
    monkeypatch.setattr(main.POOL, "submit", lambda *a, **k: None)
    game = submit(client, video).json()
    job = client.post(
        "/api/games/" + game["game_id"] + "/analyze",
        json={},
        headers={"Idempotency-Key": "queued"},
    ).json()
    route = "/api/jobs/" + job["job_id"] + "/cancel"
    response = client.post(route)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"
    assert response.json()["attempts"] == 0
    before = len(main.all_records("audit"))
    assert client.post(route).json()["status"] == "cancelled"
    monkeypatch.setattr(
        main, "process_video", lambda *a: pytest.fail("Cancelled queued worker started")
    )
    main.run_job(job["job_id"])
    assert len(main.all_records("audit")) == before
    assert (main.DATA / "processed" / job["job_id"] / MARKER).exists()
    assert client.post("/api/jobs/unknown/cancel").status_code == 404


def test_running_cancel_removes_partial_outputs_and_never_retries(
    client, video, monkeypatch
):
    ready = threading.Event()
    clip_uri = []
    calls = []

    def worker(source, out, options, emit):
        calls.append(1)
        emit(
            "tracks.batch",
            dict(
                items=[
                    dict(
                        track_id="1",
                        timestamp_ms=0,
                        bbox=[0, 0, 0.2, 0.2],
                        keypoints=[],
                        confidence=0.9,
                    )
                ],
                source_frame=0,
            ),
        )
        emit("basketball.updated", dict(status="running", report=None))
        clip = dict(
            clip_id="partial",
            event_id="event1",
            path=str(source),
            source_start_ms=0,
            source_end_ms=1000,
            qa_status="passed",
        )
        emit("clip.updated", clip)
        clip_uri.append(clip["uri"])
        ready.set()
        while True:
            time.sleep(0.02)
            emit("analysis.progress", dict(processed_frames=1, source_timestamp_ms=0))

    monkeypatch.setattr(main, "process_video", worker)
    game = submit(client, video).json()
    path = "/api/games/" + game["game_id"]
    job = client.post(
        path + "/analyze", json={}, headers={"Idempotency-Key": "running"}
    ).json()
    assert ready.wait(3)
    response = client.post("/api/jobs/" + job["job_id"] + "/cancel")
    assert response.json()["status"] in ("cancelling", "cancelled")
    state = wait_for(
        lambda: (
            j
            if (j := main.get("job", job["job_id"]))["status"] == "cancelled"
            else None
        )
    )
    assert (
        state["attempts"] == 1 and state["report"] is None and state["artifacts"] == []
    )
    assert len(calls) == 1
    assert client.get(path + "/tracks").json()["items"] == []
    assert client.get(path + "/clips").json()["items"] == []
    assert client.get(clip_uri[0]).status_code == 404
    assert (
        client.get(game["source_uri"], headers={"Range": "bytes=0-99"}).status_code
        == 206
    )
    assert client.get(path + "/basketball").json()["status"] == "cancelled"
    assert not any(a["action"] == "analysis_error" for a in main.all_records("audit"))
    monkeypatch.setattr(main.POOL, "submit", lambda *a, **k: None)
    assert (
        client.post(
            path + "/analyze", json={}, headers={"Idempotency-Key": "rerun"}
        ).status_code
        == 202
    )


def test_successful_job_cancel_is_noop(client, video):
    _, id, path = seed_game(client, video)
    before = main.get("job", id)
    assert client.post("/api/jobs/" + id + "/cancel").json()["status"] == "succeeded"
    assert main.get("job", id) == before
    assert client.get(path.removesuffix("/highlights") + "/tracks").json()["items"]


def test_subprocess_cancellation_interrupts_inference_and_reaps_worker(
    tmp_path, monkeypatch
):
    pidfile = tmp_path / "worker.pid"
    worker_script(
        tmp_path,
        monkeypatch,
        f"""import sys,json,time,os
json.loads(sys.stdin.readline())
open({str(pidfile)!r},'w').write(str(os.getpid()))
time.sleep(60)
""",
    )
    errors = []
    out = tmp_path / "out"

    def run():
        try:
            coordinated.run_analysis(
                "source.mp4", out, dict(basketball=True), lambda *a: None
            )
        except Exception as e:
            errors.append(e)

    thread = threading.Thread(target=run)
    thread.start()
    wait_for(pidfile.exists)
    started = time.monotonic()
    (out / MARKER).touch()
    thread.join(2)
    assert not thread.is_alive() and time.monotonic() - started < 2
    assert len(errors) == 1 and isinstance(errors[0], AnalysisCancelled)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_restart_finishes_persisted_cancellation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "DB", tmp_path / "test.sqlite3")
    main.initialize()
    main.put(
        "job",
        "interrupted",
        dict(
            job_id="interrupted",
            game_id="game",
            status="cancelling",
            attempts=1,
            artifacts=[],
        ),
    )
    marker = tmp_path / "processed/interrupted" / MARKER
    marker.parent.mkdir(parents=True)
    marker.touch()
    with TestClient(main.app):
        assert main.get("job", "interrupted")["status"] == "cancelled"
