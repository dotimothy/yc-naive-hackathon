import json
import shutil
import subprocess

import numpy as np
import pytest

from workers.render import portrait


def snapshot(shift=0.01):
    samples = [
        dict(
            track_id="2",
            shot_id="0",
            timestamp_ms=i * 250,
            bbox=[0.2 + i * shift, 0.2, 0.14, 0.45],
            confidence=0.9,
            role_status="unknown",
            keypoints=[],
        )
        for i in range(15)
    ]
    return dict(
        job_id="job",
        source_sha256="a" * 64,
        selected=dict(shot_id="0", source_start_ms=0, source_end_ms=3500),
        tracks=samples,
        shots=[dict(shot_id="0", start_ms=0, end_ms=4000)],
        candidates=[
            dict(
                candidate_id="job-highlight-2",
                track_id="2",
                shot_id="0",
                action_start_ms=600,
                action_end_ms=3200,
                score=50,
            )
        ],
    )


def test_plan_bounds_and_quiz_answer_use_future_same_shot_samples():
    p = portrait.build_plan(snapshot())
    assert len(p["segments"]) == 1 and len(p["quiz_candidates"]) == 1
    q = p["quiz_candidates"][0]
    assert q["correct_option_id"] == "B"
    assert q["evidence"]["end_ms"] > q["freeze_ms"]
    assert q["reveal_ms"] <= p["segments"][0]["source_end_ms"]
    assert p["segments"][0]["source_start_ms"] >= 0
    assert p["segments"][0]["source_end_ms"] <= 3500
    assert p["segments"][0]["label"] == "Track 2"


def test_ambiguous_future_motion_and_other_camera_fragments_do_not_become_quizzes():
    assert not portrait.build_plan(snapshot(shift=0.0045))["quiz_candidates"]
    s = snapshot()
    for item in s["tracks"]:
        item["shot_id"] = "another-camera"
    assert not portrait.build_plan(s)["segments"]
    s = snapshot()
    for item in s["tracks"]:
        item["role_status"] = "likely_non_player"
    assert not portrait.build_plan(s)["segments"]


def test_portrait_crop_contains_subject_and_clamps_to_source():
    for box in [[0, 0.1, 0.12, 0.5], [0.85, 0.5, 0.15, 0.5], [0.4, 0.3, 0.1, 0.35]]:
        rect, _ = portrait.crop_rect(1280, 720, box)
        left, t, r, b = rect
        assert 0 <= left < r <= 1280 and 0 <= t < b <= 720
        assert left <= box[0] * 1280 + 1 and r >= (box[0] + box[2]) * 1280 - 1
    rect, _ = portrait.crop_rect(720, 1280, [0.1, 0.05, 0.7, 0.9])
    assert rect == (
        0,
        0,
        720,
        1280,
    )  # Tall bodies fall back to aspect-preserving context.


def test_question_does_not_reveal_answer_until_later_playback(monkeypatch):
    p = portrait.build_plan(snapshot())
    q = p["quiz_candidates"][0]
    messages = []

    def record(draw, xy, message, *args, **kwargs):
        messages.append(str(message))
        return xy[1] + 24

    monkeypatch.setattr(portrait, "text", record)
    frame = np.zeros((360, 480, 3), dtype=np.uint8)
    image, _ = portrait.draw_frame(
        frame, p["segments"][0], q["freeze_ms"], None, q, True
    )
    assert image.shape == (1280, 720, 3)
    assert not any("ANSWER:" in m or "tracked center shifts" in m for m in messages)
    messages.clear()
    portrait.draw_frame(frame, p["segments"][0], q["reveal_ms"], None, q, False)
    assert any("ANSWER: Right on screen" in m for m in messages)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")
def test_actual_portrait_render_and_quiz_music_timing(tmp_path):
    source = tmp_path / "source.mp4"
    music = tmp_path / "test-tone.wav"
    output = tmp_path / "portrait.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=480x360:r=20",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:sample_rate=48000",
            "-t",
            "4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-t",
            "1",
            str(music),
        ],
        check=True,
    )
    p = portrait.build_plan(snapshot())
    q = p["quiz_candidates"][:1]
    receipt = portrait.render_portrait(source, p, q, output, music)
    meta = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    video = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["avg_frame_rate"]) == (
        720,
        1280,
        "30/1",
    )
    assert (
        receipt["captions_burned_in"]
        and receipt["quiz_count"] == 1
        and receipt["quiz_music_configured"]
    )
    assert not receipt["quiz_music_missing"]
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(output),
            "-f",
            "null",
            "-",
        ],
        check=True,
    )
    raw = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(output),
            "-vn",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout
    audio = np.frombuffer(raw, dtype=np.float32)

    def energy(start, end, hz):
        chunk = audio[round(start * 48000) : round(end * 48000)]
        spectrum = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
        freq = np.fft.rfftfreq(len(chunk), 1 / 48000)
        return spectrum[np.abs(freq - hz) < 4].sum()

    cue = receipt["music_cues"][0]
    assert energy(
        cue["start_seconds"] + 0.4, cue["start_seconds"] + 1.0, 880
    ) > 20 * energy(cue["start_seconds"] + 0.4, cue["start_seconds"] + 1.0, 220)
    assert energy(
        cue["start_seconds"] + cue["duration_seconds"] + 0.2,
        cue["start_seconds"] + cue["duration_seconds"] + 0.7,
        220,
    ) > 20 * energy(
        cue["start_seconds"] + cue["duration_seconds"] + 0.2,
        cue["start_seconds"] + cue["duration_seconds"] + 0.7,
        880,
    )


def test_no_stale_focus_box_across_tracking_gap():
    values = snapshot()["tracks"]
    a, b = copy_sample(values[0]), copy_sample(values[2])
    b["bbox"][0] = 0.3
    sample = portrait.focus_sample([a, b], 250)
    assert sample["bbox"][0] == pytest.approx(0.25)
    b["timestamp_ms"] = 1000
    assert portrait.focus_sample([a, b], 500) is None


def copy_sample(sample):
    return dict(sample, bbox=list(sample["bbox"]))


def test_large_back_and_forth_motion_is_not_a_stationary_prediction_answer():
    s = snapshot(shift=0)
    for i, sample in enumerate(s["tracks"]):
        sample["bbox"][0] = 0.2 if i % 2 == 0 else 0.35
    assert not portrait.build_plan(s)["quiz_candidates"]
