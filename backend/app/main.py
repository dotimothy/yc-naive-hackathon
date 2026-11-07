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
from workers.video.pipeline import process_video
from workers.video.runtime import available_devices, select_device
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


def run_job(id):
    job = get("job", id)
    game = get("game", job["game_id"])
    game_id = game["game_id"]
    for attempt in range(1, 4):
        set_job(id, status="running", attempts=attempt, error=None, phase="track")
        audit(game_id, id, "analysis_started", "running", attempt)

        def emit(kind, payload):
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
                        for c in all_records("clip")
                        if c["event_id"] == payload["event_id"]
                    ),
                    None,
                )
                payload["clip_uri"] = clip["uri"] if clip else None
                payload["evidence_uri"] = game["source_uri"]
                put("event", payload["event_id"], dict(job_id=id, **payload))
                update(game_id, id, kind, payload)

        try:
            # Each retry replaces this job's materialized output; the append-only audit retains history.
            with connect() as db:
                for row in db.execute(
                    "SELECT kind,id,value FROM records WHERE kind IN ('batch','clip','event')"
                ).fetchall():
                    if json.loads(row["value"]).get("job_id") == id:
                        db.execute(
                            "DELETE FROM records WHERE kind=? AND id=?",
                            (row["kind"], row["id"]),
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
            put("report", id, result)
            set_job(id, status="succeeded", phase="complete", report=result)
            audit(game_id, id, "analysis_completed", "succeeded", attempt)
            return
        except Exception as exc:
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
        if job["status"] in ("queued", "running"):
            set_job(
                job["job_id"],
                status="failed",
                error="Server restarted; submit analysis again with a new request key",
            )
    yield


app = FastAPI(title="ScoutFilm local", lifespan=lifespan)


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


@app.post("/api/games", status_code=201)
def upload(
    video: UploadFile = File(...),
    rights_confirmed: bool = Form(...),
    sport: str = Form("basketball"),
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
        return public_game(
            register_game(path, Path(video.filename or "Uploaded game").name)
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        video.file.close()


@app.post("/api/games/sample", status_code=201)
def sample():
    source = ROOT / "data/trackid3x3/outdoor_stationary_12s.mp4"
    outdoor = source.exists()
    if not outdoor:
        source = ROOT / "data/trackid3x3/basket_S1T1_pre.mp4"
    if not source.exists():
        raise HTTPException(404, "Run scripts/download_sample.py first")
    for game in all_records("game"):
        if game.get("sample") and game.get("sample_variant") == (
            "outdoor" if outdoor else "indoor"
        ):
            return public_game(game)
    path = DATA / (uuid.uuid4().hex + ".mp4")
    shutil.copy2(source, path)
    game = register_game(
        path,
        "TrackID3x3 · "
        + ("outdoor full 3x3 court" if outdoor else "indoor fixed camera"),
    )
    game["sample"] = True
    game["sample_variant"] = "outdoor" if outdoor else "indoor"
    if outdoor:
        game["court_polygon"] = OUTDOOR_SAMPLE_COURT
    put("game", game["game_id"], game)
    return public_game(game)


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
    device: Optional[str] = Field(default=None, min_length=1, max_length=20)
    candidate_ranges: list[Cut] = Field(default_factory=list, max_length=10)
    replay_paced: bool = False
    stride: int = Field(default=5, ge=1, le=20)
    confidence: float = Field(default=0.75, ge=0.1, le=0.99)


@app.post("/api/games/{game_id}/analyze", status_code=202)
def analyze(
    game_id: str, body: Analysis, idempotency_key: str = Header(..., max_length=200)
):
    game = get("game", game_id)
    options = body.model_dump()
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
            # One active job per game prevents mixed tracking timelines.
            if any(
                j["game_id"] == game_id and j["status"] in ("queued", "running")
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


@app.get("/api/games/{game_id}/tracks")
def tracks(game_id: str, after_ms: int = -1):
    get("game", game_id)
    latest = latest_job_id(game_id)
    batches = [b for b in all_records("batch") if b["job_id"] == latest]
    items = [s for b in batches for s in b["items"] if s["timestamp_ms"] > after_ms]
    return {"items": items}


@app.get("/api/games/{game_id}/events")
def events(game_id: str):
    get("game", game_id)
    id = latest_job_id(game_id)
    return {"items": [c for c in all_records("event") if c["job_id"] == id]}


@app.get("/api/games/{game_id}/clips")
def clips(game_id: str):
    get("game", game_id)
    id = latest_job_id(game_id)
    return {"items": [c for c in all_records("clip") if c["job_id"] == id]}


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


if (ROOT / "frontend/dist").exists():
    app.mount(
        "/", StaticFiles(directory=ROOT / "frontend/dist", html=True), name="frontend"
    )
