#!/usr/bin/env python3
"""Compare RF-DETR ball/hoop view strategies on one authorized source.

Subcommands:
  preflight  Check that no analysis job or GPU worker is active. No model is loaded.
  diagnose   CPU-only trajectory diagnostics on existing basketball artifact folders.
  run        Model inference. Prints a plan unless --execute is given, refuses while
             any local analysis job/worker is active, re-checks the job queue before
             every frame and aborts if an analysis starts.

All strategies run in ONE decode pass: each sampled frame goes through every
strategy back-to-back (order rotated per frame), so timings are paired and warm.
"Detection coverage" is the share of processed frames with at least one ball
observation over threshold. It is not accuracy: nothing here is compared against
annotated ground truth and no makes/misses are inferred. The production views
(``tiled3``) are the reference, not truth.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workers.video.adaptive_ball_detector import (  # noqa: E402
    BALL_MAX_AGE_MS,
    TILE_FRACTION,
    AdaptiveCropPolicy,
    TileSelectionPolicy,
    fixed_tiles,
    full_view,
    predict_views,
    production_views,
)
from workers.video.shot_candidates import (  # noqa: E402
    BallTrajectories,
    center,
    propose_shots,
    stationary_hoops,
    suppress,
    xyxy,
)
from workers.video.tracker import iou  # noqa: E402

ACTIVE = ("queued", "running", "cancelling")
JOB_KINDS = ("job", "basketball_job", "highlights")
WORKER_PATTERNS = (
    "scripts/run_analysis.py",
    "analyze_basketball.py",
    "benchmark_devices.py",
    "validate_pose_migration.py",
    "cache_tracking_detections.py",
    "benchmark_ball_views.py run",
)
OUTPUT = ROOT / "data/benchmarks/ball_views"
LOCK = OUTPUT / ".gpu.lock"
MATCH_DISTANCE = (
    0.015  # normalized centre distance counted as the same ball across strategies
)
DUPLICATE_DISTANCE = 0.03  # same-frame ball pairs closer than this survived suppression
PROCESS_CHECK_EVERY = 15


# ---------------------------------------------------------------- guard


def candidate_databases(extra=()):
    paths = {p.resolve() for p in (ROOT / "data").glob("*/scoutfilm.sqlite3")}
    if os.environ.get("SCOUTFILM_DATA"):
        paths.add((Path(os.environ["SCOUTFILM_DATA"]) / "scoutfilm.sqlite3").resolve())
    paths.update(Path(p).resolve() for p in extra)
    return sorted(p for p in paths if p.exists())


def active_jobs(database):
    """Read-only scan; an unreadable database counts as a blocker (fail closed)."""
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
        return None  # stale lock
    except PermissionError:
        return -1


def preflight(extra_databases=(), processes=True):
    databases = candidate_databases(extra_databases)
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


# ---------------------------------------------------------------- strategies


class FixedViews:
    def __init__(self, name, full=True, tiled=True, every=1):
        self.name, self.full, self.tiled, self.every = name, full, tiled, every
        self.params = dict(full=full, tiled=tiled, tile_every=every)
        self.frame_number = 0

    def plan(self, timestamp_ms, w, h):
        views = full_view(w, h) if self.full else []
        if self.tiled and w / h > 1.2 and self.frame_number % self.every == 0:
            views = views + fixed_tiles(w, h)
        return views or full_view(w, h), "fixed"

    def update(self, kept, timestamp_ms, raw=None):
        self.frame_number += 1


def parse_strategy(spec):
    """``name[:k=v,...][@tag]``; the tag only distinguishes repeated specs."""
    base = spec.split("@", 1)[0]
    name, _, raw = base.partition(":")
    params = dict(p.split("=", 1) for p in raw.split(",") if p)
    if name == "tiled3":
        strategy = FixedViews(spec, True, True)
    elif name == "full":
        strategy = FixedViews(spec, True, False)
    elif name == "tiles2":
        strategy = FixedViews(spec, False, True)
    elif name == "sparse":
        strategy = FixedViews(spec, True, True, int(params.get("every", 3)))
    elif name in ("adaptive", "select"):
        common = dict(
            bootstrap_frames=int(params.get("bootstrap", 3)),
            refresh_every=int(params.get("refresh", 10)),
            reacquire_every=int(params.get("reacquire", 1)),
            keep_full=params.get("full", "1") not in ("0", "false"),
        )
        if name == "adaptive":
            strategy = AdaptiveCropPolicy(
                crop_fraction=float(params.get("crop", TILE_FRACTION)), **common
            )
        else:
            strategy = TileSelectionPolicy(
                margin=float(params.get("margin", 0.05)),
                learn_hoop_tiles=params.get("learnhoop", "0") in ("1", "true"),
                **common,
            )
        strategy.name = spec
    else:
        raise ValueError("Unknown strategy %r" % spec)
    return strategy


DEFAULT_STRATEGIES = [
    "tiled3",
    "tiled3@repeat",  # same views again: measures run-to-run noise in the paired run
    "full",
    "tiles2",
    "select",
    "select:learnhoop=1",
]


# ---------------------------------------------------------------- metrics


def ball_metrics(frames):
    """Pure diagnostics. ``frames`` is ordered [{pts_ms, kept:[observation]}]."""
    linker = BallTrajectories()
    linked, with_ball = [], []
    multi = duplicates = 0
    for frame in frames:
        balls = [d for d in frame["kept"] if d["class_name"] == "basketball"]
        linked.extend(linker.update(balls, frame["pts_ms"]))
        with_ball.append(bool(balls))
        multi += len(balls) > 1
        for i, a in enumerate(balls):
            for b in balls[i + 1 :]:
                if (
                    float(((center(a) - center(b)) ** 2).sum() ** 0.5)
                    < DUPLICATE_DISTANCE
                ):
                    duplicates += 1
    segments = Counter(b["ball_track_id"] for b in linked)
    spans = {}
    for b in linked:
        lo, hi = spans.get(b["ball_track_id"], (b["timestamp_ms"],) * 2)
        spans[b["ball_track_id"]] = (
            min(lo, b["timestamp_ms"]),
            max(hi, b["timestamp_ms"]),
        )
    gaps, run = [], 0
    for present in with_ball:
        if present and run:
            gaps.append(run)
        run = 0 if present else run + 1
    if run:
        gaps.append(run)
    longest_ms, last = 0, None
    for frame, present in zip(frames, with_ball):
        if present:
            if last is not None:
                longest_ms = max(longest_ms, frame["pts_ms"] - last)
            last = frame["pts_ms"]
    lengths = sorted(segments.values())
    processed = len(frames)
    return dict(
        processed_frames=processed,
        ball_observations=len(linked),
        frames_with_ball=sum(with_ball),
        detection_coverage=sum(with_ball) / processed if processed else 0,
        frames_with_multiple_balls=multi,
        near_duplicate_ball_pairs=duplicates,
        segments=len(segments),
        single_observation_segments=sum(1 for n in lengths if n == 1),
        segments_with_4plus_observations=sum(1 for n in lengths if n >= 4),
        segment_lengths=lengths,
        median_segment_duration_ms=statistics.median(
            hi - lo for lo, hi in spans.values()
        )
        if spans
        else 0,
        no_ball_runs=len(gaps),
        no_ball_run_lengths=sorted(gaps),
        longest_no_ball_run_frames=max(gaps, default=0),
        longest_gap_between_ball_frames_ms=longest_ms,
        linked=linked,
    )


def hoop_metrics(frames):
    hoops = [d for f in frames for d in f["kept"] if d["class_name"] == "hoop"]
    stable = stationary_hoops(hoops)
    return dict(
        frames_with_hoop=sum(
            any(d["class_name"] == "hoop" for d in f["kept"]) for f in frames
        ),
        hoop_observations=len(hoops),
        median_hoop_confidence=statistics.median(d["confidence"] for d in hoops)
        if hoops
        else None,
        stable_hoops=stable,
        stable_hoop_count=len(stable),
    )


def summarize(frames, duration_ms):
    balls = ball_metrics(frames)
    hoops = hoop_metrics(frames)
    linked = balls.pop("linked")
    # Review-required trajectory proposals only; never makes, misses or shots.
    proposals = propose_shots(linked, hoops["stable_hoops"], duration_ms, [])
    return dict(ball=balls, hoop=hoops, review_proposals=len(proposals))


def match_balls(a_items, b_items):
    a = [d for d in a_items if d["class_name"] == "basketball"]
    b = [d for d in b_items if d["class_name"] == "basketball"]
    used, pairs, only_a = set(), [], []
    for item in a:
        best = None
        for j, cand in enumerate(b):
            if j in used:
                continue
            distance = float(((center(item) - center(cand)) ** 2).sum() ** 0.5)
            if distance <= MATCH_DISTANCE and (best is None or distance < best[0]):
                best = (distance, j)
        if best:
            used.add(best[1])
            pairs.append((item, b[best[1]]))
        else:
            only_a.append(item)
    return pairs, only_a, [c for j, c in enumerate(b) if j not in used]


def agreement(reference, candidate):
    """Frame-by-frame ball agreement with the reference strategy (not ground truth)."""
    by_time = {f["pts_ms"]: f for f in candidate}
    matched = ref_only = cand_only = hoop_lost = 0
    lost_frames, extra_frames = [], []
    for ref in reference:
        other = by_time.get(ref["pts_ms"])
        if other is None:
            continue
        if any(d["class_name"] == "hoop" for d in ref["kept"]) and not any(
            d["class_name"] == "hoop" for d in other["kept"]
        ):
            hoop_lost += 1
        pairs, only_ref, only_cand = match_balls(ref["kept"], other["kept"])
        matched += len(pairs)
        ref_only += len(only_ref)
        cand_only += len(only_cand)
        if only_ref:
            lost_frames.append(ref["pts_ms"])
        if only_cand:
            extra_frames.append(ref["pts_ms"])
    total = matched + ref_only
    return dict(
        reference_ball_observations_matched=matched,
        reference_only=ref_only,
        candidate_only=cand_only,
        reference_retention=matched / total if total else None,
        frames_where_reference_hoop_was_lost=hoop_lost,
        frames_with_lost_reference_ball=lost_frames,
        frames_with_candidate_only_ball=extra_frames,
        note="Agreement with the current strategy, not accuracy; candidate-only "
        "observations are unreviewed and may be true or false.",
    )


def view_attribution(frames):
    """Which views independently found each kept ball/hoop in a run."""
    found = Counter()
    for frame in frames:
        for kept in frame["kept"]:
            if kept["class_name"] not in ("basketball", "hoop"):
                continue
            views = sorted(
                {
                    raw["view"]
                    for raw in frame["raw"]
                    if raw["class_name"] == kept["class_name"]
                    and iou(xyxy(raw["bbox"]), xyxy(kept["bbox"])) >= 0.5
                }
            )
            found[kept["class_name"] + ":" + "+".join(views)] += 1
    return dict(found)


def derive(frames, views):
    """Offline subset of a recorded run: re-suppress only the listed views' raw output."""
    return [
        dict(
            pts_ms=f["pts_ms"],
            frame_index=f["frame_index"],
            kept=suppress([d for d in f["raw"] if d["view"] in views]),
        )
        for f in frames
    ]


