"""Shared decoder cadence, live persistence and worker IPC safety boundaries."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from workers.video import pipeline, coordinated
from backend.app import main
from backend.tests.test_highlights import seed_game


@pytest.mark.parametrize("ball_failure", [False, True])
@pytest.mark.parametrize("camera_cut", [False, True])
def test_single_decoder_model_cadences_and_source_timestamps(
    tmp_path, monkeypatch, ball_failure, camera_cut
):
    retrieved, pose_frames, ball_frames = [], [], []
    captures = []

    class Capture:
        def __init__(self, source):
            self.index = -1
            self.released = False
            captures.append(self)

        def grab(self):
            self.index += 1
            return self.index < 12

        def retrieve(self):
            retrieved.append(self.index)
            frame = np.full((32, 32, 3), self.index, dtype=np.uint8)
            if camera_cut:
                frame[:] = [
                    self.index,
                    100 if self.index >= 6 else 0,
                    0 if self.index >= 6 else 100,
                ]
            return True, frame

        def get(self, key):
            return self.index * 100

        def release(self):
            self.released = True

    class Ball:
        def __init__(self, *args):
            self.frames = 0
            self.emit = args[-1]

        def begin_shot(self, shot_id, timestamp):
            pass

        def snapshot(self):
            return dict(processed_frames=self.frames)

        def observe(self, frame, timestamp):
            ball_frames.append((int(frame[0, 0, 0]), timestamp))
            if ball_failure and timestamp == 200:
                raise RuntimeError("injected detector failure")
            self.frames += 1

        def flush(self):
            pass

        def finish(self, players, decoded, shots=None):
            assert decoded == 12 and len(players) == 3
            report = dict(processed_frames=self.frames, integrated=True)
            self.emit("basketball.updated", dict(status="succeeded", report=report))
            return report

    monkeypatch.setattr(pipeline.cv2, "VideoCapture", Capture)
    monkeypatch.setattr(pipeline, "BasketballPass", Ball)
    monkeypatch.setattr(
        pipeline, "probe", lambda _: dict(width=32, height=32, fps=10, duration_ms=1200)
    )
    monkeypatch.setattr(pipeline, "sha256", lambda _: "sourcehash")
    monkeypatch.setattr(
        pipeline,
        "get_model",
        lambda _: (SimpleNamespace(roi_heads=SimpleNamespace()), 0, False),
    )

    def infer(model, tensor, device):
        pose_frames.append(int(tensor[0, 0, 0]))
        return (
            dict(
                scores=torch.tensor([0.99]),
                boxes=torch.tensor([[2, 2, 15, 28]]),
                keypoints=torch.ones((1, 17, 3)),
            ),
            0.01,
            0,
        )

    monkeypatch.setattr(pipeline, "infer", infer)
    monkeypatch.setattr(pipeline, "render_highlights", lambda *a: ([], []))
    monkeypatch.setattr(pipeline, "render_clip", lambda *a, **k: tmp_path / "clip.mp4")
    monkeypatch.setattr(pipeline, "verify_clip", lambda *a: dict(qa_status="passed"))
    events = []
    result = pipeline.process_video(
        "source.mp4",
        tmp_path,
        dict(
            game_id="g",
            job_id="j",
            device="cpu",
            basketball=True,
            stride=5,
            ball_stride=2,
        ),
        lambda k, v: events.append((k, v)),
    )
    assert len(captures) == 1 and captures[0].released
    expected = [0, 2, 5, 10] if ball_failure else [0, 2, 4, 5, 6, 8, 10]
    assert retrieved == list(range(12))
    assert pose_frames == [0, 5, 10]
    assert ball_frames == [
        (i, i * 100) for i in ([0, 2] if ball_failure else [0, 2, 4, 6, 8, 10])
    ]
    assert result["analysis_mode"] == "shared_decoder" and result[
        "basketball_status"
    ] == ("failed" if ball_failure else "succeeded")
    assert result["retrieved_frames"] == 12 and result["decoded_frames"] == 12
    # Uncertain/close-up shots finish honestly with empty outputs, never an approval gate.
    assert result["automatic_output"]["status"] == "no_eligible_clips"
    assert result["automatic_output"]["requires_human_action"] is False
    assert result["automatic_output"]["clip_count"] == 0
    assert [
        v["source_timestamp_ms"] for k, v in events if k == "analysis.progress"
    ] == [i * 100 for i in expected]

    if camera_cut:
        assert [(s["start_ms"], s["end_ms"]) for s in result["shots"]] == [
            (0, 600),
            (600, 1200),
        ]
        tracked = json.loads((tmp_path / "tracks.json").read_text())
        assert tracked[-1]["track_id"].startswith("s1:")
        assert tracked[-1]["player_id"] not in {s["player_id"] for s in tracked[:-1]}
        assert len(result["identity_summary_by_shot"]) == 2
    else:
        assert len(result["shots"]) == 1


def test_optional_runtime_missing_is_explicit_and_keeps_core(tmp_path, monkeypatch):
    monkeypatch.setattr(
        coordinated, "optional_runtime", lambda: (None, "Detector setup unavailable")
    )
    received = {}

    def core(source, out, options, emit):
        received.update(options)
        return {"ok": True}

    monkeypatch.setattr(coordinated, "process_video", core)
    assert coordinated.run_analysis(
        "x", tmp_path, dict(basketball=True), lambda *a: None
    ) == {"ok": True}
    assert (
        received["basketball"] is False
        and received["basketball_unavailable"] == "Detector setup unavailable"
    )


def worker_script(tmp_path, monkeypatch, code):
    script = tmp_path / "scripts/run_analysis.py"
    script.parent.mkdir()
    script.write_text(code)
    monkeypatch.setattr(coordinated, "ROOT", tmp_path)
    monkeypatch.setattr(
        coordinated, "optional_runtime", lambda: (Path(sys.executable), None)
    )


def test_worker_ack_preserves_registered_clip_uri(tmp_path, monkeypatch):
    worker_script(
        tmp_path,
        monkeypatch,
        """import sys,json
