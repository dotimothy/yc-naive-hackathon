"""Source-time camera shots and explicit gameplay review; source media is retained."""

from copy import deepcopy
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from backend.app.highlight_routes import (
    context,
    bundle_for,
    assert_editable,
    public_highlights,
)
from workers.video.highlights import build_highlights, summarize, clear_clips

router = APIRouter(prefix="/api/games/{game_id}/shots")


def store():
    from backend.app import main

    return main


def effective_shots(job_id):
    s = store()
    records = [r for r in s.all_records("shots") if r["job_id"] == job_id]
    report = s.get("job", job_id).get("report") or {}
    items = records[-1]["items"] if records else report.get("shots")
    if items is None:
        return None
    reviews = {
        r["shot_id"]: r for r in s.all_records("shot_review") if r["job_id"] == job_id
    }
    return [
        dict(
            shot,
            shot_id=str(shot["shot_id"]),
            review_status=reviews.get(str(shot["shot_id"]), {}).get(
                "review_status", "pending"
            ),
        )
        for shot in items
    ]


def response(game_id):
    s = store()
    s.get("game", game_id)
    job_id = s.latest_job_id(game_id)
    return dict(
        job_id=job_id,
        status=s.get("job", job_id)["status"] if job_id else "not_analyzed",
        items=effective_shots(job_id) or [] if job_id else [],
    )


@router.get("")
def get_shots(game_id: str):
    return response(game_id)


class Review(BaseModel):
    job_id: str = Field(min_length=1, max_length=100)
    decision: Literal["gameplay", "non_gameplay", "pending"]


@router.put("/{shot_id}/review")
def review_shot(game_id: str, shot_id: str, body: Review):
    s = store()
    with s.LOCK:
        s, game, job = context(game_id)
        if body.job_id != job["job_id"]:
            raise HTTPException(
                409, "Camera segments belong to a different analysis; refresh first"
            )
        items = effective_shots(job["job_id"])
        if not items or not any(v["shot_id"] == shot_id for v in items):
            raise HTTPException(404, "Camera segment not found in current analysis")
        old = deepcopy(bundle_for(game, job))
        assert_editable(old)
        status = {
            "gameplay": "confirmed_gameplay",
            "non_gameplay": "confirmed_non_gameplay",
            "pending": "pending",
        }[body.decision]
        s.put(
            "shot_review",
            job["job_id"] + ":" + shot_id,
            dict(job_id=job["job_id"], shot_id=shot_id, review_status=status),
        )
        items = effective_shots(job["job_id"])
        fresh = build_highlights(
            s.corrected_samples(job["job_id"]),
            game["duration_ms"],
            game["fps"],
            job.get("options", {}).get("stride", 5),
            job["job_id"],
            max_clips=old["max_clips"],
            top_per_player=old["top_per_player"],
            shots=items,
        )
        summarize(
            clear_clips(fresh),
            groups=old.get("roster_groups", []),
            reviews={c["candidate_id"]: c.get("review", {}) for c in old["candidates"]},
        )
        s.put("highlights", job["job_id"], fresh)
        s.audit(game_id, job["job_id"], "camera_shot_review", shot_id + ":" + status)
        s.update(game_id, job["job_id"], "highlights.updated", public_highlights(fresh))
        return response(game_id)


class Export(BaseModel):
    job_id: str = Field(min_length=1, max_length=100)


@router.post("/{shot_id}/clip", status_code=201)
def export_shot(game_id: str, shot_id: str, body: Export):
    from workers.render.media import render_clip, verify_clip
    import uuid

    s = store()
    with s.LOCK:
        s, game, job = context(game_id)
        if body.job_id != job["job_id"]:
            raise HTTPException(
                409, "Camera segments belong to another analysis; refresh first"
            )
        shot = next(
            (
                v
                for v in effective_shots(job["job_id"]) or []
                if v["shot_id"] == shot_id
            ),
            None,
        )
        if not shot:
            raise HTTPException(404, "Camera segment not found")
        clip_id = job["job_id"] + "-camera-" + shot_id
        try:
            return s.get("clip", clip_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    folder = s.DATA / "processed" / job["job_id"] / "camera_shots"
    folder.mkdir(parents=True, exist_ok=True)
    path = render_clip(
        game["path"],
        shot["start_ms"],
        shot["end_ms"],
        folder / (uuid.uuid4().hex + ".mp4"),
    )
    qa = verify_clip(game["path"], path, shot["start_ms"], shot["end_ms"])
    with s.LOCK:
        if s.latest_job_id(game_id) != job["job_id"]:
            path.unlink(missing_ok=True)
            raise HTTPException(409, "Analysis changed while exporting; refresh first")
        try:
            current = s.get("clip", clip_id)
            path.unlink(missing_ok=True)
            return current
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        clip = dict(
            clip_id=clip_id,
            event_id=clip_id + "-event",
            game_id=game_id,
            kind="camera_shot",
            shot_id=shot_id,
            source_start_ms=shot["start_ms"],
            source_end_ms=shot["end_ms"],
            path=str(path),
            identity_verified=False,
            **qa,
        )
        emit = s.output_emitter(game_id, job["job_id"], job["attempts"])
        emit("clip.updated", clip)
        emit(
            "event.created",
            dict(
                event_id=clip["event_id"],
                game_id=game_id,
                start_ms=shot["start_ms"],
                end_ms=shot["end_ms"],
                event_type="camera_shot_review",
                origin="review_window",
                verified=False,
                track_id=None,
            ),
        )
        s.audit(game_id, job["job_id"], "camera_shot_clip_exported", shot_id)
        return clip
