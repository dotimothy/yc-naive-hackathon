#!/usr/bin/env python3
"""Retrieve the pinned official YOLO26 Nano pose weights; verify before model loading."""

import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA256 = "eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9"
MODEL_BYTES = 7878574
URL = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt"


def download(folder=None):
    folder = Path(folder or ROOT / "data/models")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "yolo26n-pose.pt"
    if (
        not target.exists()
        or hashlib.sha256(target.read_bytes()).hexdigest() != MODEL_SHA256
    ):
        temporary = target.with_suffix(".download")
        try:
            with (
                urllib.request.urlopen(URL, timeout=60) as response,
                temporary.open("wb") as out,
            ):
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            if (
                temporary.stat().st_size != MODEL_BYTES
                or hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256
            ):
                raise ValueError("YOLO pose checkpoint checksum mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    (folder / "yolo26n-pose-manifest.json").write_text(
        json.dumps(
            dict(
                model="ultralytics/yolo26n-pose",
                release="v8.4.0",
                source=URL,
                sha256=MODEL_SHA256,
                bytes=MODEL_BYTES,
                license="AGPL-3.0 / Ultralytics Enterprise",
                keypoints="COCO-17",
            ),
            indent=2,
        )
        + "\n"
    )
    return target


if __name__ == "__main__":
    print(download(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
