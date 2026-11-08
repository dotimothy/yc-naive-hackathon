import json
import shutil
import subprocess

import numpy as np
import pytest

from workers.render import portrait
from workers.render.story import build_story_plan, story_beat


def plan():
    return dict(
        profile=portrait.PROFILE,
        presentation_style="broadcast-quiz-v2",
        attribution="Test source",
        segments=[
            dict(
                track_id="fragment-2",
                shot_id="camera-1",
                label="Track fragment-2",
                display_label="Player A",
                source_start_ms=0,
                source_end_ms=2000,
                samples=[
                    dict(
                        timestamp_ms=t,
                        shot_id="camera-1",
                        bbox=[0.2 + t / 10000, 0.2, 0.1, 0.4],
                    )
                    for t in range(0, 2001, 250)
                ],
            )
        ],
        quiz_candidates=[dict(candidate_id="q")],
    )


def test_story_retains_evidence_and_does_not_modify_quiz_plan():
    original = plan()
    result = build_story_plan(original)
    segment = result["segments"][0]
    assert result["profile"] == "player-portrait-story-v1"
    assert not result["quiz_candidates"]
    assert "story_beats" not in original["segments"][0]
    assert original["quiz_candidates"]
    assert segment["story_evidence"]["horizontal_shift"] == pytest.approx(0.2)
    assert segment["story_evidence"]["track_id"] == "fragment-2"
    assert story_beat(segment, 100)["title"] == "SETUP"
    assert story_beat(segment, 1000)["headline"] == "Moves right on screen"
    assert "finishes further right" in story_beat(segment, 1999)["caption"]


@pytest.mark.parametrize("damage", ["gap", "cut", "short"])
def test_story_rejects_uncertain_evidence(damage):
    p = plan()
    s = p["segments"][0]
    if damage == "gap":
        s["samples"] = [
            x for x in s["samples"] if x["timestamp_ms"] not in [500, 750, 1000]
        ]
    elif damage == "cut":
        s["samples"][3]["shot_id"] = "camera-2"
    else:
        s["source_end_ms"] = 1000
    with pytest.raises(ValueError):
        build_story_plan(p)


def test_story_draws_actual_beat_without_quiz_placeholders(monkeypatch):
    p = build_story_plan(plan())
    messages = []

    def record(draw, xy, message, *args, **kwargs):
        messages.append(str(message))
        return xy[1] + 25

    monkeypatch.setattr(portrait, "text", record)
    frame, _ = portrait.draw_frame(
        np.zeros((360, 480, 3), dtype=np.uint8), p["segments"][0], 1000, None
    )
    assert frame.shape == (1280, 720, 3)
    assert "THE MOVEMENT" in messages and "Moves right on screen" in messages
    assert not any("WHAT HAPPENS" in m or "ANSWER:" in m for m in messages)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_story_render_is_playable_portrait_without_inserted_quiz_pauses(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "story.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=480x360:r=30",
            "-t",
            "2.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    receipt = portrait.render_portrait(source, build_story_plan(plan()), [], output)
    assert receipt["profile"] == "player-portrait-story-v1"
    assert receipt["captions_burned_in"] and receipt["quiz_count"] == 0
    assert receipt["duration_ms"] == 2000
    assert len(receipt["stories"]) == 1 and len(receipt["stories"][0]["beats"]) == 3
    metadata = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["avg_frame_rate"]) == (
        720,
        1280,
        "30/1",
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"],
        check=True,
    )
