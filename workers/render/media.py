"""Source-preserving clip rendering and independent media checks."""

import hashlib
import math
import cv2
import numpy as np
import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe(path):
    if not shutil.which("ffprobe"):
        raise RuntimeError("FFmpeg/ffprobe is required: brew install ffmpeg")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    info = json.loads(result.stdout)
    stream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not stream:
        raise ValueError("No video stream")
    fps = float(Fraction(stream["avg_frame_rate"]))
    duration = float(info["format"].get("duration", stream.get("duration", 0)))
    if not 0 < duration <= 1800 or not 0 < fps <= 240:
        raise ValueError("Use a video of up to 30 minutes with a valid frame rate")
    if stream["width"] * stream["height"] > 3840 * 2160:
        raise ValueError("Maximum video resolution is 4K")
    return dict(
        width=stream["width"],
        height=stream["height"],
        fps=fps,
        duration_ms=round(duration * 1000),
        frame_count=int(stream.get("nb_frames", 0)),
        codec=stream["codec_name"],
    )


def render_clip(source_path, start_ms, end_ms, output_path, overlays=None):
    metadata = probe(source_path)
    if not 0 <= start_ms < end_ms <= metadata["duration_ms"]:
        raise ValueError("Clip range falls outside source")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.stem + ".tmp.mp4")
    filters = []
    for overlay in (overlays or [])[:100]:
        values = list(overlay["bbox"])
        if len(values) != 4 or not all(math.isfinite(v) for v in values):
            raise ValueError("Invalid spotlight coordinates")
        x, y, w, h = values
        if w <= 0 or h <= 0:
            raise ValueError("Invalid spotlight dimensions")
        x = max(0, min(metadata["width"] - 1, round(x * metadata["width"])))
        y = max(0, min(metadata["height"] - 1, round(y * metadata["height"])))
        w = max(1, min(metadata["width"] - x, round(w * metadata["width"])))
        h = max(1, min(metadata["height"] - y, round(h * metadata["height"])))
        a = max(0, (overlay["timestamp_ms"] - start_ms) / 1000)
        b = min((end_ms - start_ms) / 1000, (overlay["end_ms"] - start_ms) / 1000)
        if b > a:
            filters.append(
                "drawbox=x=%d:y=%d:w=%d:h=%d:color=yellow@0.95:t=3:enable='gte(t,%.6f)*lt(t,%.6f)'"
                % (x, y, w, h, a, b)
            )
    video_filter = ["-vf", ",".join(filters)] if filters else []
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-ss",
            str(start_ms / 1000),
            "-i",
            str(source_path),
            "-t",
            str((end_ms - start_ms) / 1000),
            *video_filter,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        capture_output=True,
        timeout=180,
        check=True,
    )
    temporary.replace(output_path)
    return output_path


def verify_clip(source_path, clip_path, start_ms, end_ms):
    source, clip = probe(source_path), probe(clip_path)
    expected = end_ms - start_ms
    tolerance = max(120, round(2000 / source["fps"]))
    if not 0 <= start_ms < end_ms <= source["duration_ms"]:
        raise ValueError("Invalid source provenance")
    if abs(clip["duration_ms"] - expected) > tolerance:
        raise ValueError("Clip duration does not match source range")
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(clip_path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    # Independent first-frame alignment sanity check against the requested source time.
    original = cv2.VideoCapture(str(source_path))
    rendered = cv2.VideoCapture(str(clip_path))
    try:
        original.set(cv2.CAP_PROP_POS_MSEC, start_ms)
        ok_a, a = original.read()
        ok_b, b = rendered.read()
        if not ok_a or not ok_b:
            raise ValueError("Cannot decode alignment frames")
        a = cv2.resize(a, (320, 180)).astype(np.float32)
        b = cv2.resize(b, (320, 180)).astype(np.float32)
        alignment_mae = float(np.abs(a - b).mean())
        if alignment_mae > 12:
            raise ValueError(
                "Clip first frame does not align with requested source time"
            )
    finally:
        original.release()
        rendered.release()
    return dict(
        first_frame_alignment_mae=alignment_mae,
        qa_status="passed",
        measured_duration_ms=clip["duration_ms"],
        expected_duration_ms=expected,
        tolerance_ms=tolerance,
        source_sha256=sha256(source_path),
        sha256=sha256(clip_path),
        decoded=True,
    )
