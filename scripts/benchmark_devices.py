#!/usr/bin/env python3
"""Reproducible end-to-end comparison, preserving sample rates and model settings."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workers.video.pipeline import process_video  # noqa: E402
from workers.video.runtime import available_devices  # noqa: E402
from workers.video.sports.basketball import OUTDOOR_SAMPLE_COURT  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--devices", nargs="+", default=["cpu", "mps"])
    parser.add_argument("--output", type=Path, default=ROOT / "data/benchmarks")
    parser.add_argument(
        "--outdoor-sample",
        action="store_true",
        help="Apply the manually marked TrackID3x3 outdoor sample court",
    )
    parser.add_argument("--stride", type=int, default=5)
    args = parser.parse_args()
    reports = {}
    print("Available devices:", available_devices(), flush=True)
    for device in args.devices:
        name = device.replace(":", "-")
        options = dict(
            game_id="benchmark",
            job_id=name,
            device=device,
            stride=args.stride,
            sport="basketball",
        )
        if args.outdoor_sample:
            options["court_polygon"] = OUTDOOR_SAMPLE_COURT
        print("Starting", device, flush=True)

        def emit(kind, payload):
            if kind == "phase":
                print(device, payload["phase"], flush=True)

        reports[device] = process_video(args.source, args.output / name, options, emit)
        report = reports[device]
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in [
                        "device",
                        "execution_mode",
                        "processed_frames",
                        "track_samples",
                        "track_ids",
                        "inference_fps",
                        "elapsed_seconds",
                        "model_load_seconds",
                    ]
                },
                indent=2,
            ),
            flush=True,
        )
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "comparison.json").write_text(
            json.dumps(reports, indent=2) + "\n"
        )


if __name__ == "__main__":
    main()
