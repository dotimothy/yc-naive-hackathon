"""Loopback-only prototype API. SQLite persists jobs, output and replayable updates."""

from __future__ import annotations
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from workers.render.media import probe, sha256
from workers.video.cancellation import MARKER, check_cancelled
from workers.video.coordinated import run_analysis as process_video
from workers.video.runtime import available_devices, select_device, POSE_MODEL
from backend.app.highlight_routes import router as highlight_router, public_highlights
from backend.app.basketball_routes import router as basketball_router
from backend.app.shot_routes import router as shot_router
from backend.app.thumbnail_routes import router as thumbnail_router
from workers.video.sports.basketball import OUTDOOR_SAMPLE_COURT

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("SCOUTFILM_DATA", ROOT / "data/local")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "scoutfilm.sqlite3"
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.RLock()


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS updates(sequence INTEGER PRIMARY KEY AUTOINCREMENT,game_id TEXT,job_id TEXT,kind TEXT,payload TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS requests(game_id TEXT,key TEXT,job_id TEXT,body TEXT,PRIMARY KEY(game_id,key));
        """)


def put(kind, id, value):
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO records VALUES(?,?,?)",
            (kind, id, json.dumps(value)),
        )
    return value


def get(kind, id):
    with connect() as db:
        row = db.execute(
            "SELECT value FROM records WHERE kind=? AND id=?", (kind, id)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Record not found")
    return json.loads(row["value"])


def all_records(kind):
    with connect() as db:
        return [
            json.loads(r["value"])
            for r in db.execute(
                "SELECT value FROM records WHERE kind=? ORDER BY rowid", (kind,)
            )
        ]


def update(game, job, kind, payload):
    with connect() as db:
        db.execute(
            "INSERT INTO updates(game_id,job_id,kind,payload,created) VALUES(?,?,?,?,?)",
            (game, job, kind, json.dumps(payload), time.time()),
        )


def public_job(job):
    return {k: v for k, v in job.items() if k not in ("options",)}


def set_job(id, **changes):
    job = get("job", id)
    job.update(changes)
    put("job", id, job)
    update(job["game_id"], id, "job.updated", public_job(job))
    return job


def audit(game, job, action, outcome, attempt=1):
    item = dict(
        id=uuid.uuid4().hex,
        job_id=job,
        actor="local-worker",
        action=action,
        timestamp=datetime.now(timezone.utc).isoformat(),
        attempt=attempt,
        outcome=outcome,
    )
    put("audit", item["id"], item)
    update(game, job, "audit.created", item)


def artifact(path, kind, game):
    path = Path(path).resolve()
    if not path.is_relative_to(DATA):
        raise ValueError("Artifact outside storage")
    id = uuid.uuid4().hex
    digest = sha256(path)
    put("artifact", id, dict(path=str(path), kind=kind, game_id=game, sha256=digest))
    return dict(artifact_id=id, kind=kind, uri="/api/media/" + id, sha256=digest)


def output_emitter(game_id, id, attempt):
    game = get("game", game_id)

    def emit(kind, payload):
        check_cancelled(DATA / "processed" / id / MARKER)
        if kind == "phase":
            set_job(id, phase=payload["phase"])
        elif kind == "tracks.batch":
            # Timestamp-keyed batches replace retries; reconnect stream retains attempt history.
            payload["attempt"] = attempt
            put(
                "batch",
                id + ":" + str(payload["source_frame"]),
                dict(job_id=id, game_id=game_id, **payload),
            )
            update(game_id, id, kind, payload)
        elif kind == "analysis.progress":
            payload["attempt"] = attempt
            update(game_id, id, kind, payload)
        elif kind in ("basketball.updated", "basketball.batch"):
            try:
                current = get("basketball_job", id)
            except HTTPException:
                current = dict(
                    run_id=id,
                    game_id=game_id,
                    parent_job_id=id,
                    identity_revision=identity_revision(id),
                    integrated=True,
                    created_at=time.time(),
                    source_sha256=game["source_sha256"],
                    output_dir=str(DATA / "processed" / id / "basketball"),
                    status="running",
                    error=None,
                    report=None,
                )
            if kind == "basketball.batch":
                put(
                    "basketball_batch",
                    id + ":" + str(payload["processed_frames"]),
                    dict(job_id=id, attempt=attempt, **payload),
                )
                report = current.get("report") or {}
                report.update(
                    processed_frames=payload["processed_frames"],
                    frames_with_ball=payload["frames_with_ball"],
                    analyzed_until_ms=payload["source_timestamp_ms"],
                )
                for timing in ("elapsed_seconds", "detector_seconds"):
                    if timing in payload:
                        report[timing] = payload[timing]
                current["report"] = report
            else:
                current.update(payload)
            put("basketball_job", id, current)
            from backend.app.basketball_routes import public

            update(game_id, id, "basketball.updated", public(current))
        elif kind == "shots.updated":
            put("shots", id, dict(job_id=id, **payload))
            update(game_id, id, kind, payload)
        elif kind == "tracks.enriched":
            put("track_metadata", id, dict(job_id=id, **payload))
            update(game_id, id, kind, payload)
        elif kind == "clip.updated":
            payload["attempt"] = attempt
            path = payload.pop("path")
            registered = artifact(path, "clip", game_id)
            payload.update(uri=registered["uri"])
            put("clip", payload["clip_id"], dict(job_id=id, **payload))
            current = get("job", id)
            current["artifacts"].append(registered)
            put("job", id, current)
            update(game_id, id, kind, payload)
        elif kind == "event.created":
            payload["attempt"] = attempt
            clip = next(
                (
                    c
                    for c in reversed(all_records("clip"))
                    if c["event_id"] == payload["event_id"]
                ),
                None,
            )
            payload["clip_uri"] = clip["uri"] if clip else None
            payload["evidence_uri"] = game["source_uri"]
            put("event", payload["event_id"], dict(job_id=id, **payload))
            update(game_id, id, kind, payload)

        elif kind == "highlights.updated":
            put("highlights", id, payload)
            update(game_id, id, kind, public_highlights(payload))

    return emit


def finish_cancellation(id):
    """Only publish terminal cancellation after the worker stops and outputs are hidden."""
    with LOCK:
        job = get("job", id)
        with connect() as db:
            for row in db.execute(
                "SELECT kind,id,value FROM records WHERE kind IN ('batch','clip','event','highlights','track_metadata','basketball_batch','report','shots')"
            ).fetchall():
                value = json.loads(row["value"])
                if value.get("job_id") == id or (
                    row["kind"] == "report" and row["id"] == id
                ):
                    db.execute(
                        "DELETE FROM records WHERE kind=? AND id=?",
                        (row["kind"], row["id"]),
                    )
            for artifact in job.get("artifacts", []):
                db.execute(
                    "DELETE FROM records WHERE kind='artifact' AND id=?",
                    (artifact["artifact_id"],),
                )
        for basketball in all_records("basketball_job"):
            if basketball.get("parent_job_id") == id:
                basketball.update(status="cancelled", report=None, error=None)
                put("basketball_job", basketball["run_id"], basketball)
                update(
                    job["game_id"],
                    id,
                    "basketball.updated",
                    {
                        k: v
                        for k, v in basketball.items()
                        if k not in ("output_dir", "source_path", "options")
                    },
                )
        set_job(
            id,
            status="cancelled",
            phase="cancelled",
            error=None,
            report=None,
            artifacts=[],
        )
        audit(job["game_id"], id, "analysis_cancelled", "cancelled", job["attempts"])


def run_job(id):
    job = get("job", id)
    game = get("game", job["game_id"])
    game_id = game["game_id"]
    for attempt in range(1, 4):
        with LOCK:
            if get("job", id)["status"] == "cancelled":
                return
            if (DATA / "processed" / id / MARKER).exists():
                finish_cancellation(id)
                return
            set_job(id, status="running", attempts=attempt, error=None, phase="track")
        audit(game_id, id, "analysis_started", "running", attempt)

        emit = output_emitter(game_id, id, attempt)

        try:
            # Each retry replaces this job's materialized output; the append-only audit retains history.
            with connect() as db:
                for row in db.execute(
                    "SELECT kind,id,value FROM records WHERE kind IN ('batch','clip','event','highlights','track_metadata','basketball_batch','shots')"
                ).fetchall():
                    if json.loads(row["value"]).get("job_id") == id:
                        db.execute(
                            "DELETE FROM records WHERE kind=? AND id=?",
                            (row["kind"], row["id"]),
                        )
            with connect() as db:
                db.execute(
                    "DELETE FROM records WHERE kind='basketball_job' AND id=?", (id,)
                )
            set_job(id, artifacts=[])
            result = process_video(
                game["path"],
                DATA / "processed" / id,
                dict(game_id=game_id, job_id=id, sport=game["sport"], **job["options"]),
                emit,
            )
            # Do not expose the worker's filesystem paths in browser reports.
            result["clips"] = [
                {k: v for k, v in c.items() if k != "path"} for c in result["clips"]
            ]
            with LOCK:
                check_cancelled(DATA / "processed" / id / MARKER)
                put("report", id, result)
                set_job(id, status="succeeded", phase="complete", report=result)
            audit(game_id, id, "analysis_completed", "succeeded", attempt)
            return
        except Exception as exc:
            if (DATA / "processed" / id / MARKER).exists():
                finish_cancellation(id)
                return
            for basketball in all_records("basketball_job"):
                if (
                    basketball.get("parent_job_id") == id
                    and basketball.get("integrated")
                    and basketball["status"] == "running"
                ):
                    basketball.update(
                        status="failed",
                        error="Coordinated analysis failed; re-run Analyze footage",
                    )
                    put("basketball_job", basketball["run_id"], basketball)
            for bundle in all_records("highlights"):
                if bundle["job_id"] == id and bundle["status"] in (
                    "queued",
                    "running",
                    "cancelling",
                ):
                    bundle.update(
                        status="failed",
                        error="Analysis failed during highlight generation",
                    )
                    put("highlights", id, bundle)
                    update(game_id, id, "highlights.updated", public_highlights(bundle))
            retryable = isinstance(exc, (OSError, subprocess.TimeoutExpired))
            audit(game_id, id, "analysis_error", type(exc).__name__, attempt)
            if retryable and attempt < 3:
                set_job(id, status="queued", error="Transient worker error; retrying")
                time.sleep(attempt)
                continue
            set_job(
                id,
                status="failed",
                error="Analysis failed: "
                + type(exc).__name__
                + ". Check local worker log.",
            )
            import traceback

            traceback.print_exc()
            return


@asynccontextmanager
async def lifespan(app):
    select_device()  # Fail startup on an unavailable explicit runtime device.
    initialize()
    for job in all_records("job"):
        if job["status"] in ("queued", "running", "cancelling"):
            if (DATA / "processed" / job["job_id"] / MARKER).exists():
                finish_cancellation(job["job_id"])
                continue
            set_job(
                job["job_id"],
                status="failed",
                error="Server restarted; submit analysis again with a new request key",
            )
    for bundle in all_records("highlights"):
        if bundle["status"] in ("queued", "running", "cancelling"):
            bundle.update(
                status="failed",
                error="Server restarted during highlight generation; generate again",
            )
            put("highlights", bundle["job_id"], bundle)
    for job in all_records("basketball_job"):
        if job["status"] in ("queued", "running", "cancelling"):
            job.update(
                status="failed",
                error="Server restarted during ball analysis; submit again",
            )
            put("basketball_job", job["run_id"], job)
    yield


app = FastAPI(title="PlayLattice local", lifespan=lifespan)
app.include_router(thumbnail_router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "mode": "local",
        "inference_devices": available_devices(),
        "default_inference_device": select_device(),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
    }


def register_game(path, title):
    try:
        metadata = probe(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise HTTPException(422, "Invalid or unsupported video")
    id = uuid.uuid4().hex
    asset = artifact(path, "source", id)
    return put(
        "game",
        id,
        dict(
            game_id=id,
            sport="basketball",
            title=title,
            source_uri=asset["uri"],
            source_sha256=asset["sha256"],
            path=str(path),
            **metadata,
        ),
    )


def public_game(game):
    return {k: v for k, v in game.items() if k != "path"}


@app.get("/api/games")
def games():
    return {"items": [public_game(g) for g in all_records("game")]}


def ingested_game(game, auto_analyze):
    """Persist the ingest before queueing; client disconnects do not own the worker."""
    if not auto_analyze:
        return public_game(game)
    with LOCK:
        latest = latest_job_id(game["game_id"])
        previous = get("job", latest) if latest else None
        if previous and previous["status"] in (
            "queued",
            "running",
            "cancelling",
            "succeeded",
        ):
            game.pop("analysis_error", None)
            put("game", game["game_id"], game)
            return dict(public_game(game), analysis_job_id=latest)
        key = "ingest:" + game["game_id"] + (":" + latest if latest else "")
        try:
            job = analyze(game["game_id"], Analysis(), idempotency_key=key)
        except (HTTPException, OSError, RuntimeError) as exc:
            # A successful upload remains accessible even if scheduling is unavailable.
            error = (
                exc.detail
                if isinstance(exc, HTTPException) and isinstance(exc.detail, str)
                else "Analysis could not start. Retry analysis from this video."
            )
            game["analysis_error"] = error
            put("game", game["game_id"], game)
            failed_id = latest_job_id(game["game_id"])
            if failed_id and failed_id != latest:
                failed = get("job", failed_id)
                if failed["status"] == "queued":
                    set_job(failed_id, status="failed", error=error)
                    audit(
                        game["game_id"],
                        failed_id,
                        "analysis_enqueue_failed",
                        "failed",
                        0,
                    )
                return dict(public_game(game), analysis_job_id=failed_id)
            return public_game(game)
        game.pop("analysis_error", None)
        put("game", game["game_id"], game)
        return dict(public_game(game), analysis_job_id=job["job_id"])


@app.post("/api/games", status_code=201)
def upload(
    video: UploadFile = File(...),
    rights_confirmed: bool = Form(...),
    sport: str = Form("basketball"),
    auto_analyze: bool = Form(False),
):
    if not rights_confirmed:
        raise HTTPException(422, "Confirm footage rights before upload")
    if sport != "basketball":
        raise HTTPException(422, "Only basketball is supported yet")
    path = DATA / (uuid.uuid4().hex + ".mp4")
    size = 0
    try:
        with path.open("wb") as target:
            while True:
                block = video.file.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > 250 * 1024 * 1024:
                    raise HTTPException(413, "Use a file smaller than 250 MiB")
                target.write(block)
        game = register_game(path, Path(video.filename or "Uploaded game").name)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        video.file.close()
    # Do not remove registered media if analysis queueing subsequently fails.
    return ingested_game(game, auto_analyze)


@app.post("/api/games/sample", status_code=201)
def sample(auto_analyze: bool = False):
    source = ROOT / "data/trackid3x3/outdoor_stationary_12s.mp4"
    outdoor = source.exists()
    if not outdoor:
        source = ROOT / "data/trackid3x3/basket_S1T1_pre.mp4"
    if not source.exists():
        raise HTTPException(404, "Run scripts/download_sample.py first")
    # Concurrent sample requests share one registered game and active/completed job.
    with LOCK:
        for game in all_records("game"):
            if game.get("sample") and game.get("sample_variant") == (
                "outdoor" if outdoor else "indoor"
            ):
                return ingested_game(game, auto_analyze)
        path = DATA / (uuid.uuid4().hex + ".mp4")
        try:
            shutil.copy2(source, path)
            game = register_game(
                path,
                "TrackID3x3 · "
                + ("outdoor full 3x3 court" if outdoor else "indoor fixed camera"),
            )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        game["sample"] = True
        game["sample_variant"] = "outdoor" if outdoor else "indoor"
        if outdoor:
            game["court_polygon"] = OUTDOOR_SAMPLE_COURT
        put("game", game["game_id"], game)
        return ingested_game(game, auto_analyze)


class Cut(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    event_type: str = Field(default="coach_review", max_length=80)

    @model_validator(mode="after")
    def range_valid(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("End must follow start")
        return self


class Analysis(BaseModel):
    basketball: bool = True
    ball_stride: int = Field(default=2, ge=1, le=5)
    device: Optional[str] = Field(default=None, min_length=1, max_length=20)
    candidate_ranges: list[Cut] = Field(default_factory=list, max_length=10)
    replay_paced: bool = False
    stride: int = Field(default=5, ge=1, le=20)
    confidence: float = Field(default=0.25, ge=0.1, le=0.99)


@app.post("/api/games/{game_id}/analyze", status_code=202)
def analyze(
    game_id: str, body: Analysis, idempotency_key: str = Header(..., max_length=200)
):
    game = get("game", game_id)
    options = body.model_dump()
    options["pose_model"] = POSE_MODEL
    try:
        select_device(options["device"])
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if game.get("court_polygon"):
        options["court_polygon"] = game["court_polygon"]
    if any(c["end_ms"] > game["duration_ms"] for c in options["candidate_ranges"]):
        raise HTTPException(422, "Clip range exceeds source duration")
    serialized = json.dumps(options, sort_keys=True)
    with LOCK:
        with connect() as db:
            row = db.execute(
                "SELECT job_id,body FROM requests WHERE game_id=? AND key=?",
                (game_id, idempotency_key),
            ).fetchone()
            if row:
                if row["body"] != serialized:
                    raise HTTPException(
                        409, "Request key was already used with different options"
                    )
                return public_job(get("job", row["job_id"]))
            if any(
                b.get("job_id") == latest_job_id(game_id)
                and b["status"] in ("queued", "running", "cancelling")
                for b in all_records("highlights")
            ):
                raise HTTPException(
                    409, "Wait for highlight generation before re-analysis"
                )
            if any(
                j["game_id"] == game_id
                and j["status"] in ("queued", "running", "cancelling")
                for j in all_records("basketball_job")
            ):
                raise HTTPException(
                    409, "Wait for ball/hoop analysis before replacing player tracking"
                )
            # One active job per game prevents mixed tracking timelines.
            if any(
                j["game_id"] == game_id
                and j["status"] in ("queued", "running", "cancelling")
                for j in all_records("job")
            ):
                raise HTTPException(409, "This game already has an active analysis")
            id = uuid.uuid4().hex
            job = dict(
                job_id=id,
                game_id=game_id,
                status="queued",
                phase="ingest",
                attempts=0,
                error=None,
                artifacts=[],
                options=options,
            )
            put("job", id, job)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?)",
                (game_id, idempotency_key, id, serialized),
            )
        update(game_id, id, "job.updated", public_job(job))
        POOL.submit(run_job, id)
    return public_job(job)


@app.post("/api/jobs/{id}/cancel")
def cancel_analysis(id: str):
    with LOCK:
        job = get("job", id)
        if job["status"] not in ("queued", "running"):
            return public_job(job)
        path = DATA / "processed" / id / MARKER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        if job["status"] == "queued":
            finish_cancellation(id)
        else:
            set_job(id, status="cancelling", phase="cancelling", error=None)
            audit(
                job["game_id"],
                id,
                "analysis_cancel_requested",
                "cancelling",
                job["attempts"],
            )
        return public_job(get("job", id))


@app.get("/api/jobs/{id}")
def job(id: str):
    return public_job(get("job", id))


@app.get("/api/games/{game_id}/jobs")
def game_jobs(game_id: str):
    get("game", game_id)
    return {
        "items": [public_job(j) for j in all_records("job") if j["game_id"] == game_id]
    }


def latest_job_id(game):
    jobs = [j for j in all_records("job") if j["game_id"] == game]
    return jobs[-1]["job_id"] if jobs else None


def identity_revision(job_id):
    return next(
        (
            r.get("revision", 0)
            for r in all_records("identity_correction")
            if r["job_id"] == job_id
        ),
        0,
    )


def corrected_samples(job_id):
    from workers.video.identity_corrections import apply_track_splits

    samples = [
        sample
        for batch in all_records("batch")
        if batch["job_id"] == job_id
        for sample in batch["items"]
    ]
    correction = next(
        (r for r in all_records("identity_correction") if r["job_id"] == job_id), {}
    )
    return apply_track_splits(samples, correction.get("splits", []))


@app.get("/api/games/{game_id}/tracks")
def tracks(game_id: str, after_ms: int = -1):
    get("game", game_id)
    latest = latest_job_id(game_id)
    items = [s for s in corrected_samples(latest) if s["timestamp_ms"] > after_ms]
    meta = next(
        (
            m["assignments"]
            for m in all_records("track_metadata")
            if m["job_id"] == latest
        ),
        {},
    )
    items = [dict(sample, **meta.get(sample["track_id"], {})) for sample in items]
    manifests = [
        bundle for bundle in all_records("highlights") if bundle["job_id"] == latest
    ]
    overrides = {
        track: group
        for bundle in manifests
        for group in bundle.get("roster_groups", [])
        for track in group["track_ids"]
    }
    items = [
        dict(
            sample,
            player_id=overrides[sample["track_id"]]["player_id"],
            player_label=overrides[sample["track_id"]]["label"],
            identity_source="reviewer",
            identity_confirmed=True,
        )
        if sample["track_id"] in overrides
        else sample
        for sample in items
    ]
    return {"items": items}


@app.get("/api/games/{game_id}/events")
def events(game_id: str):
    get("game", game_id)
    id = latest_job_id(game_id)
    visible_clips = {c["event_id"] for c in clips(game_id)["items"]}
    return {
        "items": [
            c
            for c in all_records("event")
            if c["job_id"] == id
            and (
                c.get("origin") not in ("visual_heuristic", "reviewer_confirmed")
                or c["event_id"] in visible_clips
            )
        ]
    }


@app.get("/api/games/{game_id}/clips")
def clips(game_id: str):
    get("game", game_id)
    id = latest_job_id(game_id)
    result = [c for c in all_records("clip") if c["job_id"] == id]
    manifests = [b for b in all_records("highlights") if b["job_id"] == id]
    if manifests:
        active = manifests[-1]
        visible = (
            {
                c["clip_id"]
                for c in active["candidates"]
                if c.get("clip_uri") and c["review_status"] != "rejected"
            }
            if active["status"] == "succeeded"
            else set()
        )
        result = [
            c for c in result if c.get("kind") != "highlight" or c["clip_id"] in visible
        ]
    return {"items": result}


@app.get("/api/jobs/{id}/audit")
def audits(id: str):
    get("job", id)
    return {"items": [a for a in all_records("audit") if a["job_id"] == id]}


@app.get("/api/games/{game_id}/stream")
def stream(game_id: str, last_event_id: str = Header("0")):
    get("game", game_id)
    try:
        cursor = max(0, int(last_event_id))
    except ValueError:
        raise HTTPException(422, "Invalid Last-Event-ID")

    async def generate():
        nonlocal cursor
        while True:
            with connect() as db:
                rows = db.execute(
                    "SELECT * FROM updates WHERE game_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                    (game_id, cursor),
                ).fetchall()
            for row in rows:
                cursor = row["sequence"]
                envelope = dict(
                    sequence=cursor,
                    kind=row["kind"],
                    game_id=game_id,
                    job_id=row["job_id"],
                    payload=json.loads(row["payload"]),
                    server_timestamp_ms=round(row["created"] * 1000),
                )
                yield "id: %s\ndata: %s\n\n" % (cursor, json.dumps(envelope))
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/media/{id}")
def media(id: str):
    item = get("artifact", id)
    path = Path(item["path"]).resolve()
    if not path.is_relative_to(DATA) or not path.is_file():
        raise HTTPException(404, "Artifact missing")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/profiles")
def profiles():
    # No dataset track is silently converted to an athlete identity.
    return {"items": []}


app.include_router(highlight_router)
app.include_router(basketball_router)
app.include_router(shot_router)


if (ROOT / "frontend/dist").exists():
    app.mount(
        "/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="frontend"
    )