def verdict(reference, candidate, timing):
    """Strict non-regression gate against the reference (not accuracy)."""
    rb, cb = reference["ball"], candidate["ball"]
    rh, ch = reference["hoop"], candidate["hoop"]
    a = candidate["agreement"]
    region_iou = (
        iou(xyxy(rh["stable_hoops"][0]["bbox"]), xyxy(ch["stable_hoops"][0]["bbox"]))
        if rh["stable_hoops"] and ch["stable_hoops"]
        else 0.0
    )
    checks = dict(
        cheaper=timing["detector_seconds"] < timing["reference_detector_seconds"],
        frames_with_ball_preserved=cb["frames_with_ball"] >= rb["frames_with_ball"],
        no_reference_ball_lost=a["reference_only"] == 0,
        segments_not_increased=cb["segments"] <= rb["segments"],
        longest_no_ball_run_not_longer=cb["longest_no_ball_run_frames"]
        <= rb["longest_no_ball_run_frames"],
        no_new_duplicates=cb["near_duplicate_ball_pairs"]
        <= rb["near_duplicate_ball_pairs"],
        stable_hoop_count_equal=ch["stable_hoop_count"] == rh["stable_hoop_count"],
        stable_hoop_region_iou_090=region_iou >= 0.9,
        hoop_frames_preserved=ch["frames_with_hoop"] >= rh["frames_with_hoop"],
    )
    # "stability" gate: every check except per-frame hoop observations, which the
    # stationary-hoop consensus does not need on every frame. Reviewer decides.
    stability = {k: v for k, v in checks.items() if k != "hoop_frames_preserved"}
    return dict(
        passed=all(checks.values()),
        passed_except_per_frame_hoop=all(stability.values()),
        stable_hoop_region_iou=region_iou,
        checks=checks,
    )


