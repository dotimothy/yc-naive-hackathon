#!/usr/bin/env python3
"""One-job worker protocol; stdout carries JSON, model diagnostics go to stderr."""

import contextlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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
            request["source"], request["output"], request["options"], emit
        )
    channel.write(json.dumps(dict(kind="result", payload=report)) + "\n")
    channel.flush()


if __name__ == "__main__":
    main()
