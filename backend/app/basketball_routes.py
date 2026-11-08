"""Optional local ball/hoop worker, isolated from core pose dependencies."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from workers.video.highlights import summarize, clear_clips
from backend.app.highlight_routes import bundle_for, public_highlights, assert_editable

router = APIRouter(prefix="/api/games/{game_id}/basketball")


def store():
    from backend.app import main

    return main


def latest(game_id):
    parent = store().latest_job_id(game_id)
    jobs = [
        j
        for j in store().all_records("basketball_job")
        if j["game_id"] == game_id and j.get("parent_job_id") == parent
    ]
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
            run_id=s.latest_job_id(game_id),
            status="running"
            if s.latest_job_id(game_id)
            and s.get("job", s.latest_job_id(game_id))["status"]
            in ("queued", "running")
            else "not_analyzed",
            error=None,
            report=None,
            parent_job_id=s.latest_job_id(game_id),
        )
    )


@router.post("", status_code=202)
def analyze(
    game_id: str,
    body: Analysis,
    idempotency_key: str = Header(..., min_length=1, max_length=200),
):
    raise HTTPException(
        410, "Ball and player analysis now run together. Use Analyze footage."
    )


@router.get("/objects")
def objects(game_id: str):
    s = store()
    s.get("game", game_id)
    job = latest(game_id)
    if not job or job["status"] not in ("running", "succeeded"):
        return {
            "items": [],
            "run_id": job.get("run_id") if job else None,
            "parent_job_id": s.latest_job_id(game_id),
        }
    if job.get("integrated") and job["status"] == "running":
        return {
            "run_id": job["run_id"],
            "parent_job_id": job["parent_job_id"],
            "items": [
                o
                for b in s.all_records("basketball_batch")
                if b["job_id"] == job["parent_job_id"]
                for o in b["items"]
            ],
        }
    path = Path(job["output_dir"]) / "objects.json"
    return {
        "run_id": job["run_id"],
        "parent_job_id": job["parent_job_id"],
        "items": [
            o
            for o in json.loads(path.read_text())
            if o["class_name"] in ("basketball", "hoop")
        ],
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