# ---------------------------------------------------------------- diagnose (CPU only)


def load_artifact(folder):
    folder = Path(folder)
    report = json.loads((folder / "report.json").read_text())
    objects = json.loads((folder / "objects.json").read_text())
    meta, stride = report["metadata"], report["stride"]
    # Artifacts omit frames without detections; rebuild the processed grid from the
    # index clock and snap observed timestamps onto it.
    grid = [
        round(i / meta["fps"] * 1000) for i in range(0, meta["frame_count"], stride)
    ]
    grid = grid[: report["processed_frames"]]
    frames = {
        t: dict(pts_ms=t, frame_index=i * stride, kept=[]) for i, t in enumerate(grid)
    }
    unsnapped = 0
    for item in objects:
        if item["class_name"] not in ("basketball", "hoop"):
            continue
        t = min(grid, key=lambda g: abs(g - item["timestamp_ms"]))
        if abs(t - item["timestamp_ms"]) > 2:
            unsnapped += 1
            continue
        frames[t]["kept"].append(dict(item, timestamp_ms=t))
    return report, [frames[t] for t in grid], unsnapped


def project_views(frames, strategy, w, h):
    """Optimistic view-count projection: assumes the strategy sees what the reference saw."""
    reasons, views = Counter(), 0
    for frame in frames:
        chosen, reason = strategy.plan(frame["pts_ms"], w, h)
        views += len(chosen)
        reasons[reason] += 1
        strategy.update(frame["kept"], frame["pts_ms"])
    return dict(
        strategy=strategy.name,
        projected_views_per_frame=views / len(frames) if frames else 0,
        reasons=dict(reasons),
        caveat="Projection from reference detections only; needs a real run.",
    )


