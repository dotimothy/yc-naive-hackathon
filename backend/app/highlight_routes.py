"""Private highlight review and regeneration, using already processed tracking evidence."""

from copy import deepcopy
from typing import Literal, Optional
import uuid
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from workers.video.reviewed_stats import attach_basketball_stats
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
    attach_basketball_stats(bundle)
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
    samples = s.corrected_samples(job["job_id"])
    stride = job.get("options", {}).get("stride", 5)
    from backend.app.shot_routes import effective_shots

    return build_highlights(
        samples,
        game["duration_ms"],
        game["fps"],
        stride,
        job["job_id"],
        shots=effective_shots(job["job_id"]),
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
            evidence = s.corrected_samples(id)
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
        "visual_activity",
        "made_shot",
        "missed_shot",
        "made_free_throw",
        "missed_free_throw",
        "assist",
        "rebound",
        "steal",
        "block",
        "turnover",
    ] = "visual_activity"
    event_time_ms: Optional[int] = Field(default=None, ge=0)
    points: Optional[Literal[1, 2, 3]] = None
    actor_confirmed: bool = False
    distinct_event_confirmed: bool = False


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
        if body.decision == "approved":
            if (
                body.event_time_ms is not None
                and not candidate["start_ms"]
                <= body.event_time_ms
                < candidate["end_ms"]
            ):
                raise HTTPException(
                    422, "Event time must lie inside this candidate source range"
                )
            if body.points is not None and body.event_type not in (
                "made_shot",
                "made_free_throw",
            ):
                raise HTTPException(422, "Only a confirmed made shot can add points")
            if body.event_type == "made_free_throw" and body.points not in (None, 1):
                raise HTTPException(422, "A made free throw is one point")
        candidate["review"] = (
            body.model_dump()
            if body.decision == "approved"
            else dict(decision=body.decision, event_type="visual_activity")
        )
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
            occupied = set()
            for track in group.track_ids:
                times = set(bundle["track_stats"].get(track, {}).get("timestamps", []))
                if occupied.intersection(times):
                    raise HTTPException(
                        422,
                        "These fragments overlap and are visible at the same source time; they cannot be confirmed as one player",
                    )
                occupied.update(times)
            for track in group.track_ids:
                if track not in bundle["track_stats"]:
                    raise HTTPException(
                        422, "Unknown track in current analysis: " + track
                    )
                if track in seen:
                    raise HTTPException(422, "Track assigned more than once: " + track)
                seen.add(track)
        groups = [g.model_dump() for g in body.players]
        previous = {
            t: g["player_id"]
            for g in bundle.get("roster_groups", [])
            for t in g["track_ids"]
        }
        updated = {t: g["player_id"] for g in groups for t in g["track_ids"]}
        for candidate in bundle["candidates"]:
            track = candidate["track_id"]
            if (
                previous.get(track) != updated.get(track)
                and candidate.get("review", {}).get("decision") == "approved"
            ):
                candidate["review"].update(decision="pending", actor_confirmed=False)
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


class SplitTrack(BaseModel):
    track_id: str = Field(min_length=1, max_length=100)
    split_ms: int = Field(ge=0)


def change_splits(game_id, body=None):
    """Source batches are immutable; corrections rebuild all derived attribution."""
    s = store()
    with s.LOCK:
        s, game, job = context(game_id)
        id = job["job_id"]
        bundle = deepcopy(bundle_for(game, job))
        assert_editable(bundle)
        if any(
            j["game_id"] == game_id and j["status"] in ("queued", "running")
            for j in s.all_records("basketball_job")
        ):
            raise HTTPException(
                409, "Wait for ball/hoop analysis before correcting identities"
            )
        raw = [
            v for b in s.all_records("batch") if b["job_id"] == id for v in b["items"]
        ]
        correction = next(
            (c for c in s.all_records("identity_correction") if c["job_id"] == id),
            dict(job_id=id, revision=0, splits=[]),
        )
        if body is None and not correction["splits"]:
            return public_highlights(bundle)
        splits = list(correction["splits"]) if body else []
        if body:
            times = sorted(
                {v["timestamp_ms"] for v in raw if str(v["track_id"]) == body.track_id}
            )
            if not times or not times[0] < body.split_ms < times[-1]:
                raise HTTPException(
                    422, "Seek inside the selected track before splitting"
                )
            boundary = (
                min(times[1:-1], key=lambda t: abs(t - body.split_ms))
                if len(times) > 2
                else None
            )
            if boundary is None:
                raise HTTPException(
                    422, "This track needs at least three observations to split"
                )
            split = dict(track_id=body.track_id, split_ms=boundary)
            if split in splits:
                raise HTTPException(409, "This split already exists")
            splits.append(split)
        from workers.video.identity_corrections import apply_track_splits

        try:
            corrected = apply_track_splits(raw, splits)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        revision = correction["revision"] + 1
        s.put(
            "identity_correction", id, dict(job_id=id, revision=revision, splits=splits)
        )
        # Clear all player mappings and reviews rather than silently move facts to a different person.
        fresh = build_highlights(
            corrected,
            game["duration_ms"],
            game["fps"],
            job.get("options", {}).get("stride", 5),
            id,
            max_clips=bundle["max_clips"],
            top_per_player=bundle["top_per_player"],
            shots=bundle.get("shots"),
        )
        fresh["identity_revision"] = revision
        s.put("highlights", id, fresh)
        s.audit(
            game_id, id, "identity_fragments_corrected", "revision " + str(revision)
        )
        s.update(game_id, id, "highlights.updated", public_highlights(fresh))
        return public_highlights(fresh)


@router.post("/identity/splits")
def split_track(game_id: str, body: SplitTrack):
    return change_splits(game_id, body)


@router.delete("/identity/splits")
def undo_splits(game_id: str):
    return change_splits(game_id)
