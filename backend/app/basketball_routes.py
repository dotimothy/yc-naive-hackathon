"""Optional local ball/hoop worker, isolated from core pose dependencies."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Optional
import uuid
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from workers.video.runtime import select_device
from workers.video.highlights import summarize, clear_clips
from backend.app.highlight_routes import bundle_for, public_highlights, assert_editable

router = APIRouter(prefix="/api/games/{game_id}/basketball")


def store():
    from backend.app import main

    return main


def latest(game_id):
    jobs = [j for j in store().all_records("basketball_job") if j["game_id"] == game_id]
    return max(jobs, key=lambda j: j["created_at"]) if jobs else None


def public(job):
    result = {
        k: v
        for k, v in job.items()
        if k not in ("output_dir", "options", "source_path")
    }
    result["identity_stale"] = job.get(
        "identity_revision", 0
    ) != store().identity_revision(job.get("parent_job_id"))
    return result


def set_state(id, **changes):
    s = store()
    job = s.get("basketball_job", id)
    job.update(changes)
    s.put("basketball_job", id, job)
    s.update(
        job["game_id"], job.get("parent_job_id"), "basketball.updated", public(job)
    )
    return job


def run(id):
    s = store()
    job = s.get("basketball_job", id)
    out = Path(job["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    set_state(id, status="running")
    try:
        options = job["options"]
        python = os.environ.get(
            "SCOUTFILM_BALL_PYTHON", str(s.ROOT / ".venv-ball/bin/python")
        )
        command = [
            python,
            str(s.ROOT / "scripts/analyze_basketball.py"),
            job["source_path"],
            "--output",
            str(out),
            "--device",
            options["device"],
            "--stride",
            str(options["stride"]),
            "--ball-confidence",
            str(options["ball_confidence"]),
            "--hoop-confidence",
            str(options["hoop_confidence"]),
            "--limit-seconds",
            str(options["max_seconds"]),
        ]
        if job.get("parent_job_id"):
            samples = s.corrected_samples(job["parent_job_id"])
            evidence = out / "players"
            evidence.mkdir(exist_ok=True)
            (evidence / "tracks.json").write_text(json.dumps(samples))
            (evidence / "report.json").write_text(
                json.dumps(dict(source_sha256=job["source_sha256"]))
            )
            command.extend(["--players", str(evidence / "tracks.json")])
        with (out / "worker.log").open("w") as log:
            subprocess.run(
                command,
                cwd=s.ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=True,
            )
        report = json.loads((out / "report.json").read_text())
        if report["source_sha256"] != job["source_sha256"]:
            raise ValueError("Ball evidence source mismatch")
        set_state(id, status="succeeded", report=report, error=None)
        if job.get("parent_job_id"):
            s.audit(
                job["game_id"],
                job["parent_job_id"],
                "basketball_evidence_generated",
                "succeeded",
            )
    except Exception as exc:
        set_state(
            id,
            status="failed",
            error="Ball/hoop worker failed ("
            + type(exc).__name__
            + "). Check the local worker log and optional detector setup.",
        )


class Analysis(BaseModel):
    device: Optional[str] = Field(default=None, max_length=20)
    stride: int = Field(default=2, ge=1, le=5)
    ball_confidence: float = Field(default=0.25, gt=0, le=1)
    hoop_confidence: float = Field(default=0.35, gt=0, le=1)
    max_seconds: int = Field(default=60, ge=1, le=120)


@router.get("")
def get_report(game_id: str):
    s = store()
    s.get("game", game_id)
    job = latest(game_id)
    return (
        public(job)
        if job
        else dict(
            run_id=None,
            status="not_analyzed",
            error=None,
            report=None,
            parent_job_id=None,
        )
    )


@router.post("", status_code=202)
def analyze(
    game_id: str,
    body: Analysis,
    idempotency_key: str = Header(..., min_length=1, max_length=200),
):
    s = store()
    with s.LOCK:
        game = s.get("game", game_id)
        parent = s.latest_job_id(game_id)
        if parent and s.get("job", parent)["status"] != "succeeded":
            raise HTTPException(409, "Finish pose analysis before ball/hoop analysis")
        if any(
            b["job_id"] == parent and b["status"] in ("queued", "running")
            for b in s.all_records("highlights")
        ):
            raise HTTPException(409, "Wait for highlight generation")
        try:
            device = select_device(body.device)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        interpreter = Path(
            os.environ.get(
                "SCOUTFILM_BALL_PYTHON", str(s.ROOT / ".venv-ball/bin/python")
            )
        )
        if not interpreter.is_file():
            raise HTTPException(
                503,
                "Set up the optional detector first: scripts/setup_ball_detector.sh",
            )
        if not (s.ROOT / "data/models/ebard-rfdetr-nano-state.pth").is_file():
            raise HTTPException(
                503,
                "Download the pinned basketball model first: scripts/download_basketball_model.py",
            )
        options = body.model_dump()
        options["device"] = device
        serialized = json.dumps(
            dict(
                options=options,
                parent_job_id=parent,
                identity_revision=s.identity_revision(parent),
            ),
            sort_keys=True,
        )
        request_scope = game_id + ":basketball"
        with s.connect() as db:
            row = db.execute(
                "SELECT job_id,body FROM requests WHERE game_id=? AND key=?",
                (request_scope, idempotency_key),
            ).fetchone()
            if row:
                if row["body"] != serialized:
                    raise HTTPException(
                        409,
                        "Request key already used with different ball analysis settings",
                    )
                return public(s.get("basketball_job", row["job_id"]))
            active = latest(game_id)
            if active and active["status"] in ("queued", "running"):
                return public(active)
            id = uuid.uuid4().hex
            job = dict(
                run_id=id,
                game_id=game_id,
                parent_job_id=parent,
                identity_revision=s.identity_revision(parent),
                source_sha256=game["source_sha256"],
                source_path=game["path"],
                created_at=time.time(),
                status="queued",
                error=None,
                report=None,
                options=options,
                output_dir=str(s.DATA / "processed" / "basketball" / id),
            )
            s.put("basketball_job", id, job)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?)",
                (request_scope, idempotency_key, id, serialized),
            )
        s.POOL.submit(run, id)
        return public(job)


@router.get("/objects")
def objects(game_id: str):
    s = store()
    s.get("game", game_id)
    job = latest(game_id)
    if not job or job["status"] != "succeeded":
        return {"items": []}
    path = Path(job["output_dir"]) / "objects.json"
    return {
        "items": [
            o
            for o in json.loads(path.read_text())
            if o["class_name"] in ("basketball", "hoop")
        ]
    }


class ImportCandidate(BaseModel):
    track_id: str = Field(min_length=1, max_length=80)
    actor_confirmed: bool = False


@router.post("/candidates/{candidate_id}/review", status_code=201)
def import_candidate(game_id: str, candidate_id: str, body: ImportCandidate):
    s = store()
    with s.LOCK:
        job = latest(game_id)
        parent = s.latest_job_id(game_id)
        if not job or job["status"] != "succeeded":
            raise HTTPException(409, "Finish ball/hoop analysis first")
        if (
            job["parent_job_id"] != parent
            or not parent
            or job.get("identity_revision", 0) != s.identity_revision(parent)
        ):
            raise HTTPException(
                409, "Run ball/hoop analysis again for the current player tracks"
            )
        analysis = s.get("job", parent)
        if analysis["status"] != "succeeded":
            raise HTTPException(409, "Finish player tracking first")
        proposal = next(
            (
                c
                for c in job["report"]["shot_candidates"]
                if c["candidate_id"] == candidate_id
            ),
            None,
        )
        if not proposal:
            raise HTTPException(
                404, "Shot candidate not found in current ball analysis"
            )
        bundle = deepcopy(bundle_for(s.get("game", game_id), analysis))
        assert_editable(bundle)
        if body.track_id not in bundle["track_stats"]:
            raise HTTPException(422, "Select a track from the current analysis")
        if not body.actor_confirmed:
            raise HTTPException(
                422, "Confirm the actor assignment after checking the source"
            )
        id = "ball-" + job["run_id"] + "-" + candidate_id
        if not any(c["candidate_id"] == id for c in bundle["candidates"]):
            score = round(proposal["evidence_quality"] * 100, 2)
            bundle["candidates"].append(
                dict(
                    candidate_id=id,
                    track_id=body.track_id,
                    player_id="track-" + body.track_id,
                    start_ms=proposal["start_ms"],
                    end_ms=proposal["end_ms"],
                    action_start_ms=proposal["evidence_timestamps"][0],
                    action_end_ms=proposal["approach_ms"],
                    score=score,
                    automatic_score=score,
                    components={"ball_detection_support": score},
                    reasons=["Ball/hoop trajectory candidate; outcome unverified"],
                    review_status="pending",
                    event_type="visual_activity",
                    origin="visual_heuristic",
                    selected=False,
                    clip_id=None,
                    clip_uri=None,
                    qa_status=None,
                    review=dict(
                        decision="pending",
                        event_type="visual_activity",
                        event_time_ms=proposal["approach_ms"],
                        actor_confirmed=True,
                    ),
                    basketball_evidence=proposal,
                )
            )
            summarize(clear_clips(bundle))
            s.put("highlights", parent, bundle)
            s.audit(game_id, parent, "shot_candidate_added_for_review", id)
        return public_highlights(bundle)