def command_diagnose(args):
    results = []
    for folder in args.folders:
        report, frames, unsnapped = load_artifact(folder)
        meta = report["metadata"]
        stored = json.loads((Path(folder) / "ball_tracks.json").read_text())
        results.append(
            dict(
                folder=str(folder),
                source_sha256=report["source_sha256"],
                device=report.get("device"),
                stride=report["stride"],
                model_sha256=report.get("model", {}).get("sha256"),
                recorded_detector_seconds=report.get("detector_seconds"),
                recorded_frames_with_ball=report.get("frames_with_ball"),
                recorded_ball_segments=report.get("ball_segments"),
                stored_ball_track_segments=len({b["ball_track_id"] for b in stored}),
                observations_not_on_index_grid=unsnapped,
                recomputed=summarize(frames, meta["duration_ms"]),
                projections=[
                    project_views(
                        frames, parse_strategy(s), meta["width"], meta["height"]
                    )
                    for s in args.strategies
                ],
                limitation="Existing artifacts do not record which view produced an "
                "observation, so view subsets cannot be derived from them.",
            )
        )
    text = json.dumps(results, indent=2, default=float)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


# ---------------------------------------------------------------- replay (CPU only)


def command_replay(args):
    """Exact offline replay of production-view subsets from a recorded tiled3 run.

    Valid only for strategies whose views are a subset of {full, left, right}:
    identical crop pixels give identical model output (checked by the repeat and
    derived:tiles2 rows of the recorded run). Time is ESTIMATED from the run's
    paired median seconds per view count; confirm on the GPU before relying on it.
    """
    folder = args.run
    summary = json.loads((folder / "summary.json").read_text())
    duration = summary["manifest"]["metadata"]["duration_ms"]
    reference = [json.loads(line) for line in (folder / "tiled3.jsonl").open()]
    per_count = {}
    for path in folder.glob("*.jsonl"):
        for line in path.open():
            r = json.loads(line)
            per_count.setdefault(len(r["views"]), []).append(r["detector_seconds"])
    cost = {n: statistics.median(v) for n, v in per_count.items()}
    ref_summary = summarize(reference, duration)
    ref_seconds = sum(cost[3] for _ in reference)
    rows = []
    for spec in args.strategies:
        strategy = parse_strategy(spec)
        frames = []
        for r in reference:
            h, w = (
                summary["manifest"]["metadata"]["height"],
                summary["manifest"]["metadata"]["width"],
            )
            views, reason = strategy.plan(r["pts_ms"], w, h)
            names = {n for n, _ in views}
            known = {v["name"]: tuple(v["rect"]) for v in r["views"]}
            if any(known.get(n) != tuple(rect) for n, rect in views):
                raise SystemExit(
                    "%s uses views not recorded in tiled3; run it on the GPU" % spec
                )
            raw = [d for d in r["raw"] if d["view"] in names]
            kept = suppress(raw)
            strategy.update(kept, r["pts_ms"], raw)
            frames.append(
                dict(
                    pts_ms=r["pts_ms"],
                    frame_index=r["frame_index"],
                    kept=kept,
                    views=views,
                    reason=reason,
                )
            )
        row = summarize(frames, duration)
        estimated = sum(cost[len(f["views"])] for f in frames)
        row.update(
            strategy=spec,
            params=strategy.params,
            replayed_from=str(folder),
            estimated_detector_seconds=estimated,
            reference_estimated_seconds=ref_seconds,
            views_per_frame=sum(len(f["views"]) for f in frames) / len(frames),
            view_reasons=dict(Counter(f["reason"] for f in frames)),
            agreement=agreement(reference, frames),
        )
        row["verdict"] = verdict(
            ref_summary,
            row,
            dict(detector_seconds=estimated, reference_detector_seconds=ref_seconds),
        )
        rows.append(row)
    text = json.dumps(
        dict(median_seconds_by_view_count=cost, runs=rows), indent=2, default=float
    )
    if args.output:
        args.output.write_text(text + "\n")
    for r in rows:
        b, a, v = r["ball"], r["agreement"], r["verdict"]
        print(
            "%-40s est_s %6.2f views %.2f ball %3d segs %2d gapF %2d retain %.3f hoopF %3d hoops %d %s"
            % (
                r["strategy"][:40],
                r["estimated_detector_seconds"],
                r["views_per_frame"],
                b["frames_with_ball"],
                b["segments"],
                b["longest_no_ball_run_frames"],
                a["reference_retention"],
                r["hoop"]["frames_with_hoop"],
                r["hoop"]["stable_hoop_count"],
                "PASS"
                if v["passed"]
                else (
                    "PASS-stability " if v["passed_except_per_frame_hoop"] else "FAIL "
                )
                + ",".join(k for k, ok in v["checks"].items() if not ok)
                + " iou=%.3f" % v["stable_hoop_region_iou"],
            )
        )


