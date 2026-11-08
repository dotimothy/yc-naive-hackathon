"""Deterministic local inference worker, isolated from API and persistence."""

import json
import logging
import os
import time
from pathlib import Path
import cv2
import numpy as np
import torch
from workers.video.runtime import (
    select_device,
    get_model,
    infer,
    POSE_MODEL,
    KEYPOINT_THRESHOLD,
)
from workers.video.tracker import ContinuityTracker, iou
from workers.video.teams import TeamGrouper, flag_non_players, torso_colour_feature
from workers.video.identity import (
    PlayerIdentityTracker,
    uniform_descriptor,
    propose_tracklet_merges,
)
from workers.video.basketball_pass import BasketballPass
from workers.video.cancellation import check_cancelled
from workers.video.highlights import build_highlights, render_highlights, split_ranges
from workers.video.shots import ShotDetector, classify_shots
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
    original_emit = emit

    def emit(kind, payload):
        check_cancelled(options.get("cancel_path"))
        original_emit(kind, payload)

    check_cancelled(options.get("cancel_path"))
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
    source_hash = sha256(source_path)
    ball = None
    ball_report = None
    ball_stride = options.get("ball_stride", 2)
    ball_status = "disabled"
    if options.get("basketball"):
        emit("basketball.updated", dict(status="running", report=None))
        try:
            ball = BasketballPass(
                metadata,
                source_hash,
                output_dir / "basketball",
                device,
                ball_stride,
                emit,
            )
            ball_status = "running"
            emit("basketball.updated", dict(status="running", report=ball.snapshot()))
        except Exception as exc:
            logging.exception("Ball/hoop initialization failed")
            ball_status = "failed"
            emit(
                "basketball.updated",
                dict(
                    status="failed",
                    report=None,
                    error="Ball detector initialization failed: " + type(exc).__name__,
                ),
            )
    elif options.get("basketball_unavailable"):
        ball_status = "unavailable"
        emit(
            "basketball.updated",
            dict(
                status="unavailable",
                report=None,
                error=options["basketball_unavailable"],
            ),
        )
    model, model_load_seconds, model_cache_hit = get_model(device)
    model.confidence = options.get("confidence", 0.25)
    tracker, samples, processed = ContinuityTracker(), [], 0
    teams = TeamGrouper()
    track_descriptors = {}
    identities = PlayerIdentityTracker(game, reconnection="review")
    shot_detector = ShotDetector()
    shot_detection_seconds = 0.0
    shot_start_ms = 0
    trackers = [tracker]
    identity_trackers = [("0", identities)]
    team_groupers = {"0": teams}
    cap = cv2.VideoCapture(str(source_path))
    inference_time = 0.0
    transfer_time = 0.0
    first_inference_seconds = None
    replay_start = time.perf_counter()
    index = 0
    retrieved_frames = 0
    last_timestamp = -1
    try:
        while True:
            check_cancelled(options.get("cancel_path"))
            ok = cap.grab()
            if not ok:
                break
            pose_due = index % stride == 0
            ball_due = (
                ball is not None
                and ball_status == "running"
                and index % ball_stride == 0
            )
            # Inspect every decoded source frame without opening a second decoder.
            ok, frame = cap.retrieve()
            if not ok:
                break
            retrieved_frames += 1
            reported = round(cap.get(cv2.CAP_PROP_POS_MSEC))
            timestamp = (
                reported
                if reported > last_timestamp
                else round(index / metadata["fps"] * 1000)
            )
            if timestamp <= last_timestamp:
                raise ValueError("Non-monotonic source timestamps")
            last_timestamp = timestamp
            before_shot = time.perf_counter()
            cut = shot_detector.update(frame, timestamp)
            shot_detection_seconds += time.perf_counter() - before_shot
            shot_id = str(shot_detector.shot_id)
            if cut:
                shot_start_ms = timestamp
                tracker = ContinuityTracker()
                trackers.append(tracker)
                identities = PlayerIdentityTracker(
                    game + "-shot" + shot_id, reconnection="review"
                )
                identity_trackers.append((shot_id, identities))
                teams = TeamGrouper()
                team_groupers[shot_id] = teams
                if ball is not None and ball_status == "running":
                    ball.begin_shot(shot_id, timestamp)
            if cut or index == 0:
                live_shots = shot_detector.finalize(metadata["duration_ms"])
                for item in live_shots:
                    item.update(
                        shot_id=str(item["shot_id"]),
                        category="uncertain",
                        requires_review=True,
                        review_status="pending",
                    )
                emit("shots.updated", dict(items=live_shots))
            if not (pose_due or ball_due):
                index += 1
                continue
            if options.get("replay_paced"):
                time.sleep(
                    max(0, timestamp / 1000 - (time.perf_counter() - replay_start))
                )
            if ball_due:
                try:
                    ball.observe(frame, timestamp)
                except Exception as exc:
                    logging.exception(
                        "Ball/hoop inference stopped; player tracking continues"
                    )
                    ball_status = "failed"
                    ball.flush()
                    emit(
                        "basketball.updated",
                        dict(
                            status="failed",
                            report=ball.snapshot(),
                            error="Ball detector failed: " + type(exc).__name__,
                        ),
                    )
            emit(
                "analysis.progress",
                dict(
                    processed_frames=processed,
                    source_frame=index,
                    source_timestamp_ms=timestamp,
                    basketball_processed_frames=ball.frames if ball else 0,
                ),
            )
            if not pose_due:
                index += 1
                continue
            result, seconds, copy_seconds = infer(model, frame, device)
            inference_time += seconds
            transfer_time += copy_seconds
            if first_inference_seconds is None:
                first_inference_seconds = seconds
            keep = result["scores"] >= options.get("confidence", 0.25)
            if options.get("court_polygon") and shot_id == "0":
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
            identity_ids = (
                ids if shot_id == "0" else ["s" + shot_id + ":" + str(i) for i in ids]
            )
            associations = identities.update(
                identity_ids, boxes, descriptors, timestamp
            )
            if shot_id != "0":
                for association in associations:
                    association["player_label"] = (
                        "Shot "
                        + str(int(shot_id) + 1)
                        + " · "
                        + association["player_label"]
                    )
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
                if "keypoint_scores" in result:
                    for point, confidence in zip(
                        points, result["keypoint_scores"][keep][j]
                    ):
                        point["confidence"] = float(confidence)
                batch.append(
                    dict(
                        game_id=game,
                        shot_id=shot_id,
                        shot_start_ms=shot_start_ms,
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
    shots = shot_detector.finalize(metadata["duration_ms"])
    for shot in shots:
        shot["shot_id"] = str(shot["shot_id"])
    shots = classify_shots(shots, samples)
    for shot in shots:
        shot["review_status"] = "pending"
        shot["reasons"] = shot.get("category_reasons", [])
    emit("shots.updated", dict(items=shots))
    aspect = metadata["width"] / metadata["height"]
    team_map, role_flags, merges, team_summaries = {}, {}, [], {}
    for shot in shots:
        sid = shot["shot_id"]
        scene_samples = [s for s in samples if s["shot_id"] == sid]
        court = options.get("court_polygon") if sid == "0" else None
        first_roles = flag_non_players(
            scene_samples, aspect=aspect, court_polygon=court
        )
        scene_teams, summary = team_groupers[sid].assign(
            exclude=[k for k, v in first_roles.items() if v["role_status"] != "unknown"]
        )
        team_summaries[sid] = summary
        team_map.update(scene_teams)
        role_flags.update(
            flag_non_players(
                scene_samples,
                aspect=aspect,
                court_polygon=court,
                team_assignments=scene_teams,
            )
        )
        prototypes = {}
        for key in {s["track_id"] for s in scene_samples}:
            values = track_descriptors.get(key, [])
            if len(values) >= 3:
                vector = np.mean(values, axis=0)
                prototypes[key] = vector / max(1e-8, np.linalg.norm(vector))
        merges.extend(
            propose_tracklet_merges(
                scene_samples, descriptors=prototypes, teams=scene_teams, aspect=aspect
            )
        )
    team_summary = (
        team_summaries.get("0", {}) if len(shots) == 1 else dict(by_shot=team_summaries)
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
    if ball is not None and ball_status == "running":
        ball_report = ball.finish(samples, index, shots=shots)
        ball_status = "succeeded"
    emit("phase", {"phase": "score"})
    bundle = build_highlights(
        samples,
        metadata["duration_ms"],
        metadata["fps"],
        stride,
        options["job_id"],
        shots=shots,
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
    ranges = split_ranges(ranges, shots)
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
            shot_id=cut.get("shot_id"),
            path=str(path.resolve()),
            identity_verified=False,
            **qa,
        )
        # Default windows are explicitly review segments, not inferred basketball events.
        event = dict(
            event_id=clip["event_id"],
            game_id=game,
            chunk_id=str(i),
            shot_id=cut.get("shot_id"),
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
        automatic_output=dict(
            status="clips_ready" if clips else "no_eligible_clips",
            clip_count=len(clips),
            requires_human_action=False,
            reason=(
                "Automatic source-verified clips are ready. Identities and basketball outcomes remain provisional."
                if clips
                else "No camera-safe gameplay ranges qualified for automatic clips. The analysis is complete; no approval is pending."
            ),
        ),
        source_sha256=source_hash,
        analysis_mode="shared_decoder",
        retrieved_frames=retrieved_frames,
        basketball_status=ball_status,
        basketball=ball_report,
        metadata=metadata,
        shots=shots,
        shot_detection_seconds=shot_detection_seconds,
        scene_detection_method="hard-cut-heuristic-v1",
        court_polygon_scope="first_shot_only",
        requested_device=requested_device,
        device=device,
        execution_mode=device,
        model_cache_hit=model_cache_hit,
        model_load_seconds=model_load_seconds,
        inference_seconds=inference_time,
        output_transfer_seconds=transfer_time,
        first_inference_seconds=first_inference_seconds,
        precision="float32",
        model=POSE_MODEL,
        pose_model_manifest=getattr(model, "manifest", None),
        pose_image_size=getattr(model, "image_size", None),
        pose_confidence_threshold=options.get("confidence", 0.25),
        pose_end_to_end=True,
        keypoint_visibility_threshold=KEYPOINT_THRESHOLD,
        tracker="Continuity + review-only fragment reconnection",
        tracker_version="continuity-review-fragments-v1",
        continuity_stats={
            key: sum(t.stats.get(key, 0) for t in trackers) for key in tracker.stats
        },
        team_summary=team_summary,
        role_flags=role_flags,
        merge_proposals=merges,
        identity_summary=dict(
            method="camera-shot-scoped",
            players=sum(i.summary()["players"] for _, i in identity_trackers),
            identity_confirmed=False,
        ),
        identity_summary_by_shot=[
            dict(shot_id=sid, **i.summary()) for sid, i in identity_trackers
        ],
        identity_merge_suggestions=[
            s for _, i in identity_trackers for s in i.reconnection_suggestions()
        ],
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
