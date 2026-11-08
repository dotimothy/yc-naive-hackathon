"""Local-only bridge for portrait planning/rendering from a frozen analysis snapshot."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from workers.render.portrait import build_plan, render_portrait  # noqa: E402
from workers.render.cover import render_cover  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", required=True)
parser.add_argument("--output-dir", required=True)
parser.add_argument("--plan-only", action="store_true")
parser.add_argument("--quizzes")
parser.add_argument("--quiz-music")
a = parser.parse_args()
snapshot = json.loads(Path(a.snapshot).read_text())
source = Path(snapshot["source_path"])
if hashlib.sha256(source.read_bytes()).hexdigest() != snapshot["source_sha256"]:
    raise ValueError("Source bytes do not match analysis hash")
folder = Path(a.output_dir)
folder.mkdir(parents=True, exist_ok=True)
plan = build_plan(snapshot)
if not plan["segments"]:
    raise ValueError("No sufficiently tracked highlighted players")
(folder / "plan.json").write_text(json.dumps(plan, indent=2))
if a.plan_only:
    print(
        json.dumps(
            {
                "plan_path": str(folder / "plan.json"),
                "tracks": len(plan["segments"]),
                "quiz_candidates": len(plan["quiz_candidates"]),
            }
        )
    )
    sys.exit(0)
selection = (
    json.loads(Path(a.quizzes).read_text())
    if a.quizzes
    else {"selected_candidate_ids": []}
)
known = {q["candidate_id"]: q for q in plan["quiz_candidates"]}
ids = selection["selected_candidate_ids"]
if len(ids) > 3 or len(ids) != len(set(ids)) or any(id not in known for id in ids):
    raise ValueError("Unrecognized clipper quiz selection")
quizzes = [known[id] for id in ids]
if len({q["track_id"] for q in quizzes}) != len(quizzes):
    raise ValueError("Duplicate quiz for one track")
output = folder / "player-breakdown-9x16.mp4"
receipt = render_portrait(source, plan, quizzes, output, music=a.quiz_music)
probe = json.loads(
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
video = next(s for s in probe["streams"] if s["codec_type"] == "video")
if video["width"] * 16 != video["height"] * 9:
    raise ValueError("Rendered output is not 9:16")
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
    capture_output=True,
    timeout=120,
    check=True,
)
cover = render_cover(source, plan, quizzes, folder / "cover.jpg")
receipt.update(
    cover=cover,
    presentation_style="broadcast-quiz-v2",
    job_id=snapshot["job_id"],
    source_sha256=snapshot["source_sha256"],
    sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    output_path=str(output),
    decoded=True,
    quizzes=quizzes,
)
(folder / "receipt.json").write_text(json.dumps(receipt, indent=2))
print(
    json.dumps(
        {
            "output_path": str(output),
            "receipt_path": str(folder / "receipt.json"),
            "tracks": receipt["track_count"],
            "quizzes": receipt["quiz_count"],
            "quiz_music_missing": receipt["quiz_music_missing"],
        }
    )
)
