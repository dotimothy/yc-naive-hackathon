#!/usr/bin/env python3
"""Download a pinned, attributed basketball detector; safely extract tensor weights."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import torch

ROOT = Path(__file__).resolve().parents[1]
REPO = "GabrieleGiudici/E-BARD-detection-models"
REVISION = "aae99b56aadd2f1c40f1a402fd465dd666fbc8da"
FILE = "BODD_rf-detr-nano_0000/checkpoint_best_total.pth"
SHA256 = "759968ecf8f83663c85de3d881713e072f4a9dd0ea2f136b082878b1e4fe2400"
CLASS_NAMES = {0: "basketball", 1: "hoop", 2: "player", 3: "referee"}


def main():
    folder = ROOT / "data/models"
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / "ebard-rfdetr-nano.pth"
    if not original.exists():
        temporary = original.with_suffix(".tmp")
        with (
            urllib.request.urlopen(
                "https://huggingface.co/" + REPO + "/resolve/" + REVISION + "/" + FILE,
                timeout=60,
            ) as source,
            temporary.open("wb") as target,
        ):
            total = 0
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > 125 * 1024 * 1024:
                    raise ValueError("Model exceeds pinned size bound")
                target.write(block)
        temporary.replace(original)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    if digest != SHA256:
        raise ValueError("Basketball model hash mismatch")
    # Permit only Namespace metadata; never use unrestricted pickle loading of downloads.
    with torch.serialization.safe_globals([argparse.Namespace]):
        checkpoint = torch.load(original, map_location="cpu", weights_only=True)
    state = checkpoint["model"]
    if not isinstance(state, dict) or not all(
        isinstance(k, str) and isinstance(v, torch.Tensor) for k, v in state.items()
    ):
        raise ValueError("Expected tensor-only state dictionary")
    sanitized = folder / "ebard-rfdetr-nano-state.pth"
    torch.save({"model": state}, sanitized)
    manifest = dict(
        repository=REPO,
        revision=REVISION,
        file=FILE,
        sha256=SHA256,
        sanitized_sha256=hashlib.sha256(sanitized.read_bytes()).hexdigest(),
        classes=CLASS_NAMES,
        license="CC BY 4.0",
        attribution="Gabriele Giudici, E-BARD Basketball Object Detection Models",
        architecture_license="RF-DETR Apache-2.0",
        class_mapping_source="https://github.com/GabrieleGiudic/E-BARD/blob/f8314e03179b6558fb6ef0d26a9135fdc3572b78/detection/eval/yolo_vs_detr.out",
        resolution=704,
    )
    (folder / "ebard-rfdetr-nano-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(sanitized)


if __name__ == "__main__":
    main()
