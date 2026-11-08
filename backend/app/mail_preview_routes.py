"""Serve only registered, expiring email-request previews using opaque bearer links."""

import json
import re
import sqlite3
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]


@router.get("/api/mail-previews/{token}")
def mail_preview(token: str):
    if not re.fullmatch(r"[a-f0-9]{48}", token):
        raise HTTPException(404, "Preview not found")
    folder = ROOT / "data/agentmail"
    db_path = folder / "mailroom.sqlite3"
    if not db_path.is_file():
        raise HTTPException(404, "Preview not found")
    with sqlite3.connect("file:%s?mode=ro" % db_path, uri=True) as db:
        row = db.execute(
            "SELECT id,value FROM requests WHERE token=? AND status='ready'", (token,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Preview not found")
    value = json.loads(row[1])
    if value.get("preview_expires", 0) <= time.time():
        raise HTTPException(410, "Preview expired")
    path = (folder / "previews" / (row[0] + ".mp4")).resolve()
    if not path.is_relative_to((folder / "previews").resolve()) or not path.is_file():
        raise HTTPException(404, "Preview not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
