#!/usr/bin/env python3
"""Local ball/hoop detection and review-required shot proposals, independent of pose."""

import argparse
from collections import Counter
import json
import platform
from importlib.metadata import version
from pathlib import Path
import sys
import time
import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workers.render.media import probe, sha256  # noqa: E402
from workers.video.basketball_detector import BasketballDetector  # noqa: E402
from workers.video.shot_candidates import (  # noqa: E402
    BallTrajectories,
    stationary_hoops,
    propose_shots,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/ball_hoop")
    parser.add_argument("--device", default=None)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--ball-confidence", type=float, default=0.25)
    parser.add_argument("--hoop-confidence", type=float, default=0.35)
    parser.add_argument("--no-tiles", action="store_true")
    parser.add_argument(
        "--players",
        type=Path,
        help="Optional source-matched track samples for provisional release attribution",
    )
    parser.add_argument("--limit-seconds", type=float, default=None)
    parser.add_argument(
        "--hoop",
        nargs=4,
        type=float,
        help="Optional reviewer-supplied normalized x y w h; never labelled automatic",
    )
    args = parser.parse_args()
    if not 1 <= args.stride <= 5:
        parser.error("Use stride 1–5 for ball trajectories")
    if args.limit_seconds is not None and args.limit_seconds <= 0:
        parser.error("limit-seconds must be positive")
    if args.hoop and not (
        0 <= args.hoop[0] < 1
        and 0 <= args.hoop[1] < 1
        and 0 < args.hoop[2] <= 1 - args.hoop[0]
        and 0 < args.hoop[3] <= 1 - args.hoop[1]
    ):
        parser.error("Hoop must lie inside normalized image")
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = probe(args.source)
    source_hash = sha256(args.source)
    if args.players:
        evidence_report = args.players.with_name("report.json")
        if (
            not evidence_report.exists()
            or json.loads(evidence_report.read_text()).get("source_sha256")
            != source_hash
        ):
            parser.error(
                "Player tracks require a neighboring report.json with the same source_sha256"
            )
    started = time.perf_counter()
    detector = BasketballDetector(
        ROOT / "data/models",
        args.device,
        args.ball_confidence,
        args.hoop_confidence,
        not args.no_tiles,
    )
    loaded = time.perf_counter()
    cap = cv2.VideoCapture(str(args.source))
    linker = BallTrajectories()
    detections = []
    balls = []
    hoop_observations = []
    frames = 0
    decoded = 0
    inference_seconds = 0.0
    previews = []
    last_time = -1
    try:
        while cap.grab():
            index = decoded
            decoded += 1
            if index % args.stride:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            # Source PTS when the decoder provides it; CFR index clock fallback is explicit.
            reported = round(cap.get(cv2.CAP_PROP_POS_MSEC))
            timestamp = (
                reported
                if reported > last_time
                else round(index / metadata["fps"] * 1000)
            )
            if timestamp <= last_time:
                raise ValueError("Non-monotonic source timestamps")
            last_time = timestamp
            if args.limit_seconds and timestamp >= args.limit_seconds * 1000:
                break
            before = time.perf_counter()
            observed = detector.detect(frame, timestamp)
            inference_seconds += time.perf_counter() - before
            detections.extend(observed)
            frames += 1
            balls.extend(
                linker.update(
                    [d for d in observed if d["class_name"] == "basketball"], timestamp
                )
            )
            hoop_observations.extend(d for d in observed if d["class_name"] == "hoop")
            if len(previews) < 6 and (frames == 1 or timestamp >= len(previews) * 2000):
                image = frame.copy()
                h, w = image.shape[:2]
                for d in observed:
                    if d["class_name"] not in ("basketball", "hoop"):
                        continue
                    x, y, bw, bh = d["bbox"]
                    a, b, c, e = (
                        round(x * w),
                        round(y * h),
                        round((x + bw) * w),
                        round((y + bh) * h),
                    )
                    colour = (
                        (0, 220, 255)
                        if d["class_name"] == "basketball"
                        else (255, 220, 40)
                    )
                    cv2.rectangle(image, (a, b), (c, e), colour, 2)
                    cv2.putText(
                        image,
                        d["class_name"] + " %.2f" % d["confidence"],
                        (a, max(18, b - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        colour,
                        1,
                    )
                cv2.putText(
                    image,
                    "MODEL OBSERVATIONS / %.2fs" % (timestamp / 1000),
                    (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )
                previews.append(cv2.resize(image, (640, 360)))
            if frames % 30 == 0:
                print("processed", frames, "source_ms", timestamp, flush=True)
    finally:
        cap.release()
    hoops = stationary_hoops(hoop_observations)
    if args.hoop:
        hoops = [
            dict(
                hoop_id="reviewer-hoop",
                bbox=args.hoop,
                confidence=None,
                observed_frames=0,
                origin="reviewer_supplied",
                verified=False,
            )
        ]
    players = json.loads(args.players.read_text()) if args.players else []
    candidates = propose_shots(balls, hoops, metadata["duration_ms"], players)
    report = dict(
        source_sha256=source_hash,
        runtime=dict(
            python=platform.python_version(),
            torch=version("torch"),
            rfdetr=version("rfdetr"),
            opencv=cv2.__version__,
        ),
        metadata=metadata,
        model=detector.manifest,
        device=detector.device,
        stride=args.stride,
        analyzed_until_ms=last_time,
        analysis_limit_seconds=args.limit_seconds,
        coverage_scope="partial_video"
        if args.limit_seconds and args.limit_seconds * 1000 < metadata["duration_ms"]
        else "full_video",
        tiled=not args.no_tiles,
        processed_frames=frames,
        decoded_frames=decoded,
        detector_seconds=inference_seconds,
        inference_fps=frames / inference_seconds if inference_seconds else 0,
        model_load_seconds=loaded - started,
        elapsed_seconds=time.perf_counter() - started,
        observations_by_class=dict(Counter(d["class_name"] for d in detections)),
        frames_with_ball=len({b["timestamp_ms"] for b in balls}),
        ball_segments=len({b["ball_track_id"] for b in balls}),
        stable_hoops=hoops,
        shot_candidates=candidates,
        status="needs_hoop_review" if not hoops else "needs_event_review",
        made_shots=None,
        missed_shots=None,
        official_statistics=False,
    )
    for filename, content in [
        ("objects.json", detections),
        ("ball_tracks.json", balls),
        ("report.json", report),
    ]:
        temporary = args.output / (filename + ".tmp")
        temporary.write_text(json.dumps(content, indent=2) + "\n")
        temporary.replace(args.output / filename)
    if previews:
        cv2.imwrite(
            str(args.output / "observations.jpg"),
            cv2.vconcat(
                [
                    cv2.hconcat(previews[i : i + 2])
                    if len(previews[i : i + 2]) == 2
                    else cv2.hconcat([previews[i], previews[i]])
                    for i in range(0, len(previews), 2)
                ]
            ),
        )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("model", "metadata")},
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
