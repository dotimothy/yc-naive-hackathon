import subprocess
import pytest
from fastapi.testclient import TestClient
from backend.app import main


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=20",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        check=True,
    )
    return p


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path / "storage")
    main.DATA.mkdir()
    monkeypatch.setattr(main, "DB", main.DATA / "test.sqlite3")
    with TestClient(main.app) as c:
        yield c
