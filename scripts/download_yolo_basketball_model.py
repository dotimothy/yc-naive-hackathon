#!/usr/bin/env python3
"""Download pinned official YOLOE-26 Nano prompt-free weights for local inference."""

import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = "yoloe-26n-seg-pf.pt"
MODEL_SHA256 = "ae805a9ac11c85214588b32da8248d68a516e1d071465e1e86a03cbfadb66ba7"
MODEL_BYTES = 16271345
MODEL_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/" + MODEL_NAME
)
MANIFEST_NAME = "yoloe-26n-basketball-manifest.json"


def manifest():
    return dict(
        model="ultralytics/yoloe-26n-seg-pf",
        architecture="YOLOE-26n prompt-free instance segmentation",
        release="v8.4.0",
        source=MODEL_URL,
        sha256=MODEL_SHA256,
        bytes=MODEL_BYTES,
        license="AGPL-3.0 / Ultralytics Enterprise",
        vocabulary="RAM++ 4585",
        classes={0: "basketball", 1: "hoop"},
        source_classes={310: "basketball", 315: "basketball hoop"},
        basketball_finetuned=False,
        text_encoder_required=False,
    )


def download(folder=None):
    folder = Path(folder or ROOT / "data/models")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MODEL_NAME
    if (
        not target.is_file()
        or target.stat().st_size != MODEL_BYTES
        or hashlib.sha256(target.read_bytes()).hexdigest() != MODEL_SHA256
    ):
        temporary = target.with_suffix(".download")
        try:
            with (
                urllib.request.urlopen(MODEL_URL, timeout=60) as response,
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
                raise ValueError("YOLOE-26 basketball checkpoint checksum mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    (folder / MANIFEST_NAME).write_text(json.dumps(manifest(), indent=2) + "\n")
    return target


if __name__ == "__main__":
    print(download(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
