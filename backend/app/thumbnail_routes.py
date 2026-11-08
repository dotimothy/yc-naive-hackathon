"""Cached, bounded stills from registered game sources only."""

import re
import subprocess
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api")
_GENERATORS = threading.BoundedSemaphore(2)


def thumbnail_file(game, folder):
    digest = game.get("source_sha256", "")
    source = Path(game["path"])
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or not source.is_file():
        raise HTTPException(404, "Video source unavailable")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{digest}-480-v1.jpg"
    if destination.is_file():
        return destination
    with _GENERATORS:
        if destination.is_file():
            return destination
        temporary = folder / f"{digest}-{uuid.uuid4().hex}.jpg"
        timestamp = min(2.0, max(0, game.get("duration_ms", 0) / 5000))
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-threads",
                    "1",
                    "-ss",
                    str(timestamp),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2:color=0x0f141b",
                    "-q:v",
                    "4",
                    "-threads",
                    "1",
                    "-y",
                    str(temporary),
                ],
                capture_output=True,
                check=True,
                timeout=20,
            )
            if not temporary.read_bytes().startswith(b"\xff\xd8\xff"):
                raise ValueError("Invalid thumbnail")
            temporary.replace(destination)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise HTTPException(503, "Thumbnail temporarily unavailable") from None
        finally:
            temporary.unlink(missing_ok=True)
    return destination


@router.get("/games/{game_id}/thumbnail")
def game_thumbnail(game_id: str):
    from backend.app import main as state

    game = state.get("game", game_id)
    image = thumbnail_file(game, state.DATA / "thumbnails")
    return FileResponse(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
