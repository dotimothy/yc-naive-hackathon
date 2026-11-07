"""Private highlight review and regeneration, using already processed tracking evidence."""

from copy import deepcopy
from typing import Literal
import uuid
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from workers.video.highlights import (
    build_highlights,
    summarize,
    clear_clips,
    render_highlights,
    SCORING_VERSION,
    add_spotlights,
)

router = APIRouter(prefix="/api/games/{game_id}/highlights")


def store():
    from backend.app import main

    return main


def public_highlights(bundle):
    public = {
        k: v
        for k, v in bundle.items()
        if k not in ("track_stats", "sample_interval_ms")
    }
    public["candidates"] = [
        {k: v for k, v in candidate.items() if k != "spotlight"}
        for candidate in bundle["candidates"]
    ]
    return public


def context(game_id):
    s = store()
    game = s.get("game", game_id)
    id = s.latest_job_id(game_id)
    if not id:
        raise HTTPException(409, "Analyze this game first")
    job = s.get("job", id)
    if job["status"] != "succeeded":
        raise HTTPException(409, "Wait for a successful analysis")
    return s, game, job


def bundle_for(game, job):
    s = store()
    try:
        return s.get("highlights", job["job_id"])
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    samples = [
        sample
        for batch in s.all_records("batch")
        if batch["job_id"] == job["job_id"]
        for sample in batch["items"]
    ]
    stride = job.get("options", {}).get("stride", 5)
    return build_highlights(
        samples, game["duration_ms"], game["fps"], stride, job["job_id"]
    )


def assert_editable(bundle):
    if bundle["status"] in ("queued", "running"):
        raise HTTPException(409, "Highlight generation is active; wait before editing")


@router.get("")
def get_highlights(game_id: str):
    s = store()
    s.get("game", game_id)
    id = s.latest_job_id(game_id)
    if not id:
        return dict(
            job_id=None,
            generation_id=None,
            status="not_generated",
            error=None,
            scoring_version=SCORING_VERSION,
            score_kind="highlight_priority",
            players=[],
            candidates=[],
            roster_groups=[],
            max_clips=8,
            top_per_player=2,
        )
    try:
        return public_highlights(s.get("highlights", id))
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    game = s.get("game", game_id)
    job = s.get("job", id)
    if job["status"] == "succeeded":
        return public_highlights(bundle_for(game, job))
    return dict(
        job_id=id,
        generation_id=None,
        status="not_generated",
        error=None,
        scoring_version=SCORING_VERSION,
        score_kind="highlight_priority",
        players=[],
        candidates=[],
        roster_groups=[],
        max_clips=8,
        top_per_player=2,
    )


class Generate(BaseModel):
    max_clips: int = Field(default=8, ge=1, le=12)
    top_per_player: int = Field(default=2, ge=1, le=3)


def generate(game_id, job_id, bundle):
    s = store()
    game = s.get("game", game_id)
    try:
        emit = s.output_emitter(game_id, job_id, s.get("job", job_id)["attempts"])
        render_highlights(
            game["path"],
            s.DATA / "processed" / job_id / bundle["generation_id"],
            bundle,
            game_id,
            emit,
        )
        s.audit(game_id, job_id, "highlights_generated", "succeeded")
    except Exception as exc:
        bundle.update(
            status="failed", error="Highlight generation failed: " + type(exc).__name__
        )
        s.put("highlights", job_id, bundle)
        s.update(game_id, job_id, "highlights.updated", public_highlights(bundle))
        s.audit(game_id, job_id, "highlights_generated", "failed")
        import traceback

        traceback.print_exc()


