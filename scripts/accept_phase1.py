#!/usr/bin/env python3
"""Run real, unattended upload-to-clips acceptance in isolated local storage.

Uses the same HTTP handlers as the dashboard. No review/correction/generation
request is sent. --inject-transient fails one worker invocation before running
real inference on retry; it is an acceptance harness, not a production option.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--device", choices=["cpu", "mps", "cuda:0"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inject-transient", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Real inference requires --execute")
    if not args.source.is_file():
        parser.error("Source does not exist")
    if args.output.exists():
        parser.error("Use a new output directory to keep acceptance runs isolated")
    args.output.mkdir(parents=True)
    os.environ["SCOUTFILM_DATA"] = str(args.output.resolve())
    os.environ["SCOUTFILM_DEVICE"] = args.device
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"
    from fastapi.testclient import TestClient
    from backend.app import main as api

    real_worker = api.process_video
    invocations = []

    def worker(*a, **kw):
        invocations.append(time.time())
        if args.inject_transient and len(invocations) == 1:
            raise OSError("Acceptance-only injected transient failure")
        return real_worker(*a, **kw)

    api.process_video = worker
    started = time.monotonic()
    with TestClient(api.app) as client:
        with args.source.open("rb") as video:
            response = client.post(
                "/api/games",
                files={"video": (args.source.name, video, "video/mp4")},
                data={"rights_confirmed": "true", "auto_analyze": "true"},
            )
        assert response.status_code == 201, response.text
        game = response.json()
        assert "analysis_error" not in game, game
        job_id = game["analysis_job_id"]
        status = None
        while time.monotonic() - started < 1200:
            job = client.get("/api/jobs/" + job_id).json()
            if job["status"] != status:
                print(
                    json.dumps(
                        dict(
                            job_id=job_id,
                            status=job["status"],
                            phase=job["phase"],
                            attempts=job["attempts"],
                        )
                    ),
                    flush=True,
                )
                status = job["status"]
            if status in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(0.5)
        else:
            client.post("/api/jobs/" + job_id + "/cancel")
            raise TimeoutError("Acceptance timed out; cancellation requested")
        assert status == "succeeded", job
        report = job["report"]
        assert report["model"] == "ultralytics/yolo26n-pose"
        tracks = client.get("/api/games/" + game["game_id"] + "/tracks").json()["items"]
        clips = client.get("/api/games/" + game["game_id"] + "/clips").json()["items"]
        audit = client.get("/api/jobs/" + job_id + "/audit").json()["items"]
        assert tracks and len(clips) >= 2, (len(tracks), len(clips))
        assert job["attempts"] == (2 if args.inject_transient else 1)
        assert len({c["clip_id"] for c in clips}) == len(clips)
        assert all(not t.get("identity_confirmed") for t in tracks)
        checks = []
        for clip in clips:
            assert clip["qa_status"] == "passed"
            assert (
                0
                <= clip["source_start_ms"]
                < clip["source_end_ms"]
                <= game["duration_ms"]
            )
            media = client.get(clip["uri"], headers={"Range": "bytes=0-99"})
            assert media.status_code == 206
            artifact = api.get("artifact", clip["uri"].split("/")[-1])
            path = Path(artifact["path"])
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                check=True,
                capture_output=True,
            )
            assert hashlib.sha256(path.read_bytes()).hexdigest() == clip["sha256"]
            shots = report.get("shots", [])
            assert not shots or any(
                s["start_ms"]
                <= clip["source_start_ms"]
                < clip["source_end_ms"]
                <= s["end_ms"]
                for s in shots
            )
            checks.append(
                dict(
                    clip_id=clip["clip_id"],
                    qa_status=clip["qa_status"],
                    decoded=True,
                    source_start_ms=clip["source_start_ms"],
                    source_end_ms=clip["source_end_ms"],
                )
            )
        snapshot = dict(job=job, tracks=tracks, clips=clips, audit=audit)
    # Re-enter the app lifespan with the same on-disk DB: terminal state survives.
    with TestClient(api.app) as client:
        assert client.get("/api/jobs/" + job_id).json() == snapshot["job"]
        assert (
            client.get("/api/games/" + game["game_id"] + "/tracks").json()["items"]
            == snapshot["tracks"]
        )
        assert (
            client.get("/api/games/" + game["game_id"] + "/clips").json()["items"]
            == snapshot["clips"]
        )
        assert (
            client.get("/api/jobs/" + job_id + "/audit").json()["items"]
            == snapshot["audit"]
        )
    result = dict(
        created_at=datetime.now(timezone.utc).isoformat(),
        source=str(args.source.resolve()),
        source_sha256=game["source_sha256"],
        device=args.device,
        game_id=game["game_id"],
        job_id=job_id,
        status=status,
        attempts=job["attempts"],
        injected_transient=args.inject_transient,
        worker_invocations=len(invocations),
        user_interventions_after_upload=0,
        track_samples=len(tracks),
        processed_frames=report["processed_frames"],
        decoded_frames=report["decoded_frames"],
        inference_fps=report["inference_fps"],
        analysis_elapsed_seconds=report["elapsed_seconds"],
        basketball_status=report["basketball_status"],
        ball_frames=report.get("basketball", {}).get("frames_with_ball")
        if report.get("basketball")
        else None,
        clips=checks,
        persisted_after_restart=True,
        audit_actions=[a["action"] for a in audit],
    )
    (args.output / "acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