# ---------------------------------------------------------------- run (model inference)


def git_state():
    def git(*a):
        return subprocess.run(
            ["git", *a], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()

    return dict(
        commit=git("rev-parse", "HEAD"), dirty=git("status", "--porcelain").splitlines()
    )


def decode(source, stride, limit_ms, fps):
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
            if limit_ms is not None and timestamp >= limit_ms:
                break
            yield index, timestamp, reported, fallback, frame
            index += 1
    finally:
        cap.release()


def slug(name):
    return "".join(c if c.isalnum() else "_" for c in name)


class Aborted(Exception):
    pass


def paired_run(detector, specs, args, metadata, out):
    strategies = [parse_strategy(s) for s in specs]
    records = {s: [] for s in specs}
    logs = {s: (out / (slug(s) + ".jsonl")).open("w") for s in specs}
    parity = []
    aborted = None
    try:
        warm = next(decode(args.source, args.stride, None, metadata["fps"]))[4]
        h, w = warm.shape[:2]
        # Warm every batch shape used below (1, 2 and 3 views); untimed.
        for _ in range(args.warmup):
            for views in (full_view(w, h), fixed_tiles(w, h), production_views(w, h)):
                predict_views(detector, warm, 0, views)
        for number, (index, pts, reported, fallback, frame) in enumerate(
            decode(args.source, args.stride, args.limit_ms, metadata["fps"])
        ):
            if not args.allow_active_jobs:
                state = preflight(
                    args.check_db, processes=number % PROCESS_CHECK_EVERY == 0
                )
                if not state["clear"]:
                    aborted = dict(at_frame_index=index, at_pts_ms=pts, guard=state)
                    raise Aborted()
            digest = hashlib.blake2b(frame.tobytes(), digest_size=16).hexdigest()
            order = specs[number % len(specs) :] + specs[: number % len(specs)]
            for spec in order:
                strategy = strategies[specs.index(spec)]
                views, reason = strategy.plan(pts, w, h)
                raw, seconds = predict_views(detector, frame, pts, views)
                kept = suppress(raw)
                strategy.update(kept, pts, raw)
                record = dict(
                    frame_index=index,
                    pts_ms=pts,
                    decoder_pos_ms=reported,
                    index_clock_fallback=fallback,
                    frame_digest=digest,
                    views=[dict(name=n, rect=list(r)) for n, r in views],
                    reason=reason,
                    order=order.index(spec),
                    detector_seconds=seconds,
                    raw=raw,
                    kept=kept,
                )
                logs[spec].write(json.dumps(record) + "\n")
                records[spec].append(record)
            if number < args.parity_frames:
                # Production detect() must equal the mirrored reference views.
                production = detector.detect(frame, pts)
                kept = records[specs[0]][-1]["kept"]
                parity.append(
                    len(production) == len(kept)
                    and all(
                        a["class_name"] == b["class_name"]
                        and max(abs(x - y) for x, y in zip(a["bbox"], b["bbox"])) < 1e-4
                        for a, b in zip(production, kept)
                    )
                )
            if number % 30 == 0:
                print("frame", index, "pts_ms", pts, flush=True)
    except Aborted:
        print(
            "ABORTED: analysis activity detected",
            json.dumps(
                aborted["guard"]["active_jobs"]
                or aborted["guard"]["competing_processes"]
            ),
            flush=True,
        )
    finally:
        for log in logs.values():
            log.close()
    return strategies, records, parity, aborted


def draw(frame, observations, views, colour):
    import cv2

    image = frame.copy()
    h, w = image.shape[:2]
    for _, (x1, y1, x2, y2) in views:
        cv2.rectangle(image, (x1 + 2, y1 + 2), (x2 - 2, y2 - 2), (160, 160, 160), 1)
    for d in observations:
        if d["class_name"] not in ("basketball", "hoop"):
            continue
        x, y, bw, bh = d["bbox"]
        p1 = (round(x * w), round(y * h))
        p2 = (round((x + bw) * w), round((y + bh) * h))
        c = colour if d["class_name"] == "basketball" else (255, 220, 40)
        cv2.rectangle(image, p1, p2, c, 2)
        cv2.putText(
            image,
            "%s %.2f" % (d["class_name"][:4], d["confidence"]),
            (p1[0], max(14, p1[1] - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            c,
            1,
        )
    return image


def review_sheets(args, metadata, specs, records, summaries, out):
    """Side-by-side reference vs candidate for frames with lost or extra balls."""
    import cv2
    import numpy as np

    wanted = {}
    for spec in specs[1:]:
        a = summaries[spec].get("agreement", {})
        frames = [(t, "lost") for t in a.get("frames_with_lost_reference_ball", [])]
        frames += [(t, "extra") for t in a.get("frames_with_candidate_only_ball", [])]
        for t, why in frames[: args.review_limit]:
            wanted.setdefault(t, []).append((spec, why))
    if not wanted:
        return []
    by_spec = {s: {r["pts_ms"]: r for r in records[s]} for s in specs}
    rows = {}
    for index, pts, _, _, frame in decode(
        args.source, args.stride, args.limit_ms, metadata["fps"]
    ):
        if pts not in wanted:
            continue
        ref = by_spec[specs[0]][pts]
        if (
            hashlib.blake2b(frame.tobytes(), digest_size=16).hexdigest()
            != ref["frame_digest"]
        ):
            raise RuntimeError("Re-decoded frame differs from benchmarked frame")
        h, w = frame.shape[:2]
        for spec, why in wanted[pts]:
            cand = by_spec[spec][pts]
            _, only_ref, only_cand = match_balls(ref["kept"], cand["kept"])
            focus = (only_ref or only_cand)[0]
            cx, cy = center(focus) * [w, h]
            side = 160
            x1 = int(min(max(0, cx - side), w - 2 * side))
            y1 = int(min(max(0, cy - side), h - 2 * side))
            panels = []
            for rec, colour in ((ref, (0, 220, 255)), (cand, (60, 60, 255))):
                drawn = draw(
                    frame,
                    rec["kept"],
                    [(v["name"], tuple(v["rect"])) for v in rec["views"]],
                    colour,
                )
                panels.append(cv2.resize(drawn, (640, 360)))
                panels.append(
                    cv2.resize(
                        drawn[y1 : y1 + 2 * side, x1 : x1 + 2 * side], (360, 360)
                    )
                )
            row = np.hstack([panels[0], panels[1], panels[2], panels[3]])
            label = (
                "%s | %s | frame %d | %d ms | left: tiled3 (reference)  right: %s"
                % (why.upper(), spec, index, pts, spec)
            )
            cv2.rectangle(row, (0, 0), (row.shape[1], 24), (0, 0, 0), -1)
            cv2.putText(
                row, label, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1
            )
            rows.setdefault(spec, []).append(row)
    written = []
    for spec, items in rows.items():
        for i in range(0, len(items), 8):
            path = out / ("review_%s_%d.jpg" % (slug(spec), i // 8))
            cv2.imwrite(
                str(path), np.vstack(items[i : i + 8]), [cv2.IMWRITE_JPEG_QUALITY, 85]
            )
            written.append(str(path.relative_to(out)))
    return written


def command_run(args):
    guard = preflight(args.check_db)
    specs = args.strategies
    plan = dict(
        source=str(args.source),
        device=args.device,
        stride=args.stride,
        strategies=specs,
        ball_confidence=args.ball_confidence,
        hoop_confidence=args.hoop_confidence,
        limit_seconds=args.limit_seconds,
        output_root=str(args.output),
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
        raise SystemExit(
            "Refusing: active analysis job, GPU worker or benchmark lock (see above)."
        )
    if args.device == "mps" and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1":
        raise SystemExit(
            "Unset PYTORCH_ENABLE_MPS_FALLBACK to match production native MPS."
        )
    if specs[0] != "tiled3":
        raise SystemExit(
            "The first strategy must be the production reference 'tiled3'."
        )
    if len(set(specs)) != len(specs):
        raise SystemExit("Strategy specs must be unique; use @tag for repeats.")
    from importlib.metadata import version
    import cv2
    import torch
    from workers.render.media import probe, sha256
    from workers.video.basketball_detector import BasketballDetector

    OUTPUT.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if lock_holder():
            raise SystemExit("Another ball-view benchmark holds %s" % LOCK)
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
        manifest = dict(
            created=datetime.now(timezone.utc).isoformat(),
            experimental=True,
            source=str(args.source.resolve()),
            source_sha256=source_hash,
            metadata=metadata,
            stride=args.stride,
            limit_seconds=args.limit_seconds,
            coverage_scope="partial_video" if args.limit_ms else "full_video",
            device=detector.device,
            model=detector.manifest,
            resolution=704,
            thresholds=detector.thresholds,
            suppression_iou=0.5,
            tile_fraction=TILE_FRACTION,
            ball_gap_ms=BALL_MAX_AGE_MS,
            agreement_center_distance=MATCH_DISTANCE,
            duplicate_center_distance=DUPLICATE_DISTANCE,
            timing="paired: every frame runs every strategy back-to-back, order rotated per frame; "
            "synchronized predict() time only (excludes decode, colour conversion, suppression)",
            warmup_rounds=args.warmup,
            model_load_seconds=load_seconds,
            runtime=dict(
                python=platform.python_version(),
                torch=torch.__version__,
                rfdetr=version("rfdetr"),
                opencv=cv2.__version__,
                platform=platform.platform(),
                mps_fallback=os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"),
            ),
            git=git_state(),
            guard=guard,
            guard_overridden=bool(args.allow_active_jobs),
            definitions=dict(
                detection_coverage="processed frames with >=1 thresholded ball observation / processed frames; not accuracy",
                reference="tiled3: current production full frame + two fixed 60% crops; not ground truth",
            ),
        )
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        strategies, records, parity, aborted = paired_run(
            detector, specs, args, metadata, out
        )
        reference = records[specs[0]]
        ref_seconds = sum(r["detector_seconds"] for r in reference)
        summaries = {}
        frame_keys = [
            (r["frame_index"], r["pts_ms"], r["frame_digest"]) for r in reference
        ]
        for spec, strategy in zip(specs, strategies):
            frames = records[spec]
            seconds = sum(r["detector_seconds"] for r in frames)
            ratios = [
                c["detector_seconds"] / r["detector_seconds"]
                for c, r in zip(frames, reference)
                if r["detector_seconds"]
            ]
            summary = summarize(frames, metadata["duration_ms"])
            summary.update(
                strategy=spec,
                params=strategy.params,
                detector_seconds=seconds,
                reference_detector_seconds=ref_seconds,
                seconds_per_processed_frame=seconds / len(frames) if frames else None,
                median_paired_time_ratio=statistics.median(ratios) if ratios else None,
                views_per_frame=sum(len(r["views"]) for r in frames) / len(frames)
                if frames
                else None,
                view_reasons=dict(Counter(r["reason"] for r in frames)),
                view_attribution=view_attribution(frames),
                identical_frames_to_reference=[
                    (r["frame_index"], r["pts_ms"], r["frame_digest"]) for r in frames
                ]
                == frame_keys,
                agreement=agreement(reference, frames),
            )
            summaries[spec] = summary
        for spec in specs[1:]:
            summaries[spec]["verdict"] = verdict(
                summaries[specs[0]], summaries[spec], summaries[spec]
            )
        derived = {}
        for label, views in (
            ("derived:full", {"full"}),
            ("derived:tiles2", {"left", "right"}),
        ):
            frames = derive(reference, views)
            derived[label] = dict(
                summarize(frames, metadata["duration_ms"]),
                strategy=label,
                derived_from="tiled3 raw views (coverage only, no timing)",
                agreement=agreement(reference, frames),
            )
        result = dict(
            manifest=manifest,
            aborted=aborted,
            production_parity_frames=parity,
            runs=list(summaries.values()),
            derived=list(derived.values()),
        )
        if not aborted:
            result["review_sheets"] = review_sheets(
                args, metadata, specs, records, summaries, out
            )
        (out / "summary.json").write_text(
            json.dumps(result, indent=2, default=float) + "\n"
        )
        print_table(list(summaries.values()) + list(derived.values()))
        print("parity", parity, "aborted", bool(aborted))
        print("wrote", out / "summary.json")
        if aborted:
            raise SystemExit(2)
    finally:
        LOCK.unlink(missing_ok=True)


def print_table(runs):
    print(
        "%-30s %7s %6s %6s %5s %5s %4s %4s %5s %6s %5s %4s %s"
        % (
            "strategy",
            "det_s",
            "ratio",
            "views",
            "ball",
            "segs",
            "1ob",
            "dup",
            "gapF",
            "retain",
            "hoopF",
            "hoop",
            "verdict",
        )
    )
    for r in runs:
        b, a = r["ball"], r.get("agreement", {})
        v = r.get("verdict")
        print(
            "%-30s %7s %6s %6s %5d %5d %4d %4d %5d %6s %5d %4d %s"
            % (
                r["strategy"][:30],
                "%.2f" % r["detector_seconds"] if "detector_seconds" in r else "-",
                "%.2f" % r["median_paired_time_ratio"]
                if r.get("median_paired_time_ratio")
                else "-",
                "%.2f" % r["views_per_frame"] if r.get("views_per_frame") else "-",
                b["frames_with_ball"],
                b["segments"],
                b["single_observation_segments"],
                b["near_duplicate_ball_pairs"],
                b["longest_no_ball_run_frames"],
                "%.3f" % a["reference_retention"]
                if a.get("reference_retention") is not None
                else "-",
                r["hoop"]["frames_with_hoop"],
                r["hoop"]["stable_hoop_count"],
                "-"
                if not v
                else (
                    "PASS"
                    if v["passed"]
                    else "FAIL "
                    + ",".join(k for k, ok in v["checks"].items() if not ok)
                ),
            )
        )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser(
        "preflight", help="Check for active jobs/workers; exit 1 if blocked"
    )
    pre.add_argument(
        "--check-db",
        action="append",
        default=[],
        help="Extra scoutfilm.sqlite3 to scan",
    )
    diag = sub.add_parser(
        "diagnose", help="CPU-only diagnostics of existing basketball folders"
    )
    diag.add_argument(
        "folders",
        nargs="+",
        type=Path,
        help="Folders with report.json/objects.json/ball_tracks.json",
    )
    diag.add_argument(
        "--strategies",
        nargs="+",
        default=["adaptive", "adaptive:full=0", "adaptive:full=0,reacquire=2"],
    )
    diag.add_argument("--output", type=Path)
    rep = sub.add_parser(
        "replay",
        help="CPU-only exact replay of production-view subsets from a recorded run",
    )
    rep.add_argument(
        "run", type=Path, help="Run folder containing tiled3.jsonl and summary.json"
    )
    rep.add_argument(
        "--strategies",
        nargs="+",
        default=["select", "select:learnhoop=1", "select:full=0", "tiles2"],
    )
    rep.add_argument("--output", type=Path)
    run = sub.add_parser(
        "run", help="Model inference (requires --execute and a clear preflight)"
    )
    run.add_argument("source", type=Path)
    run.add_argument("--device", required=True, help="Explicit cpu|mps|cuda:N; no auto")
    run.add_argument(
        "--expect-sha256", help="Refuse unless the source has this SHA-256"
    )
    run.add_argument("--stride", type=int, default=2)
    run.add_argument("--strategies", nargs="+", default=DEFAULT_STRATEGIES)
    run.add_argument(
        "--warmup", type=int, default=3, help="Untimed warm-up rounds per batch shape"
    )
    run.add_argument(
        "--parity-frames",
        type=int,
        default=3,
        help="Extra production detect() calls checking the mirror",
    )
    run.add_argument(
        "--review-limit",
        type=int,
        default=16,
        help="Max lost/extra review rows per strategy",
    )
    run.add_argument("--ball-confidence", type=float, default=0.25)
    run.add_argument("--hoop-confidence", type=float, default=0.35)
    run.add_argument("--limit-seconds", type=float)
    run.add_argument("--output", type=Path, default=OUTPUT)
    run.add_argument("--check-db", action="append", default=[])
    run.add_argument(
        "--execute",
        action="store_true",
        help="Actually load the model and run inference",
    )
    run.add_argument(
        "--allow-active-jobs",
        action="store_true",
        help="Override the guard (recorded in the manifest)",
    )
    args = parser.parse_args()
    if args.command == "preflight":
        state = preflight(args.check_db)
        print(json.dumps(state, indent=2))
        raise SystemExit(0 if state["clear"] else 1)
    try:
        for s in args.strategies:
            parse_strategy(s)
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.command == "diagnose":
        return command_diagnose(args)
    if args.command == "replay":
        return command_replay(args)
    if not 1 <= args.stride <= 5:
        parser.error("Use stride 1-5, matching analyze_basketball.py")
    if args.warmup < 0 or args.parity_frames < 0 or args.review_limit < 0:
        parser.error("warmup, parity-frames and review-limit must be >= 0")
    if args.limit_seconds is not None and args.limit_seconds <= 0:
        parser.error("limit-seconds must be positive")
    if not args.source.is_file():
        parser.error("Source not found")
    args.limit_ms = args.limit_seconds * 1000 if args.limit_seconds else None
    command_run(args)


if __name__ == "__main__":
    main()
