import hashlib
import subprocess

from PIL import Image

from backend.tests.test_portrait_export import snapshot
from workers.render.cover import render_cover
from workers.render.portrait import build_plan


def test_cover_uses_bounded_real_source_frames_and_correct_portrait_dimensions(
    tmp_path,
):
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
            "testsrc2=size=480x360:rate=20",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-threads",
            "1",
            "-y",
            str(source),
        ],
        check=True,
    )
    plan = build_plan(snapshot())
    path = tmp_path / "cover.jpg"
    receipt = render_cover(source, plan, plan["quiz_candidates"], path)
    assert Image.open(path).size == (720, 1280)
    segment = next(s for s in plan["segments"] if s["track_id"] == receipt["track_id"])
    assert (
        segment["source_start_ms"]
        <= receipt["source_timestamp_ms"]
        <= segment["source_end_ms"]
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["sha256"]
