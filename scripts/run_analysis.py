#!/usr/bin/env python3
"""One-job worker protocol; stdout carries JSON, model diagnostics go to stderr."""

import contextlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def normalize_options(options):
    """Old API processes may enqueue jobs while a model migration awaits an idle reload."""
    options = dict(options)
    if options.get("pose_model") != "ultralytics/yolo26n-pose":
        options.update(pose_model="ultralytics/yolo26n-pose", confidence=0.25)
    return options


def main():
    channel = sys.stdout
    request = json.loads(sys.stdin.readline())

    def emit(kind, payload):
        channel.write(json.dumps(dict(kind=kind, payload=payload)) + "\n")
        channel.flush()
        # Parent registers media and returns mutations (URI, attempt), as the in-process emitter does.
        reply = json.loads(sys.stdin.readline())
        payload.clear()
        payload.update(reply["payload"])

    with contextlib.redirect_stdout(sys.stderr):
        from workers.video.pipeline import process_video

        report = process_video(
            request["source"],
            request["output"],
            normalize_options(request["options"]),
            emit,
        )
    channel.write(json.dumps(dict(kind="result", payload=report)) + "\n")
    channel.flush()


if __name__ == "__main__":
    main()
