#!/usr/bin/env python3
"""Frozen-frame detector/identity comparison for the YOLO26 pose migration.

Uses authorized TrackID3x3 media and the historical Keypoint R-CNN caches without
altering either. This measures box matching and association, not pose accuracy.
No threshold or association tuning is performed on held-out footage.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_tracking import (  # noqa: E402
    COUNT_KEYS,
    association_metrics,
    finish,
    load_gt,
    load_sequence,
    match,
    run_variant,
)
from workers.video.identity import uniform_descriptor  # noqa: E402
from workers.video.runtime import get_model, infer, select_device  # noqa: E402

MANIFEST = ROOT / "data/claude_tracking/sources/manifest.json"
BASELINE = ROOT / "data/claude_tracking/detections"
POLICY = dict(
    tracker="continuity",
    identity_continuity=False,
    appearance=True,
    reconnection="review",
)
MODELS = ("keypoint_rcnn_cached", "yolo26n_pose")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(frames, gt, name):
    labels = [match(f["boxes"], gt.get(f["index"] + 1, [])) for f in frames]
    gt_count = sum(len(gt.get(f["index"] + 1, [])) for f in frames)
    tracks, players, _, summary = run_variant(frames, POLICY, game=name)
    return dict(
        sampled_frames=len(frames),
        detections=sum(len(f["boxes"]) for f in frames),
        zero_detection_frames=sum(not f["boxes"] for f in frames),
        track_id=finish(association_metrics(labels, tracks, gt_count)),
        player_id=finish(association_metrics(labels, players, gt_count)),
        identity_summary=summary,
    )


def evaluate(name, spec, model, device, confidence, out):
    video, annotation = ROOT / spec["video"], ROOT / spec["annotation"]
    if (
        digest(video) != spec["video_sha256"]
        or digest(annotation) != spec["annotation_sha256"]
    ):
        raise ValueError(
            "Source or annotation hash differs from frozen manifest: " + name
        )
    historical = json.loads((BASELINE / name / "report.json").read_text())
    if (
        historical.get("model") != "torchvision/keypointrcnn_resnet50_fpn/COCO_V1"
        or historical.get("confidence") != 0.75
    ):
        raise ValueError("Historical detector cache metadata changed: " + name)
    baseline, meta, complete = load_sequence(
        BASELINE / name / "tracks.json", BASELINE / name / "report.json"
    )
    if not complete:
        raise ValueError("Baseline must include zero-detection sampled frames")
    wanted = {f["index"]: f for f in baseline}
    if any(index % 5 for index in wanted):
        raise ValueError("Unexpected historical sampling cadence")
    frames, samples, seconds, transfers = [], [], [], []
    cap = cv2.VideoCapture(str(video))
    index = 0
    started = time.perf_counter()
    try:
        while wanted and cap.grab():
            if index in wanted:
                ok, image = cap.retrieve()
                if not ok:
                    raise ValueError("Cannot retrieve sampled source frame")
                old = wanted.pop(index)
                # The historical frame list, not a newly estimated clock, defines alignment.
                ts = old["ts"]
                old["uniform"] = [uniform_descriptor(image, b) for b in old["boxes"]]
                prediction, took, transfer = infer(model, image, device)
                seconds.append(took)
                transfers.append(transfer)
                keep = prediction["scores"] >= confidence
                boxes = prediction["boxes"][keep].numpy()
                keypoints = prediction["keypoints"][keep].numpy()
                if (
                    keypoints.shape != (len(boxes), 17, 3)
                    or not np.isfinite(keypoints).all()
                ):
                    raise ValueError(
                        "Pose adapter did not preserve finite COCO-17 output"
                    )
                frames.append(
                    dict(
                        index=index,
                        ts=ts,
                        boxes=boxes.tolist(),
                        keypoints=list(keypoints),
                        uniform=[uniform_descriptor(image, b) for b in boxes],
                    )
                )
                for box, score in zip(boxes, prediction["scores"][keep].numpy()):
                    x1, y1, x2, y2 = map(float, box)
                    samples.append(
                        dict(
                            timestamp_ms=ts,
                            source_frame=index,
                            bbox=[
                                x1 / meta["width"],
                                y1 / meta["height"],
                                (x2 - x1) / meta["width"],
                                (y2 - y1) / meta["height"],
                            ],
                            confidence=float(score),
                        )
                    )
            index += 1
    finally:
        cap.release()
    if wanted:
        raise ValueError("Source ended before all baseline frames: " + name)
    gt = load_gt(annotation)
    predictor = getattr(getattr(model, "model", None), "predictor", None)
    settings = getattr(predictor, "args", None)
    result = dict(
        yolo_predictor_settings={
            key: getattr(settings, key, None)
            for key in ("imgsz", "conf", "rect", "nms", "half", "max_det")
        },
        split=spec["split"],
        video_sha256=spec["video_sha256"],
        annotation_sha256=spec["annotation_sha256"],
        fixed_sample_indices=[f["index"] for f in baseline],
        source_metadata=meta,
        models={
            "keypoint_rcnn_cached": metrics(baseline, gt, name),
            "yolo26n_pose": metrics(frames, gt, name),
        },
        yolo_timing=dict(
            inference_seconds=sum(seconds),
            transfer_seconds=sum(transfers),
            inference_fps=len(seconds) / sum(seconds) if sum(seconds) else None,
            first_frame_seconds=seconds[0] if seconds else None,
            median_seconds=float(np.median(seconds)) if seconds else None,
            p95_seconds=float(np.percentile(seconds, 95)) if seconds else None,
            elapsed_seconds=time.perf_counter() - started,
        ),
    )
    (out / (name + "_detections.json")).write_text(json.dumps(samples, indent=2))
    (out / (name + "_comparison.json")).write_text(json.dumps(result, indent=2))
    return result


def aggregate(sequences):
    result = {}
    splits = sorted({s["split"] for s in sequences.values()})
    for split in splits:
        selected = [s for s in sequences.values() if s["split"] == split]
        result[split] = {}
        for model in MODELS:
            result[split][model] = {}
            for kind in ("track_id", "player_id"):
                result[split][model][kind] = finish(
                    {
                        key: sum(s["models"][model][kind][key] for s in selected)
                        for key in COUNT_KEYS
                    }
                )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--device", default="mps")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    parser.add_argument("--only", nargs="+")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/pose_migration/yolo26n_1280"
    )
    args = parser.parse_args()
    if not 0 < args.confidence < 1:
        parser.error("Confidence must be in (0,1)")
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        parser.error("Disable MPS CPU fallback before validating native MPS execution")
    args.output = args.output.resolve()
    data_root = (ROOT / "data").resolve()
    if data_root not in args.output.parents:
        parser.error("Validation artifacts must stay under ignored data/")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    unknown = set(args.only or []) - set(manifest)
    if unknown:
        parser.error("Unknown sequences: " + ", ".join(sorted(unknown)))
    selected = {
        name: spec
        for name, spec in manifest.items()
        if (args.split == "all" or spec["split"] == args.split)
        and (not args.only or name in args.only)
    }
    if not selected:
        parser.error("The requested split and sequence filter select no footage")
    device = select_device(args.device)
    model, load_seconds, cached = get_model(device)
    # The adapter exposes this score threshold; no held-out threshold search.
    model.confidence = args.confidence
    report = dict(
        protocol=dict(
            source_manifest_sha256=digest(MANIFEST),
            stride=5,
            yolo_score_threshold=args.confidence,
            baseline_score_threshold=0.75,
            mot_frame_offset=1,
            matching_iou=0.5,
            association=POLICY,
            limitations=[
                "Box ground truth cannot validate pose accuracy",
                "Confidence scores are not calibrated across detectors",
                "Previously observed held-out sessions are regression footage, not a fresh acceptance set",
                "Higher detection coverage can change contamination denominators; compare both together",
            ],
        ),
        runtime=dict(
            device=device,
            torch=torch.__version__,
            model_load_seconds=load_seconds,
            model_cache_hit=cached,
            model_manifest=getattr(model, "manifest", {}),
            mps_cpu_fallback=os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "disabled"),
        ),
        sequences={},
    )
    for name, spec in selected.items():
        print("Evaluating", name, flush=True)
        report["sequences"][name] = evaluate(
            name, spec, model, device, args.confidence, args.output
        )
        report["aggregate"] = aggregate(report["sequences"])
        (args.output / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["sequences"][name]["models"]), flush=True)
    print("Report", args.output / "report.json", flush=True)


if __name__ == "__main__":
    main()
