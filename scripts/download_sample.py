#!/usr/bin/env python3
"""Download one attributed TrackID3x3 sample; never commits footage."""

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/trackid3x3/basket_S1T1_pre.mp4"
EXPECTED = "8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad"
URL = "https://drive.google.com/uc?export=download&id=1ONVOjVBPmNdpQWQ_9WJCNYte__4NJ5Ol"
if not DEST.exists():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != EXPECTED:
        raise ValueError("Sample integrity check failed; no file saved")
    DEST.write_bytes(data)
if hashlib.sha256(DEST.read_bytes()).hexdigest() != EXPECTED:
    raise ValueError("Existing sample differs from pinned hash")
print(DEST)
print("TrackID3x3, Yamada et al., CC BY 4.0; see docs/datasets/TRACKID3X3.md")

# Optional 12-second derivative of the outdoor full-3x3-court fixed camera.
# Range support is checked first so the 1.3 GB original is not downloaded blindly.

if "--outdoor" in sys.argv:
    import re
    import subprocess
    import urllib.parse
    import json

    target = DEST.parent / "outdoor_stationary_12s.mp4"
    source_id = "1B4WgkrIrPaIe-kIRd5Kq8XouUmezolL_"
    if not target.exists():
        html = (
            urllib.request.urlopen(
                "https://drive.google.com/uc?export=download&id=" + source_id,
                timeout=30,
            )
            .read()
            .decode()
        )
        fields = dict(re.findall(r'name="([^"]+)" value="([^"]+)"', html))
        if fields.get("id") != source_id:
            raise ValueError("Unexpected download confirmation")
        url = "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(
            fields
        )
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Range": "bytes=0-0"}), timeout=30
        ) as response:
            if response.status != 206:
                raise ValueError("Range requests unavailable; skipping large source")
        temporary = target.with_name("outdoor.tmp.mp4")
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                url,
                "-t",
                "12",
                "-an",
                "-vf",
                "scale=1280:-2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                str(temporary),
            ],
            check=True,
            timeout=120,
        )
        temporary.replace(target)
    manifest = dict(
        source_url="https://drive.google.com/file/d/" + source_id + "/view",
        license="CC BY 4.0",
        attribution="TrackID3x3, Yamada et al.",
        transform="first 12 seconds; scaled to 1280 width; audio removed; H.264 CRF 23",
        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    (target.parent / "outdoor_source_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(target)