@router.post("", status_code=202)
def generate_highlights(
    game_id: str,
    body: Generate,
    idempotency_key: str = Header(..., min_length=1, max_length=200),
):
    s = store()
    with s.LOCK:
        s, game, job = context(game_id)
        bundle = deepcopy(bundle_for(game, job))
        id = job["job_id"]
        request_key = game_id + ":highlights:" + id
        options = body.model_dump_json()
        with s.connect() as db:
            row = db.execute(
                "SELECT job_id,body FROM requests WHERE game_id=? AND key=?",
                (request_key, idempotency_key),
            ).fetchone()
            if row:
                if row["body"] != options:
                    raise HTTPException(
                        409, "Request key used for different highlight options"
                    )
                # Never create a second generation on replay, even if review has since invalidated it.
                return public_highlights(bundle)
            if bundle["status"] in ("queued", "running"):
                return public_highlights(bundle)
            bundle.update(
                max_clips=body.max_clips,
                top_per_player=body.top_per_player,
                generation_id=uuid.uuid4().hex,
            )
            evidence = [
                sample
                for batch in s.all_records("batch")
                if batch["job_id"] == id
                for sample in batch["items"]
            ]
            add_spotlights(bundle, evidence)
            summarize(clear_clips(bundle))
            bundle["status"] = "queued"
            s.put("highlights", id, bundle)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?)",
                (request_key, idempotency_key, bundle["generation_id"], options),
            )
        s.POOL.submit(generate, game_id, id, deepcopy(bundle))
        return public_highlights(bundle)


class Review(BaseModel):
    decision: Literal["approved", "rejected", "pending"]
    event_type: Literal[
        "visual_activity", "made_shot", "assist", "rebound", "steal", "block"
    ] = "visual_activity"


@router.put("/candidates/{candidate_id}/review")
def review_candidate(game_id: str, candidate_id: str, body: Review):
    s = store()
    with s.LOCK:
        s, game, job = context(game_id)
        bundle = deepcopy(bundle_for(game, job))
        assert_editable(bundle)
        candidate = next(
            (c for c in bundle["candidates"] if c["candidate_id"] == candidate_id), None
        )
        if not candidate:
            raise HTTPException(404, "Candidate is not from the current analysis")
        if body.decision != "approved" and body.event_type != "visual_activity":
            raise HTTPException(422, "Basketball outcomes require an approved review")
        candidate["review"] = body.model_dump()
        summarize(clear_clips(bundle))
        s.put("highlights", job["job_id"], bundle)
        s.audit(
            game_id, job["job_id"], "highlight_review_" + body.decision, candidate_id
        )
        s.update(
            game_id, job["job_id"], "highlights.updated", public_highlights(bundle)
        )
        return public_highlights(bundle)


class PlayerGroup(BaseModel):
    player_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=80)
    track_ids: list[str] = Field(min_length=1, max_length=200)
    confirmed: Literal[True]


class Groups(BaseModel):
    players: list[PlayerGroup] = Field(max_length=100)


@router.put("/players")
def map_players(game_id: str, body: Groups):
    s = store()
    with s.LOCK:
        s, game, job = context(game_id)
        bundle = deepcopy(bundle_for(game, job))
        assert_editable(bundle)
        seen = set()
        ids = set()
        for group in body.players:
            if group.player_id.startswith("track-") or group.player_id in ids:
                raise HTTPException(
                    422, "Use unique player group IDs outside the track- namespace"
                )
            ids.add(group.player_id)
            for track in group.track_ids:
                if track not in bundle["track_stats"]:
                    raise HTTPException(
                        422, "Unknown track in current analysis: " + track
                    )
                if track in seen:
                    raise HTTPException(422, "Track assigned more than once: " + track)
                seen.add(track)
        groups = [g.model_dump() for g in body.players]
        summarize(clear_clips(bundle), groups=groups)
        s.put("highlights", job["job_id"], bundle)
        s.audit(
            game_id,
            job["job_id"],
            "private_player_mapping_updated",
            "confirmed_by_reviewer",
        )
        s.update(
            game_id, job["job_id"], "highlights.updated", public_highlights(bundle)
        )
        return public_highlights(bundle)