request=json.loads(sys.stdin.readline())
print(json.dumps(dict(kind='clip.updated',payload=dict(path='local.mp4'))),flush=True)
reply=json.loads(sys.stdin.readline())
print(json.dumps(dict(kind='result',payload=dict(clips=[reply['payload']]))),flush=True)
""",
    )

    def emit(kind, payload):
        assert kind == "clip.updated"
        payload.pop("path")
        payload["uri"] = "/api/media/registered"

    report = coordinated.run_analysis(
        "x", tmp_path / "out", dict(basketball=True), emit
    )
    assert report["clips"] == [dict(uri="/api/media/registered")]


def test_worker_timeout_and_missing_result_fail(tmp_path, monkeypatch):
    worker_script(
        tmp_path, monkeypatch, "import sys,time\nsys.stdin.readline()\ntime.sleep(5)\n"
    )
    monkeypatch.setenv("SCOUTFILM_WORKER_TIMEOUT_SECONDS", ".2")
    import subprocess

    with pytest.raises(subprocess.TimeoutExpired):
        coordinated.run_analysis(
            "x", tmp_path / "out", dict(basketball=True), lambda *a: None
        )
    (tmp_path / "scripts/run_analysis.py").write_text(
        "import sys\nsys.stdin.readline()\n"
    )
    with pytest.raises(RuntimeError, match="worker failed"):
        coordinated.run_analysis(
            "x", tmp_path / "out", dict(basketball=True), lambda *a: None
        )


def test_live_ball_persistence_current_job_scope_and_retired_second_action(
    client, video
):
    game, parent, path = seed_game(client, video)
    root = path.removesuffix("/highlights") + "/basketball"
    emit = main.output_emitter(game["game_id"], parent, 1)
    emit(
        "basketball.updated",
        dict(
            status="running",
            report=dict(
                processed_frames=0,
                frames_with_ball=0,
                stable_hoops=[],
                shot_candidates=[],
            ),
        ),
    )
    ball = dict(class_name="basketball", timestamp_ms=0, bbox=[0.2, 0.3, 0.01, 0.01])
    emit(
        "basketball.batch",
        dict(
            items=[ball],
            processed_frames=5,
            frames_with_ball=1,
            source_timestamp_ms=250,
        ),
    )
    response = client.get(root).json()
    assert response["integrated"] and response["status"] == "running"
    assert response["report"]["processed_frames"] == 5
    objects = client.get(root + "/objects").json()
    assert objects["items"] == [ball] and objects["parent_job_id"] == parent
    # Completed reads use the final artifact, including any trailing observations.
    out = main.DATA / "processed" / parent / "basketball"
    out.mkdir(parents=True)
    trailing = dict(ball, timestamp_ms=300)
    (out / "objects.json").write_text(json.dumps([ball, trailing]))
    emit(
        "basketball.updated",
        dict(status="succeeded", report=dict(processed_frames=6, frames_with_ball=2)),
    )
    assert client.get(root + "/objects").json()["items"] == [ball, trailing]
    assert (
        client.post(
            root, json={}, headers={"Idempotency-Key": "old-action"}
        ).status_code
        == 410
    )
    main.put(
        "job",
        "new",
        dict(
            job_id="new",
            game_id=game["game_id"],
            status="running",
            phase="track",
            attempts=1,
            error=None,
            artifacts=[],
        ),
    )
    assert client.get(root).json()["parent_job_id"] == "new"
    assert client.get(root + "/objects").json()["items"] == []


def test_ball_eof_flush_preserves_last_live_batch(tmp_path, monkeypatch):
    from workers.video import basketball_detector
    from workers.video.basketball_pass import BasketballPass

    class Detector:
        manifest = {}
        device = "cpu"

        def __init__(self, *args):
            pass

        def detect(self, frame, timestamp):
            return [
                dict(
                    class_name="basketball",
                    timestamp_ms=timestamp,
                    bbox=[0.1, 0.2, 0.01, 0.01],
                    confidence=0.8,
                )
            ]

    monkeypatch.setattr(basketball_detector, "BasketballDetector", Detector)
    events = []
    ball = BasketballPass(
        dict(duration_ms=250),
        "sourcehash",
        tmp_path,
        "cpu",
        2,
        lambda k, v: events.append((k, v)),
    )
    for t in range(0, 250, 50):
        ball.observe(np.zeros((2, 2, 3)), t)
    assert ball.snapshot()["coverage_scope"] == "partial_video"
    report = ball.finish([], 10)
    batches = [p for k, p in events if k == "basketball.batch"]
    assert len(batches) == 1 and len(batches[0]["items"]) == 5
    assert report["coverage_scope"] == "full_video"
    assert len(json.loads((tmp_path / "objects.json").read_text())) == 5
