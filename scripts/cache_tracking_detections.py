"""Download fixed TrackID3x3 indoor clips and cache pretrained pose detections.

Media and detections stay under data/claude_tracking/ (ignored by Git). Each
video's SHA-256 is recorded on first download and verified on every later run.
Detection mirrors the pipeline: torchvision keypoint R-CNN (COCO_V1, pretrained,
no training), score >= 0.75, every 5th frame. Sequences run one at a time under
a lock file so inference is serialized.

  .venv/bin/python scripts/cache_tracking_detections.py [--device cpu|mps]

Dataset: TrackID3x3, Yamada et al., CC BY 4.0 (videos and annotations),
https://github.com/open-starlab/TrackID3x3 . Transformation: frame sampling and
model inference only; source files are not modified.
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.video.runtime import infer, load_model, select_device  # noqa: E402

OUT = ROOT / "data" / "claude_tracking"
GT_DIR = ROOT / "data" / "trackid3x3" / "ground_truth" / "Indoor" / "MOT"
INDOOR_FOLDER = (
    "https://drive.google.com/drive/folders/1q0L_Mmf5cwb-Yv51NTjgQcF12Is0fYCB"
)
# Fixed before any results were seen. Rule: S1T1 is the original development
# clip; every other session contributes its longest annotated sequence (ties:
# lower trial). S2 is development; S3, S4 and S6 are held out.
SEQUENCES = {
    "basket_S1T1_pre": dict(split="dev", drive_id="1ONVOjVBPmNdpQWQ_9WJCNYte__4NJ5Ol"),
    "basket_S2T6_pre": dict(split="dev", drive_id="1A_mD5nadl4603dE_R9zeqYfEBNMBw28I"),
    "basket_S3T4_pre": dict(
        split="heldout", drive_id="1sRF-LZZG9VbPIh7SOTXearyrrzA9B-eg"
    ),
    "basket_S4T2_post": dict(
        split="heldout", drive_id="12VCupNldXtXU1pYMtOK_eC6c7-2LUKrw"
    ),
    "basket_S6T2_post": dict(
        split="heldout", drive_id="1BV9izBdD3I1EySL68JCXbaBj_cIAkftB"
    ),
}
STRIDE, CONFIDENCE, MAX_BYTES = 5, 0.75, 20 * 1024 * 1024
NAMES = [
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(name, spec, manifest):
    target = OUT / "media" / (name + ".mp4")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        url = "https://drive.google.com/uc?export=download&id=" + spec["drive_id"]
        request = urllib.request.Request(url, headers={"User-Agent": "scoutfilm-eval"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or data[4:8] != b"ftyp":
            raise SystemExit(
                f"{name}: unexpected download (not a small MP4); nothing saved"
            )
        temporary = target.with_suffix(".part")
        temporary.write_bytes(data)
        temporary.rename(target)
    digest = sha256(target)
    known = manifest.get(name, {}).get("video_sha256")
    if known and known != digest:
        raise SystemExit(f"{name}: video hash changed ({digest} != {known})")
    gt = GT_DIR / (name + ".txt")
    manifest[name] = dict(
        split=spec["split"],
        video=str(target.relative_to(ROOT)),
        video_sha256=digest,
        video_bytes=target.stat().st_size,
        source=f"https://drive.google.com/file/d/{spec['drive_id']}/view",
        source_folder=INDOOR_FOLDER,
        annotation=str(gt.relative_to(ROOT)),
        annotation_sha256=sha256(gt),
        license="CC BY 4.0, TrackID3x3 (Yamada et al.), pinned repo 9822a3dd",
    )
    return target


def detect(name, video, model, device):
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    samples, frames, index, seconds = [], [], 0, 0.0
    try:
        while cap.grab():
            if index % STRIDE == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                timestamp = round(index / fps * 1000)
                tensor = (
                    torch.from_numpy(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    .permute(2, 0, 1)
                    .float()
                    .div(255)
                    .to("cpu" if device == "mps" else device)
                )
                result, took, _ = infer(model, tensor, device)
                seconds += took
                keep = result["scores"] >= CONFIDENCE
                frames.append(
                    dict(
                        index=index, timestamp_ms=timestamp, detections=int(keep.sum())
                    )
                )
                for box, kp, score in zip(
                    result["boxes"][keep].numpy(),
                    result["keypoints"][keep].numpy(),
                    result["scores"][keep].numpy(),
                ):
                    x1, y1, x2, y2 = map(float, box)
                    samples.append(
                        dict(
                            timestamp_ms=timestamp,
                            bbox=[
                                x1 / width,
                                y1 / height,
                                (x2 - x1) / width,
                                (y2 - y1) / height,
                            ],
                            keypoints=[
                                dict(
                                    name=n,
                                    x=max(0, min(1, float(p[0]) / width)),
                                    y=max(0, min(1, float(p[1]) / height)),
                                    visibility=int(p[2]),
                                )
                                for n, p in zip(NAMES, kp)
                            ],
                            confidence=float(score),
                            origin="model",
                        )
                    )
            index += 1
    finally:
        cap.release()
    folder = OUT / "detections" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracks.json").write_text(json.dumps(samples))
    report = dict(
        metadata=dict(
            width=width,
            height=height,
            fps=fps,
            frame_count=index,
            duration_ms=round(index / fps * 1000),
        ),
        model="torchvision/keypointrcnn_resnet50_fpn/COCO_V1",
        device=device,
        stride=STRIDE,
        confidence=CONFIDENCE,
        sampled_frames=frames,
        detections=len(samples),
        inference_seconds=round(seconds, 2),
        inference_fps=round(len(frames) / seconds, 3) if seconds else None,
    )
    (folder / "report.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--force", action="store_true", help="re-run cached detections")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    lock = OUT / ".inference.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"Another cache run holds {lock}; remove it if stale")
    try:
        manifest_path = OUT / "sources" / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        )
        device, model = select_device(args.device), None
        for name, spec in SEQUENCES.items():
            if args.only and name not in args.only:
                continue
            video = fetch(name, spec, manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2))
            if (OUT / "detections" / name / "tracks.json").exists() and not args.force:
                print(name, "cached")
                continue
            if model is None:
                model = load_model(device)
                model.roi_heads.score_thresh = CONFIDENCE
            started = time.perf_counter()
            report = detect(name, video, model, device)
            print(
                name,
                spec["split"],
                len(report["sampled_frames"]),
                "frames",
                report["detections"],
                "detections",
                round(time.perf_counter() - started, 1),
                "s",
                report["inference_fps"],
                "fps",
            )
    finally:
        os.close(fd)
        lock.unlink()


if __name__ == "__main__":
    main()
