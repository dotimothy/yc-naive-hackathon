#!/usr/bin/env python3
"""EXPERIMENTAL: cross-frame batching of the production ball/hoop views.

Every strategy runs the same three production views per source frame (full frame
plus two fixed 60% crops), with the same checkpoint, thresholds and per-frame
suppression. Only the number of source frames per RF-DETR ``predict()`` call
changes: ``fb1`` (3 images, current production), ``fb2`` (6), ``fb4`` (12).

Timing is paired and warm: frames are decoded once into groups of
lcm(frame batches); each group runs through every strategy back-to-back with the
order rotated per group. Before every group the local job databases are re-read
and the run aborts if an analysis became active.

Subcommands:
  preflight  Check for active jobs/workers; exit 1 if blocked. Loads no model.
  run        Prints a plan unless --execute is given.

Output comparisons are against ``fb1`` (production call shape), not ground truth.
"Coverage" is processed frames with >=1 thresholded ball observation; not accuracy.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workers.video.batched_ball_detector import (  # noqa: E402
    BatchedBallDetector,
    production_views,
)
from workers.video.shot_candidates import (  # noqa: E402
    BallTrajectories,
    propose_shots,
    stationary_hoops,
)

ACTIVE = ("queued", "running", "cancelling")
JOB_KINDS = ("job", "basketball_job", "highlights")
WORKER_PATTERNS = (
    "scripts/run_analysis.py",
    "analyze_basketball.py",
    "benchmark_devices.py",
    "validate_pose_migration.py",
    "cache_tracking_detections.py",
    "benchmark_ball_views.py run",
    "benchmark_ball_batches.py run",
)
OUTPUT = ROOT / "data/benchmarks/ball_batches"
LOCK = ROOT / "data/benchmarks/ball_views/.gpu.lock"  # shared with the view benchmark
BBOX_TOLERANCE = 1e-4  # normalized (0.128 px at 1280 wide)
CONFIDENCE_TOLERANCE = 1e-4
PROCESS_CHECK_EVERY = 8  # groups


# ---------------------------------------------------------------- guard


def candidate_databases(extra=()):
    paths = {p.resolve() for p in (ROOT / "data").glob("*/scoutfilm.sqlite3")}
    if os.environ.get("SCOUTFILM_DATA"):
        paths.add((Path(os.environ["SCOUTFILM_DATA"]) / "scoutfilm.sqlite3").resolve())
    paths.update(Path(p).resolve() for p in extra)
    return sorted(p for p in paths if p.exists())


def active_jobs(database):
    """Read-only scan; an unreadable database is a blocker (fail closed)."""
    import sqlite3

    try:
        db = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
        try:
            rows = db.execute(
                "SELECT kind,id,value FROM records WHERE kind IN (?,?,?)", JOB_KINDS
            ).fetchall()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return [dict(database=str(database), error="unreadable: %s" % exc)]
    found = []
    for kind, record_id, value in rows:
        try:
            status = json.loads(value).get("status")
        except (ValueError, AttributeError):
            continue
        if status in ACTIVE:
            found.append(
                dict(database=str(database), kind=kind, id=record_id, status=status)
            )
    return found


def competing_processes():
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return [dict(error="process listing failed: %s" % exc)]
    found = []
    for line in listing.splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) in (os.getpid(), os.getppid()):
            continue
        if any(pattern in command for pattern in WORKER_PATTERNS):
            found.append(dict(pid=int(pid), command=command[:200]))
    return found


def lock_holder():
    if not LOCK.exists():
        return None
    try:
        pid = int(LOCK.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError):
        return None
    except PermissionError:
        return -1


def preflight(extra=(), processes=True):
    databases = candidate_databases(extra)
    jobs = [job for database in databases for job in active_jobs(database)]
    others = competing_processes() if processes else []
    holder = lock_holder()
    blockers = jobs + others
    if holder and holder != os.getpid():
        blockers.append(dict(lock=str(LOCK), pid=holder))
    return dict(
        checked_databases=[str(p) for p in databases],
        active_jobs=jobs,
        competing_processes=others,
        benchmark_lock_pid=holder,
        clear=not blockers,
    )


# ---------------------------------------------------------------- comparison


def match_frame(reference, candidate):
    """One-to-one same-class match by nearest box; returns pairs and leftovers."""
    used, pairs, missing = set(), [], []
    for item in reference:
        best = None
        for j, other in enumerate(candidate):
            if j in used or other["class_name"] != item["class_name"]:
                continue
            distance = max(abs(a - b) for a, b in zip(item["bbox"], other["bbox"]))
            if best is None or distance < best[0]:
                best = (distance, j)
        if best is not None and best[0] <= 0.01:
            used.add(best[1])
            pairs.append((item, candidate[best[1]]))
        else:
            missing.append(item)
    return pairs, missing, [c for j, c in enumerate(candidate) if j not in used]


def compare_outputs(reference, candidate, bbox_tol, conf_tol):
    """Per-source-frame comparison of kept and raw observations."""
    max_bbox = max_conf = 0.0
    missing = extra = raw_count_changes = out_of_tolerance = 0
    frames_differing = []
    for ref, cand in zip(reference, candidate):
        if (ref["frame_index"], ref["pts_ms"], ref["frame_digest"]) != (
            cand["frame_index"],
            cand["pts_ms"],
            cand["frame_digest"],
        ):
            raise RuntimeError("Strategies saw different frames")
        pairs, gone, added = match_frame(ref["kept"], cand["kept"])
        frame_bad = bool(gone or added)
        for a, b in pairs:
            db = max(abs(x - y) for x, y in zip(a["bbox"], b["bbox"]))
            dc = abs(a["confidence"] - b["confidence"])
            max_bbox, max_conf = max(max_bbox, db), max(max_conf, dc)
            if db > bbox_tol or dc > conf_tol:
                out_of_tolerance += 1
                frame_bad = True
        missing += len(gone)
        extra += len(added)
        raw_count_changes += len(ref["raw"]) != len(cand["raw"])
        if frame_bad:
            frames_differing.append(ref["pts_ms"])
    return dict(
        kept_missing=missing,
        kept_extra=extra,
        kept_matched_out_of_tolerance=out_of_tolerance,
        max_abs_bbox_difference=max_bbox,
        max_abs_confidence_difference=max_conf,
        frames_with_raw_count_change=raw_count_changes,
        frames_differing=frames_differing,
    )


def trajectory_summary(frames, duration_ms):
    linker = BallTrajectories()
    linked, with_ball = [], []
    for frame in frames:
        balls = [d for d in frame["kept"] if d["class_name"] == "basketball"]
        linked.extend(linker.update(balls, frame["pts_ms"]))
        with_ball.append(bool(balls))
    runs, run = [], 0
    for present in with_ball:
        if present and run:
            runs.append(run)
        run = 0 if present else run + 1
    if run:
        runs.append(run)
    hoops = [d for f in frames for d in f["kept"] if d["class_name"] == "hoop"]
    stable = stationary_hoops(hoops)
    proposals = propose_shots(linked, stable, duration_ms, [])
    segments = Counter(b["ball_track_id"] for b in linked)
    return dict(
        processed_frames=len(frames),
        ball_observations=len(linked),
        frames_with_ball=sum(with_ball),
        detection_coverage=sum(with_ball) / len(frames) if frames else 0,
        segments=len(segments),
        segment_lengths=sorted(segments.values()),
        no_ball_run_lengths=sorted(runs),
        longest_no_ball_run_frames=max(runs, default=0),
        frames_with_hoop=sum(
            any(d["class_name"] == "hoop" for d in f["kept"]) for f in frames
        ),
        stable_hoops=[
            dict(bbox=h["bbox"], observed_frames=h["observed_frames"]) for h in stable
        ],
        # Review-required trajectory proposals only; never makes/misses/shots.
        review_proposals=[
            dict(
                start_ms=p["start_ms"],
                end_ms=p["end_ms"],
                approach_ms=p["approach_ms"],
                evidence_timestamps=p["evidence_timestamps"],
            )
            for p in proposals
        ],
    )


def gate(reference, candidate, comparison, bbox_tol):
    def same_hoops(a, b):
        return len(a) == len(b) and all(
            x["observed_frames"] == y["observed_frames"]
            and max(abs(p - q) for p, q in zip(x["bbox"], y["bbox"])) <= bbox_tol
            for x, y in zip(a, b)
        )

    checks = dict(
        no_kept_observation_missing_or_added=comparison["kept_missing"] == 0
        and comparison["kept_extra"] == 0,
        all_matched_within_tolerance=comparison["kept_matched_out_of_tolerance"] == 0,
        same_frames_with_ball=candidate["frames_with_ball"]
        == reference["frames_with_ball"],
        same_segments=candidate["segment_lengths"] == reference["segment_lengths"],
        same_gaps=candidate["no_ball_run_lengths"] == reference["no_ball_run_lengths"],
        same_hoop_frames=candidate["frames_with_hoop"] == reference["frames_with_hoop"],
        same_stable_hoops=same_hoops(
            reference["stable_hoops"], candidate["stable_hoops"]
        ),
        same_review_proposals=candidate["review_proposals"]
        == reference["review_proposals"],
    )
    return dict(passed=all(checks.values()), checks=checks)


# ---------------------------------------------------------------- run


def decode(source, stride, fps):
    """Production frame selection and timestamp rule (workers/video/pipeline.py)."""
    import cv2

    cap = cv2.VideoCapture(str(source))
    index, last = 0, -1
    try:
        while cap.grab():
            if index % stride:
                index += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            reported = round(cap.get(cv2.CAP_PROP_POS_MSEC))
            fallback = reported <= last
            timestamp = round(index / fps * 1000) if fallback else reported
            if timestamp <= last:
                raise ValueError("Non-monotonic source timestamps")
            last = timestamp
            yield index, timestamp, fallback, frame
            index += 1
    finally:
        cap.release()


def memory_state(device):
    import torch

    state = dict(
        process_max_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1024 * 1024 if sys.platform == "darwin" else 1024)
    )
    if device == "mps":
        state.update(
            mps_current_allocated_mb=torch.mps.current_allocated_memory() / 2**20,
            mps_driver_allocated_mb=torch.mps.driver_allocated_memory() / 2**20,
        )
    elif device.startswith("cuda"):
        state.update(
            cuda_max_allocated_mb=torch.cuda.max_memory_allocated() / 2**20,
        )
    return state


def swap_usage():
    """macOS swap in use (MB); None elsewhere. Swapping invalidates timings."""
    try:
        text = subprocess.run(
            ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5
        ).stdout
        used = text.split("used =")[1].split("M")[0]
        return float(used)
    except Exception:  # noqa: BLE001
        return None


def pageouts():
    """Cumulative macOS pageouts; a rise during a run means it was swapping."""
    try:
        text = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, timeout=5
        ).stdout
        line = next(x for x in text.splitlines() if x.startswith("Pageouts"))
        return int(line.split(":")[1].strip().rstrip("."))
    except Exception:  # noqa: BLE001
        return None


def is_oom(exc):
    text = str(exc).lower()
    return "out of memory" in text or "mps backend out of memory" in text


class Aborted(Exception):
    pass


def run_benchmark(args, detector, metadata, out):
    sizes = args.frame_batches
    specs = ["fb%d" % k for k in sizes] + (
        ["fb1@repeat"] if args.repeat_reference and not args.single else []
    )
    batch_of = {s: int(s.split("@")[0][2:]) for s in specs}
    detectors = {s: BatchedBallDetector(detector, batch_of[s]) for s in specs}
    group_size = math.lcm(*sizes)
    records = {s: [] for s in specs}
    calls = {s: [] for s in specs}
    pipeline_seconds = Counter()
    failed = {}
    decode_seconds = 0.0
    paired = {s: [] for s in specs}
    parity = []
    aborted = None
    logs = {s: (out / (s.replace("@", "_") + ".jsonl")).open("w") for s in specs}
    call_logs = {
        s: (out / (s.replace("@", "_") + "_calls.jsonl")).open("w") for s in specs
    }
    try:
        first = []
        for item in decode(args.source, args.stride, metadata["fps"]):
            first.append(item)
            if len(first) == max(sizes):
                break
        # Untimed warm-up of every batch shape on real frames.
        for _ in range(args.warmup):
            for k in sorted(set(sizes)):
                BatchedBallDetector(detector, k).detect_batch(
                    [(f[3], f[1]) for f in first[:k]]
                )
        frames = decode(args.source, args.stride, metadata["fps"])
        group_number = 0
        while True:
            started = time.perf_counter()
            group = []
            for item in frames:
                group.append(item)
                if len(group) == group_size:
                    break
            decode_seconds += time.perf_counter() - started
            if not group:
                break
            if not args.allow_active_jobs:
                state = preflight(
                    args.check_db, processes=group_number % PROCESS_CHECK_EVERY == 0
                )
                if not state["clear"]:
                    aborted = dict(at_pts_ms=group[0][1], guard=state)
                    raise Aborted()
            digests = [
                hashlib.blake2b(f[3].tobytes(), digest_size=16).hexdigest()
                for f in group
            ]
            order = (
                specs[group_number % len(specs) :] + specs[: group_number % len(specs)]
            )
            for spec in order:
                if spec in failed:
                    continue
                k = batch_of[spec]
                spec_started = time.perf_counter()
                try:
                    for start in range(0, len(group), k):
                        chunk = group[start : start + k]
                        kept, raw = detectors[spec].detect_batch(
                            [(f[3], f[1]) for f in chunk]
                        )
                        call = dict(
                            detectors[spec].last, **memory_state(detector.device)
                        )
                        call["group"] = group_number
                        calls[spec].append(call)
                        call_logs[spec].write(json.dumps(call) + "\n")
                        for offset, (f, frame_kept, frame_raw) in enumerate(
                            zip(chunk, kept, raw)
                        ):
                            record = dict(
                                frame_index=f[0],
                                pts_ms=f[1],
                                index_clock_fallback=f[2],
                                frame_digest=digests[start + offset],
                                batch_position=offset,
                                batch_frames=len(chunk),
                                kept=frame_kept,
                                raw=frame_raw,
                            )
                            records[spec].append(record)
                            logs[spec].write(json.dumps(record) + "\n")
                except RuntimeError as exc:
                    if not is_oom(exc):
                        raise
                    failed[spec] = dict(
                        error=str(exc)[:500],
                        at_pts_ms=group[0][1],
                        **memory_state(detector.device),
                    )
                    import torch

                    if detector.device == "mps":
                        torch.mps.empty_cache()
                    continue
                elapsed = time.perf_counter() - spec_started
                pipeline_seconds[spec] += elapsed
                paired[spec].append(elapsed)
            if group_number == 0 and specs[0] == "fb1":
                for f in group[: args.parity_frames]:
                    production = detector.detect(f[3], f[1])
                    ours = next(r for r in records[specs[0]] if r["pts_ms"] == f[1])[
                        "kept"
                    ]
                    parity.append(
                        len(production) == len(ours)
                        and all(
                            a["class_name"] == b["class_name"]
                            and a["confidence"] == b["confidence"]
                            and a["bbox"] == b["bbox"]
                            for a, b in zip(production, ours)
                        )
                    )
            if group_number % 10 == 0:
                print("group", group_number, "pts_ms", group[0][1], flush=True)
            group_number += 1
    except Aborted:
        print("ABORTED: analysis activity detected", flush=True)
    finally:
        for log in list(logs.values()) + list(call_logs.values()):
            log.close()
    return dict(
        specs=specs,
        records=records,
        calls=calls,
        pipeline_seconds=pipeline_seconds,
        paired=paired,
        decode_seconds=decode_seconds,
        failed=failed,
        parity=parity,
        aborted=aborted,
        group_size=group_size,
    )


def memory_probe(args, detector, metadata):
    """Larger batches on real frames: time per frame, memory, OOM. Untimed for the gate."""
    import torch

    frames = []
    for item in decode(args.source, args.stride, metadata["fps"]):
        frames.append((item[3], item[1]))
        if len(frames) == max(args.probe_batches, default=0):
            break
    results = []
    for k in args.probe_batches:
        if not args.allow_active_jobs and not preflight(args.check_db)["clear"]:
            results.append(dict(frames_per_batch=k, skipped="analysis became active"))
            break
        if detector.device == "mps":
            torch.mps.empty_cache()
        runner = BatchedBallDetector(detector, k)
        entry = dict(frames_per_batch=k, images_per_call=3 * k)
        try:
            runner.detect_batch(frames[:k])  # warm this shape
            seconds = []
            for _ in range(3):
                runner.detect_batch(frames[:k])
                seconds.append(runner.last["predict_seconds"])
            entry.update(
                ok=True,
                median_predict_seconds=statistics.median(seconds),
                predict_seconds_per_frame=statistics.median(seconds) / k,
                **memory_state(detector.device),
            )
        except RuntimeError as exc:
            if not is_oom(exc):
                raise
            entry.update(ok=False, oom=str(exc)[:500], **memory_state(detector.device))
        results.append(entry)
    return results


def git_state():
    def git(*a):
        return subprocess.run(
            ["git", *a], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()

    return dict(
        commit=git("rev-parse", "HEAD"), dirty=git("status", "--porcelain").splitlines()
    )


def summarize_run(result, metadata, args):
    specs = result["specs"]
    reference_spec = specs[0]
    reference = result["records"][reference_spec]
    ref_traj = trajectory_summary(reference, metadata["duration_ms"])
    ref_predict = sum(c["predict_seconds"] for c in result["calls"][reference_spec])
    ref_pipeline = result["pipeline_seconds"][reference_spec]
    decode_s = result["decode_seconds"]
    runs = []
    for spec in specs:
        calls = result["calls"][spec]
        frames = result["records"][spec]
        if spec in result["failed"] or len(frames) != len(reference):
            runs.append(
                dict(strategy=spec, failed=result["failed"].get(spec, "incomplete"))
            )
            continue
        predict = sum(c["predict_seconds"] for c in calls)
        prepare = sum(c["prepare_seconds"] for c in calls)
        post = sum(c["postprocess_seconds"] for c in calls)
        pipeline = result["pipeline_seconds"][spec]
        traj = trajectory_summary(frames, metadata["duration_ms"])
        comparison = compare_outputs(
            reference, frames, args.bbox_tolerance, args.confidence_tolerance
        )
        ratios = [
            a / b
            for a, b in zip(result["paired"][spec], result["paired"][reference_spec])
            if b
        ]
        k = int(spec.split("@")[0][2:])
        runs.append(
            dict(
                strategy=spec,
                frames_per_batch=k,
                images_per_call=3 * k,
                predict_calls=len(calls),
                synchronized_predict_seconds=predict,
                host_prepare_seconds=prepare,
                host_postprocess_seconds=post,
                detector_pipeline_seconds=pipeline,
                end_to_end_seconds_estimate=decode_s + pipeline,
                predict_speedup_vs_fb1=ref_predict / predict if predict else None,
                pipeline_speedup_vs_fb1=ref_pipeline / pipeline if pipeline else None,
                end_to_end_speedup_vs_fb1=(decode_s + ref_pipeline)
                / (decode_s + pipeline),
                median_paired_group_ratio=statistics.median(ratios) if ratios else None,
                predict_seconds_per_frame=predict / len(frames),
                max_memory=dict(
                    process_max_rss_mb=max(c["process_max_rss_mb"] for c in calls),
                    **(
                        dict(
                            mps_current_allocated_mb=max(
                                c["mps_current_allocated_mb"] for c in calls
                            ),
                            mps_driver_allocated_mb=max(
                                c["mps_driver_allocated_mb"] for c in calls
                            ),
                        )
                        if "mps_current_allocated_mb" in calls[0]
                        else {}
                    ),
                ),
                added_source_latency_ms=round(
                    (k - 1) * args.stride / metadata["fps"] * 1000
                ),
                trajectory=traj,
                comparison=comparison,
                gate=gate(ref_traj, traj, comparison, args.bbox_tolerance),
            )
        )
    return runs


def command_run(args):
    guard = preflight(args.check_db)
    plan = dict(
        source=str(args.source),
        device=args.device,
        stride=args.stride,
        frame_batches=args.frame_batches,
        repeat_reference=args.repeat_reference,
        probe_batches=args.probe_batches,
        tolerances=dict(bbox=args.bbox_tolerance, confidence=args.confidence_tolerance),
        guard=guard,
    )
    if not args.execute:
        print(
            json.dumps(
                dict(plan, dry_run=True, note="Add --execute to run model inference."),
                indent=2,
            )
        )
        return
    if not guard["clear"] and not args.allow_active_jobs:
        print(json.dumps(guard, indent=2))
        raise SystemExit("Refusing: active analysis job, GPU worker or benchmark lock.")
    if args.device == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise SystemExit(
            "Unset PYTORCH_ENABLE_MPS_FALLBACK to match production native MPS."
        )
    from importlib.metadata import version
    import cv2
    import torch
    from workers.render.media import probe, sha256
    from workers.video.basketball_detector import BasketballDetector

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if lock_holder():
            raise SystemExit("Another ball benchmark holds %s" % LOCK)
        LOCK.unlink()
        descriptor = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    try:
        metadata = probe(args.source)
        source_hash = sha256(args.source)
        if args.expect_sha256 and source_hash != args.expect_sha256:
            raise SystemExit(
                "Source hash %s != expected %s" % (source_hash, args.expect_sha256)
            )
        out = args.output / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + source_hash[:12]
        )
        out.mkdir(parents=True)
        started = time.perf_counter()
        detector = BasketballDetector(
            ROOT / "data/models",
            args.device,
            args.ball_confidence,
            args.hoop_confidence,
            True,
        )
        load_seconds = time.perf_counter() - started
        h, w = metadata["height"], metadata["width"]
        manifest = dict(
            created=datetime.now(timezone.utc).isoformat(),
            experimental=True,
            source=str(args.source.resolve()),
            source_sha256=source_hash,
            metadata=metadata,
            stride=args.stride,
            device=detector.device,
            model=detector.manifest,
            resolution=704,
            thresholds=detector.thresholds,
            suppression="per source frame, same-class IoU 0.5 (shot_candidates.suppress)",
            views=production_views(w, h),
            frame_batches=args.frame_batches,
            tolerances=dict(
                bbox_normalized=args.bbox_tolerance,
                confidence=args.confidence_tolerance,
            ),
            timing="paired: frames decoded once into groups; each group runs every strategy "
            "back-to-back, order rotated per group. predict = synchronized model.predict() only; "
            "pipeline = colour conversion + predict + mapping + suppression; decode is shared and "
            "measured once.",
            warmup_rounds=args.warmup,
            model_load_seconds=load_seconds,
            runtime=dict(
                python=platform.python_version(),
                torch=torch.__version__,
                rfdetr=version("rfdetr"),
                opencv=cv2.__version__,
                platform=platform.platform(),
                mps_fallback=os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
                mps_high_watermark_ratio=os.environ.get(
                    "PYTORCH_MPS_HIGH_WATERMARK_RATIO"
                ),
            ),
            git=git_state(),
            guard=guard,
            guard_overridden=bool(args.allow_active_jobs),
            mode="isolated single batch size" if args.single else "paired",
            swap_used_mb_before=swap_usage(),
            pageouts_before=pageouts(),
        )
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        result = run_benchmark(args, detector, metadata, out)
        summary = dict(
            manifest=manifest,
            aborted=result["aborted"],
            production_parity_frames=result["parity"],
            group_size=result["group_size"],
            shared_decode_seconds=result["decode_seconds"],
            failed=result["failed"],
            swap_used_mb_after=swap_usage(),
            pageouts_after=pageouts(),
        )
        if not result["aborted"]:
            summary["runs"] = summarize_run(result, metadata, args)
            summary["memory_probe"] = (
                memory_probe(args, detector, metadata) if args.probe_batches else []
            )
        (out / "summary.json").write_text(
            json.dumps(summary, indent=2, default=float) + "\n"
        )
        print_table(summary)
        print("wrote", out / "summary.json")
        if result["aborted"]:
            raise SystemExit(2)
    finally:
        LOCK.unlink(missing_ok=True)


def load_single(folder):
    summary = json.loads((folder / "summary.json").read_text())
    if summary["manifest"].get("mode") != "isolated single batch size":
        raise SystemExit("%s is not an isolated --single run" % folder)
    run = summary["runs"][0]
    records = [
        json.loads(line) for line in (folder / (run["strategy"] + ".jsonl")).open()
    ]
    return summary, run, records


def command_compare(args):
    """Isolated sequential processes: outputs compared exactly; timing is NOT paired."""
    ref_summary, ref_run, reference = load_single(args.reference)
    ref_manifest = ref_summary["manifest"]
    if ref_run["frames_per_batch"] != 1:
        raise SystemExit("Reference must be an fb1 run")
    ref_traj = ref_run["trajectory"]
    rows = []
    for folder in [args.reference] + args.candidates:
        summary, run, records = load_single(folder)
        manifest = summary["manifest"]
        for key in ("source_sha256", "stride", "device", "thresholds", "views"):
            if manifest[key] != ref_manifest[key]:
                raise SystemExit("%s differs in %s" % (folder, key))
        if (
            manifest["model"]["sanitized_sha256"]
            != ref_manifest["model"]["sanitized_sha256"]
        ):
            raise SystemExit("%s used a different checkpoint" % folder)
        comparison = compare_outputs(
            reference, records, args.bbox_tolerance, args.confidence_tolerance
        )
        rows.append(
            dict(
                folder=str(folder),
                strategy=run["strategy"],
                images_per_call=run["images_per_call"],
                synchronized_predict_seconds=run["synchronized_predict_seconds"],
                detector_pipeline_seconds=run["detector_pipeline_seconds"],
                decode_seconds=summary["shared_decode_seconds"],
                predict_speedup_vs_fb1=ref_run["synchronized_predict_seconds"]
                / run["synchronized_predict_seconds"],
                pipeline_speedup_vs_fb1=ref_run["detector_pipeline_seconds"]
                / run["detector_pipeline_seconds"],
                end_to_end_speedup_vs_fb1=(
                    ref_summary["shared_decode_seconds"]
                    + ref_run["detector_pipeline_seconds"]
                )
                / (summary["shared_decode_seconds"] + run["detector_pipeline_seconds"]),
                max_memory=run["max_memory"],
                swap_used_mb=[
                    manifest.get("swap_used_mb_before"),
                    summary.get("swap_used_mb_after"),
                ],
                pageouts_during_run=(summary.get("pageouts_after") or 0)
                - (manifest.get("pageouts_before") or 0),
                added_source_latency_ms=run["added_source_latency_ms"],
                trajectory=run["trajectory"],
                comparison=comparison,
                gate=gate(ref_traj, run["trajectory"], comparison, args.bbox_tolerance),
                timing_note="sequential isolated processes; not paired",
            )
        )
    text = json.dumps(
        dict(reference=str(args.reference), runs=rows), indent=2, default=float
    )
    if args.output:
        args.output.write_text(text + "\n")
    for r in rows:
        t, c = r["trajectory"], r["comparison"]
        print(
            "%-6s predict %7.2f (x%.3f) pipeline %7.2f (x%.3f) e2e x%.3f ball %d segs %d gap %d "
            "hoop %d dbox %.1e dconf %.1e mps_driver_MB %.0f pageouts %d %s"
            % (
                r["strategy"],
                r["synchronized_predict_seconds"],
                r["predict_speedup_vs_fb1"],
                r["detector_pipeline_seconds"],
                r["pipeline_speedup_vs_fb1"],
                r["end_to_end_speedup_vs_fb1"],
                t["frames_with_ball"],
                t["segments"],
                t["longest_no_ball_run_frames"],
                t["frames_with_hoop"],
                c["max_abs_bbox_difference"],
                c["max_abs_confidence_difference"],
                r["max_memory"].get("mps_driver_allocated_mb", 0),
                r["pageouts_during_run"],
                "PASS"
                if r["gate"]["passed"]
                else "FAIL "
                + ",".join(k for k, ok in r["gate"]["checks"].items() if not ok),
            )
        )


def print_table(summary):
    print(
        "parity",
        summary["production_parity_frames"],
        "decode_s %.2f" % summary["shared_decode_seconds"],
    )
    print(
        "%-11s %6s %8s %7s %8s %7s %6s %5s %5s %4s %5s %9s %9s %8s %s"
        % (
            "strategy",
            "images",
            "predict",
            "x",
            "pipeln",
            "x",
            "e2e_x",
            "ball",
            "segs",
            "gap",
            "hoop",
            "max_dbox",
            "max_dconf",
            "mps_MB",
            "gate",
        )
    )
    for r in summary.get("runs", []):
        if "failed" in r:
            print("%-11s FAILED %s" % (r["strategy"], r["failed"]))
            continue
        t, c, g = r["trajectory"], r["comparison"], r["gate"]
        print(
            "%-11s %6d %8.2f %7.3f %8.2f %7.3f %6.3f %5d %5d %4d %5d %9.2e %9.2e %8.0f %s"
            % (
                r["strategy"],
                r["images_per_call"],
                r["synchronized_predict_seconds"],
                r["predict_speedup_vs_fb1"],
                r["detector_pipeline_seconds"],
                r["pipeline_speedup_vs_fb1"],
                r["end_to_end_speedup_vs_fb1"],
                t["frames_with_ball"],
                t["segments"],
                t["longest_no_ball_run_frames"],
                t["frames_with_hoop"],
                c["max_abs_bbox_difference"],
                c["max_abs_confidence_difference"],
                r["max_memory"].get("mps_driver_allocated_mb", 0),
                "PASS"
                if g["passed"]
                else "FAIL " + ",".join(k for k, ok in g["checks"].items() if not ok),
            )
        )
    for p in summary.get("memory_probe", []):
        print("probe", json.dumps(p))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser(
        "preflight", help="Check for active jobs/workers; exit 1 if blocked"
    )
    pre.add_argument("--check-db", action="append", default=[])
    run = sub.add_parser(
        "run", help="Model inference (requires --execute and a clear preflight)"
    )
    run.add_argument("source", type=Path)
    run.add_argument("--device", required=True, help="Explicit cpu|mps|cuda:N; no auto")
    run.add_argument(
        "--expect-sha256", help="Refuse unless the source has this SHA-256"
    )
    run.add_argument("--stride", type=int, default=2)
    run.add_argument("--frame-batches", type=int, nargs="+", default=[1, 2, 4])
    run.add_argument(
        "--no-repeat-reference",
        dest="repeat_reference",
        action="store_false",
        help="Skip the second fb1 pass that measures run-to-run noise",
    )
    run.add_argument(
        "--probe-batches",
        type=int,
        nargs="*",
        default=[],
        help="Opt-in: after the run, probe larger frame batches for time/memory/OOM. "
        "Can exhaust unified memory and push the machine into swap.",
    )
    run.add_argument("--warmup", type=int, default=3)
    run.add_argument("--parity-frames", type=int, default=3)
    run.add_argument("--bbox-tolerance", type=float, default=BBOX_TOLERANCE)
    run.add_argument("--confidence-tolerance", type=float, default=CONFIDENCE_TOLERANCE)
    run.add_argument("--ball-confidence", type=float, default=0.25)
    run.add_argument("--hoop-confidence", type=float, default=0.35)
    run.add_argument("--output", type=Path, default=OUTPUT)
    run.add_argument("--check-db", action="append", default=[])
    run.add_argument(
        "--single",
        action="store_true",
        help="Run exactly one --frame-batches value in this process (fresh MPS pool); "
        "combine isolated runs with the compare subcommand",
    )
    run.add_argument("--execute", action="store_true")
    run.add_argument("--allow-active-jobs", action="store_true")
    cmp = sub.add_parser(
        "compare", help="CPU-only: compare isolated --single runs against an fb1 run"
    )
    cmp.add_argument("reference", type=Path, help="Run folder of the isolated fb1 run")
    cmp.add_argument(
        "candidates", type=Path, nargs="+", help="Isolated fbK run folders"
    )
    cmp.add_argument("--bbox-tolerance", type=float, default=BBOX_TOLERANCE)
    cmp.add_argument("--confidence-tolerance", type=float, default=CONFIDENCE_TOLERANCE)
    cmp.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        state = preflight(args.check_db)
        print(json.dumps(state, indent=2))
        raise SystemExit(0 if state["clear"] else 1)
    if args.command == "compare":
        return command_compare(args)
    if args.single and len(args.frame_batches) != 1:
        parser.error("--single takes exactly one --frame-batches value")
    if not args.single and (
        args.frame_batches[0] != 1
        or len(set(args.frame_batches)) != len(args.frame_batches)
    ):
        parser.error(
            "--frame-batches must start with 1 (production reference) and be unique"
        )
    if any(not 1 <= k <= 16 for k in args.frame_batches + args.probe_batches):
        parser.error("frame batches must be in 1..16")
    if not 1 <= args.stride <= 5:
        parser.error("Use stride 1-5")
    if args.warmup < 0 or args.parity_frames < 0:
        parser.error("warmup and parity-frames must be >= 0")
    if not args.source.is_file():
        parser.error("Source not found")
    command_run(args)


if __name__ == "__main__":
    main()
