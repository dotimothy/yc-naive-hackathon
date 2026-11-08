#!/usr/bin/env python3
"""Paired E-BARD detector benchmark. Coverage/agreement are not accuracy.

Run with .venv-ball/bin/python; models and per-frame artifacts remain in data/.
YOLO checkpoint: E-BARD revision aae99b56aadd2f1c40f1a402fd465dd666fbc8da.
"""

import argparse
import hashlib
import json
import os
import platform
from importlib.metadata import version
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"
os.environ["YOLO_OFFLINE"] = "true"
os.environ["YOLO_CONFIG_DIR"] = str(ROOT / "data/models/ultralytics-config")
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from workers.video.basketball_detector import BasketballDetector, CLASSES  # noqa: E402
from workers.video.runtime import synchronize  # noqa: E402
from workers.video.shot_candidates import suppress, center, BallTrajectories  # noqa: E402

YOLO_SHA256 = "dfe3534d51bb21024d1a400c37f0c1fbf0c8b96ea9a56a5f3cb5454813bfd641"
YOLO_URL = "https://huggingface.co/GabrieleGiudici/E-BARD-detection-models/resolve/aae99b56aadd2f1c40f1a402fd465dd666fbc8da/BODD_yolov8n_0001.pt"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/benchmarks/ebard-models"
    )
    args = parser.parse_args()
    from ultralytics import YOLO, settings

    settings.update({"sync": False})
    folder = ROOT / "data/models"
    weights = folder / "ebard-yolov8n.pt"
    if not weights.exists():
        urllib.request.urlretrieve(YOLO_URL, weights)
    manifest = dict(url=YOLO_URL, sha256=YOLO_SHA256, bytes=weights.stat().st_size)
    if hashlib.sha256(weights.read_bytes()).hexdigest() != YOLO_SHA256:
        raise ValueError("YOLO checkpoint hash mismatch")
    rf = BasketballDetector(folder, "mps")
    yolo = YOLO(str(weights)).to("mps")
    print("YOLO classes:", yolo.names, flush=True)
    if {int(k): v.lower() for k, v in yolo.names.items()} != CLASSES:
        raise ValueError("Unexpected class mapping")
    args.output.mkdir(parents=True, exist_ok=True)

    def detect_yolo(frame, timestamp):
        h, w = frame.shape[:2]
        crop = round(w * 0.6)
        views = [(0, 0, w, h), (0, 0, crop, h), (w - crop, 0, w, h)]
        # Square letterboxing gives the same 704x704 network input dimensions as RF-DETR.
        results = yolo.predict(
            [frame[y1:y2, x1:x2] for x1, y1, x2, y2 in views],
            imgsz=704,
            rect=False,
            conf=0.25,
            iou=0.5,
            max_det=300,
            device="mps",
            quantize=None,
            verbose=False,
            save=False,
        )
        detections = []
        for result, (left, top, _, _) in zip(results, views):
            for x1, y1, x2, y2, confidence, cls in result.boxes.data.cpu().tolist():
                label = CLASSES[int(cls)]
                if confidence < rf.thresholds[label]:
                    continue
                x1, x2 = np.clip([left + x1, left + x2], 0, w)
                y1, y2 = np.clip([top + y1, top + y2], 0, h)
                if x2 <= x1 or y2 <= y1:
                    continue
                detections.append(
                    dict(
                        class_name=label,
                        class_id=int(cls),
                        confidence=confidence,
                        bbox=[
                            float(x1 / w),
                            float(y1 / h),
                            float((x2 - x1) / w),
                            float((y2 - y1) / h),
                        ],
                        timestamp_ms=timestamp,
                        origin="ebard_yolov8n",
                    )
                )
        return suppress(detections)

    all_results = []
    for source in args.sources:
        cap = cv2.VideoCapture(str(source))
        fps = cap.get(cv2.CAP_PROP_FPS)
        ok, first = cap.read()
        if not ok:
            raise ValueError("Cannot decode " + str(source))
        for _ in range(3):
            rf.detect(first, 0)
            detect_yolo(first, 0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        records = []
        frame_index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % 2:
                frame_index += 1
                continue
            ts = round(cap.get(cv2.CAP_PROP_POS_MSEC))
            item = dict(
                frame_index=frame_index,
                timestamp_ms=ts,
                frame_sha256=hashlib.sha256(frame.tobytes()).hexdigest(),
            )
            methods = [("rfdetr", rf.detect), ("yolov8n", detect_yolo)]
            if len(records) % 2:
                methods.reverse()
            for name, method in methods:
                synchronize("mps")
                start = time.perf_counter()
                detections = method(frame, ts)
                synchronize("mps")
                item[name] = dict(
                    seconds=time.perf_counter() - start, observations=detections
                )
            records.append(item)
            frame_index += 1
            if len(records) % 30 == 0:
                print(source.name, len(records), "frames", flush=True)
        cap.release()
        result = dict(
            source=str(source.resolve()),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            fps=fps,
            processed_frames=len(records),
            models={},
        )
        for name in ("rfdetr", "yolov8n"):
            linker = BallTrajectories()
            track_ids = []
            for r in records:
                balls = [
                    d
                    for d in r[name]["observations"]
                    if d["class_name"] == "basketball"
                ]
                track_ids.extend(
                    d["ball_track_id"] for d in linker.update(balls, r["timestamp_ms"])
                )
            result["models"][name] = dict(
                seconds=sum(r[name]["seconds"] for r in records),
                segments=len(set(track_ids)),
                **{
                    label + "_frames": sum(
                        any(d["class_name"] == label for d in r[name]["observations"])
                        for r in records
                    )
                    for label in ("basketball", "hoop")
                },
                **{
                    label + "_observations": sum(
                        sum(d["class_name"] == label for d in r[name]["observations"])
                        for r in records
                    )
                    for label in ("basketball", "hoop")
                },
            )
        matched = ref_only = candidate_only = 0
        for r in records:
            a = [
                d
                for d in r["rfdetr"]["observations"]
                if d["class_name"] == "basketball"
            ]
            b = [
                d
                for d in r["yolov8n"]["observations"]
                if d["class_name"] == "basketball"
            ]
            used = set()
            for d in a:
                candidates = [
                    (np.linalg.norm(center(d) - center(c)), i)
                    for i, c in enumerate(b)
                    if i not in used
                ]
                best = min(candidates, default=(1, None))
                if best[0] <= 0.015:
                    matched += 1
                    used.add(best[1])
                else:
                    ref_only += 1
            candidate_only += len(b) - len(used)
        result["ball_agreement"] = dict(
            matched=matched,
            rfdetr_only=ref_only,
            yolo_only=candidate_only,
            center_distance_threshold=0.015,
            ground_truth=False,
        )
        out = args.output / source.stem
        out.mkdir(exist_ok=True)
        (out / "frames.json").write_text(json.dumps(records))
        (out / "summary.json").write_text(json.dumps(result, indent=2))
        # Fixed temporal samples plus largest ball-count disagreements, drawn side by side.
        picks = set(np.linspace(0, len(records) - 1, 8, dtype=int).tolist())
        rank = sorted(
            range(len(records)),
            key=lambda i: abs(
                sum(
                    d["class_name"] == "basketball"
                    for d in records[i]["rfdetr"]["observations"]
                )
                - sum(
                    d["class_name"] == "basketball"
                    for d in records[i]["yolov8n"]["observations"]
                )
            ),
            reverse=True,
        )
        for i in rank:
            if all(abs(i - j) >= 8 for j in picks):
                picks.add(i)
            if len(picks) >= 14:
                break
        cap = cv2.VideoCapture(str(source))
        rows = []
        for i in sorted(picks):
            r = records[i]
            cap.set(cv2.CAP_PROP_POS_FRAMES, r["frame_index"])
            ok, frame = cap.read()
            if not ok:
                continue
            panels = []
            for name in ("rfdetr", "yolov8n"):
                panel = frame.copy()
                h, w = panel.shape[:2]
                for d in r[name]["observations"]:
                    if d["class_name"] not in ("basketball", "hoop"):
                        continue
                    x, y, bw, bh = d["bbox"]
                    color = (
                        (0, 255, 255)
                        if d["class_name"] == "basketball"
                        else (0, 255, 0)
                    )
                    cv2.rectangle(
                        panel,
                        (round(x * w), round(y * h)),
                        (round((x + bw) * w), round((y + bh) * h)),
                        color,
                        2,
                    )
                    cv2.putText(
                        panel,
                        "%.2f" % d["confidence"],
                        (round(x * w), max(14, round(y * h) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        color,
                        1,
                    )
                cv2.putText(
                    panel,
                    "%s %dms" % (name, r["timestamp_ms"]),
                    (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                )
                panels.append(cv2.resize(panel, (640, 360)))
            row = np.hstack(panels)
            rows.append(row)
            cv2.imwrite(str(out / ("review-%04d.jpg" % r["frame_index"])), row)
        cap.release()
        for start in range(0, len(rows), 4):
            cv2.imwrite(
                str(out / ("sheet-%d.jpg" % (start // 4))),
                np.vstack(rows[start : start + 4]),
            )
        print(json.dumps(result), flush=True)
        all_results.append(result)
        (args.output / "summary.json").write_text(
            json.dumps(
                dict(
                    yolo_manifest=manifest,
                    device="mps",
                    fallback=False,
                    runtime=dict(
                        python=platform.python_version(),
                        platform=platform.platform(),
                        torch=version("torch"),
                        ultralytics=version("ultralytics"),
                        rfdetr=version("rfdetr"),
                    ),
                    resolution=704,
                    views="full + two overlapping 60% crops",
                    stride=2,
                    warmup=3,
                    timing="paired alternating order, synchronized full detect call including preprocessing/postprocessing; excludes decode/model loading",
                    thresholds=rf.thresholds,
                    results=all_results,
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
