"""Deterministic local inference worker, isolated from API and persistence."""

import json
import os
import time
from pathlib import Path
import cv2
import numpy as np
import torch
from workers.video.runtime import select_device, get_model, infer
from workers.video.tracker import ContinuityTracker, iou
from workers.video.teams import TeamGrouper, flag_non_players, torso_colour_feature
from workers.video.identity import (
    PlayerIdentityTracker,
    uniform_descriptor,
    propose_tracklet_merges,
)
from workers.video.highlights import build_highlights, render_highlights
from workers.render.media import probe, render_clip, verify_clip, sha256

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


def process_video(source_path, output_dir, options, emit):
    if options.get("sport", "basketball") != "basketball":
        raise ValueError("Only basketball is implemented")
    started = time.perf_counter()
    metadata = probe(source_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    game = options["game_id"]
    stride = options.get("stride", 5)
    requested_device = options.get("device") or os.environ.get(
        "SCOUTFILM_DEVICE", "auto"
    )
    device = select_device(options.get("device"))
    emit("phase", {"phase": "track"})
    model, model_load_seconds, model_cache_hit = get_model(device)
    # Reject low-score detections before the expensive pose head, not after it.
    # A single worker owns the cached model and sets this for every job.
    model.roi_heads.score_thresh = options.get("confidence", 0.75)
    tracker, samples, processed = ContinuityTracker(), [], 0
    teams = TeamGrouper()
    track_descriptors = {}
    identities = PlayerIdentityTracker(game, reconnection="review")
    cap = cv2.VideoCapture(str(source_path))
    inference_time = 0.0
    transfer_time = 0.0
    first_inference_seconds = None
    replay_start = time.perf_counter()
    index = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if index % stride:
                index += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            timestamp = round(index / metadata["fps"] * 1000)
            if options.get("replay_paced"):
                time.sleep(
                    max(0, timestamp / 1000 - (time.perf_counter() - replay_start))
                )
            tensor = (
                torch.from_numpy(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                .permute(2, 0, 1)
                .float()
                .div(255)
                .to("cpu" if device == "mps" else device)
            )
            result, seconds, copy_seconds = infer(model, tensor, device)
            inference_time += seconds
            transfer_time += copy_seconds
            if first_inference_seconds is None:
                first_inference_seconds = seconds
            keep = result["scores"] >= options.get("confidence", 0.75)
            if options.get("court_polygon"):
                polygon = np.array(options["court_polygon"], dtype=np.float32)
                in_court = [
                    cv2.pointPolygonTest(
                        polygon,
                        (
                            float((b[0] + b[2]) / 2 / metadata["width"]),
                            float(b[3] / metadata["height"]),
                        ),
                        False,
                    )
                    >= 0
                    for b in result["boxes"]
                ]
                keep = keep & torch.tensor(in_court, dtype=torch.bool)
            boxes = result["boxes"][keep].numpy()
            descriptors = [uniform_descriptor(frame, box) for box in boxes]
            ids = tracker.update(boxes, descriptors, timestamp_ms=timestamp)
            associations = identities.update(ids, boxes, descriptors, timestamp)
            batch = []
            w, h = metadata["width"], metadata["height"]
            for j, (box, track_id) in enumerate(zip(boxes, ids)):
                x1, y1, x2, y2 = map(float, box)
                kp = result["keypoints"][keep][j].numpy()
                contaminated = any(
                    j != k and iou(box, other) > 0.3 for k, other in enumerate(boxes)
                )
                track_key = associations[j]["track_id"]
                teams.observe(
                    track_key,
                    None if contaminated else torso_colour_feature(frame, box, kp),
                    timestamp,
                )
                if descriptors[j] is not None and not contaminated:
                    track_descriptors.setdefault(track_key, []).append(descriptors[j])
                # Visibility flags are not calibrated pose probabilities; keep them explicit.
                points = [
                    {
                        "name": name,
                        "x": max(0, min(1, float(p[0]) / w)),
                        "y": max(0, min(1, float(p[1]) / h)),
                        "visibility": int(p[2]),
                    }
                    for name, p in zip(NAMES, kp)
                ]
                batch.append(
                    dict(
                        game_id=game,
                        **associations[j],
                        timestamp_ms=timestamp,
                        bbox=[x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h],
                        keypoints=points,
                        confidence=float(result["scores"][keep][j]),
                        origin="model",
                    )
                )
            samples.extend(batch)
            processed += 1
            emit(
                "tracks.batch",
                {
                    "items": batch,
                    "processed_frames": processed,
                    "source_frame": index,
                    "source_timestamp_ms": timestamp,
                },
            )
            index += 1
    finally:
        cap.release()
    if not samples:
        raise ValueError(
            "No person tracks found; use clearer footage or a lower threshold"
        )
    if index == 0:
        raise ValueError("No decodable video frames")
    aspect = metadata["width"] / metadata["height"]
    first_roles = flag_non_players(
        samples, aspect=aspect, court_polygon=options.get("court_polygon")
    )
    team_map, team_summary = teams.assign(
        exclude=[k for k, v in first_roles.items() if v["role_status"] != "unknown"]
    )
    role_flags = flag_non_players(
        samples,
        aspect=aspect,
        court_polygon=options.get("court_polygon"),
        team_assignments=team_map,
    )
    prototypes = {}
    for key, values in track_descriptors.items():
        if len(values) >= 3:
            vector = np.mean(values, axis=0)
            prototypes[key] = vector / max(1e-8, np.linalg.norm(vector))
    merges = propose_tracklet_merges(
        samples, descriptors=prototypes, teams=team_map, aspect=aspect
    )
    enriched = {}
    for sample in samples:
        key = sample["track_id"]
        fields = dict(
            team_label=team_map.get(key, {}).get("team", "unknown"),
            role_status=role_flags.get(key, {}).get("role_status", "unknown"),
        )
        sample.update(fields)
        enriched[key] = fields
    emit("tracks.enriched", dict(assignments=enriched))
    emit("phase", {"phase": "score"})
    bundle = build_highlights(
        samples, metadata["duration_ms"], metadata["fps"], stride, options["job_id"]
    )
    clips, events = render_highlights(source_path, output_dir, bundle, game, emit)
    emit("phase", {"phase": "render"})
    ranges = options.get("candidate_ranges") or (
        []
        if clips
        else [
            dict(
                start_ms=0,
                end_ms=metadata["duration_ms"] // 2,
                event_type="review_segment",
            ),
            dict(
                start_ms=metadata["duration_ms"] // 2,
                end_ms=metadata["duration_ms"],
                event_type="review_segment",
            ),
        ]
    )
    for i, cut in enumerate(ranges):
        start, end = cut["start_ms"], cut["end_ms"]
        path = render_clip(source_path, start, end, output_dir / ("clip_%d.mp4" % i))
        emit("phase", {"phase": "verify"})
        qa = verify_clip(source_path, path, start, end)
        clip = dict(
            clip_id="%s-%d" % (options["job_id"], i),
            game_id=game,
            event_id="%s-event-%d" % (options["job_id"], i),
            source_start_ms=start,
            source_end_ms=end,
            path=str(path.resolve()),
            identity_verified=False,
            **qa,
        )
        # Default windows are explicitly review segments, not inferred basketball events.
        event = dict(
            event_id=clip["event_id"],
            game_id=game,
            chunk_id=str(i),
            start_ms=start,
            end_ms=end,
            event_type=cut["event_type"],
            track_id=None,
            jersey_candidate=None,
            confidence=None,
            evidence_uri=None,
            clip_uri=None,
            verified=False,
            origin="coach_annotation"
            if options.get("candidate_ranges")
            else "review_window",
        )
        clips.append(clip)
        events.append(event)
        emit("clip.updated", clip)
        emit("event.created", event)
    elapsed = time.perf_counter() - started
    report = dict(
        source_sha256=sha256(source_path),
        metadata=metadata,
        requested_device=requested_device,
        device=device,
        execution_mode="mps-hybrid" if device == "mps" else device,
        model_cache_hit=model_cache_hit,
        model_load_seconds=model_load_seconds,
        inference_seconds=inference_time,
        output_transfer_seconds=transfer_time,
        first_inference_seconds=first_inference_seconds,
        precision="float32",
        model="torchvision/keypointrcnn_resnet50_fpn/COCO_V1",
        tracker="Continuity + review-only fragment reconnection",
        tracker_version="continuity-review-fragments-v1",
        continuity_stats=tracker.stats,
        team_summary=team_summary,
        role_flags=role_flags,
        merge_proposals=merges,
        identity_summary=identities.summary(),
        identity_merge_suggestions=identities.reconnection_suggestions(),
        stride=stride,
        court_polygon=options.get("court_polygon"),
        decoded_frames=index,
        processed_frames=processed,
        track_samples=len(samples),
        track_ids=len(set(s["track_id"] for s in samples)),
        inference_fps=processed / inference_time,
        elapsed_seconds=elapsed,
        source_fps=metadata["fps"],
        realtime_factor=(metadata["duration_ms"] / 1000) / elapsed,
        clips=clips,
        events=events,
        highlight_summary=dict(
            players=len(bundle["players"]),
            candidates=len(bundle["candidates"]),
            selected=sum(c["selected"] for c in bundle["candidates"]),
            score_kind="highlight_priority",
        ),
    )
    (output_dir / "tracks.json").write_text(json.dumps(samples))
    temporary = output_dir / "report.tmp.json"
    temporary.write_text(json.dumps(report, indent=2))
    temporary.replace(output_dir / "report.json")
    emit("phase", {"phase": "complete"})
    return report
