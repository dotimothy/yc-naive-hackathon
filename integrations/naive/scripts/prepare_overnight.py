"""Prepare a hash-bound overnight queue from cached Drive source manifests."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "data/naive"
items = []
sources = []
for directory in ("low-camera-basketball-dataset", "next-basketball-dataset"):
    base = ROOT.parent / directory
    manifest = json.loads((base / "clip-manifest.json").read_text())
    for clip in manifest["clips"]:
        path = base / clip["file_name"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != clip["sha256"]:
            raise ValueError("Source hash mismatch: " + path.name)
        camera = (
            " ".join(
                str(clip.get("qa", {}).get(key, ""))
                for key in (
                    "stationary_review",
                    "stationary_camera",
                    "manual_review",
                    "finished_file_visual_review",
                )
            )
            + " "
            + str(clip.get("camera_review", ""))
            + " "
            + str(clip.get("spectator_review", {}).get("camera", ""))
        )
        stationary = clip.get("qa", {}).get("camera_stationary") is True or any(
            word in camera.lower()
            for word in ("fixed", "tripod", "stationary", "stable")
        )
        if not stationary:
            continue
        credit = f"{clip['channel_name']} — {clip['title']}"
        sources.append(
            dict(
                sha256=clip["sha256"],
                name=clip["file_name"],
                source_url=clip["source_url"],
                credit=credit,
                footer=f"Source: {clip['channel_name']} (see caption)",
                stationary=True,
                authorization="Operator instructed autonomous TikTok publication of these Drive clips on 2026-11-08",
            )
        )
        items.append(
            dict(
                id=clip["source_id"],
                name=clip["file_name"],
                path=str(path),
                sha256=clip["sha256"],
                source_url=clip["drive_url"],
                status="queued",
                stage="discovery",
                camera_evidence=camera.strip(),
            )
        )
for clip in json.loads((FOLDER / "drive-indoor-inventory.json").read_text()):
    if clip["name"] == "basket_S1T1_pre.mp4":
        continue  # Already published in the requested portrait format.
    path = ROOT / "data/trackid3x3" / clip["name"]
    if not path.exists():
        continue
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    sources.append(
        dict(
            sha256=sha,
            name=clip["name"],
            source_url=clip["url"],
            credit="TrackID3x3, Kazuhiro Yamada et al. CC BY 4.0: https://creativecommons.org/licenses/by/4.0/",
            footer="TrackID3x3, CC BY 4.0",
            stationary=True,
            authorization="Dataset CC BY 4.0; operator requested publication",
        )
    )
    items.append(
        dict(
            id=clip["id"],
            name=clip["name"],
            path=str(path),
            sha256=sha,
            source_url=clip["url"],
            status="queued",
            stage="discovery",
            camera_evidence="TrackID3x3 indoor fixed-camera source",
        )
    )
stop = datetime.now(ZoneInfo("America/Los_Angeles")).replace(
    hour=8, minute=0, second=0, microsecond=0
)
state = dict(
    id="stationary-drive-2026-11-08",
    status="queued",
    stop_at=stop.isoformat(),
    total=len(items),
    counts={"queued": len(items)},
    items=items,
)
state_path = FOLDER / "overnight-state.json"
if state_path.exists():
    previous = json.loads(state_path.read_text())
    known = {row["id"]: row for row in previous["items"]}
    state["items"] = [known.get(row["id"], row) for row in items]
    state["status"] = previous["status"]
    state["counts"] = {
        status: sum(row["status"] == status for row in state["items"])
        for status in {row["status"] for row in state["items"]}
    }
(FOLDER / "publishing-sources.json").write_text(
    json.dumps(dict(items=sources), indent=2)
)
state_path.write_text(json.dumps(state, indent=2))
print(json.dumps(dict(total=len(items), stop_at=state["stop_at"])))
