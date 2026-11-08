"""Thumbnail routes serve cached stills only for registered sources."""

import hashlib
import subprocess

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.thumbnail_routes import thumbnail_file


def test_real_source_thumbnail_is_cached_and_served_as_jpeg(tmp_path, monkeypatch):
    from backend.app import main

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=180x320:rate=5",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-y",
            str(source),
        ],
        check=True,
    )
    game = dict(
        path=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        duration_ms=1000,
    )

    def registered(kind, identifier):
        if kind == "game" and identifier == "registered":
            return game
        raise HTTPException(404, "Record not found")

    monkeypatch.setattr(main, "get", registered)
    monkeypatch.setattr(main, "DATA", tmp_path)
    client = TestClient(main.app)
    response = client.get("/api/games/registered/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert "max-age=86400" in response.headers["cache-control"]
    image = next((tmp_path / "thumbnails").glob("*.jpg"))
    modified = image.stat().st_mtime_ns
    assert response.content.startswith(b"\xff\xd8\xff")
    assert client.get("/api/games/registered/thumbnail").content == response.content
    assert image.stat().st_mtime_ns == modified
    assert client.get("/api/games/arbitrary/thumbnail").status_code == 404


def test_invalid_source_cannot_leave_a_cached_thumbnail(tmp_path):
    source = tmp_path / "bad.mp4"
    source.write_bytes(b"invalid video")
    game = dict(path=str(source), source_sha256="a" * 64, duration_ms=1000)
    with pytest.raises(HTTPException) as error:
        thumbnail_file(game, tmp_path / "thumbnails")
    assert error.value.status_code == 503
    assert not list((tmp_path / "thumbnails").glob("*.jpg"))
    game["source_sha256"] = "../../arbitrary"
    with pytest.raises(HTTPException) as error:
        thumbnail_file(game, tmp_path / "thumbnails")
    assert error.value.status_code == 404
