"""Evaluate raw-track and final-player association on fixed TrackID3x3 clips.

Every pipeline variant receives exactly the same cached detections and the same
fixed frame alignment, so differences reflect association only.

Protocol (also written into the report):
* Detections: ``scripts/cache_tracking_detections.py`` (pretrained keypoint
  R-CNN, score >= 0.75, every 5th frame). Sampled frames with zero detections
  are kept, so their ground-truth boxes count as misses.
* Alignment (fixed, never chosen from scores): MOT frame = source frame index
  + 1, because MOT is 1-based. An offset scan is reported as a diagnostic only.
* Detection-to-GT matching: per frame, Hungarian on IoU, kept if IoU >= 0.5,
  ignoring predicted IDs (identical for every variant).
* Metrics per ID kind (raw ``track_id`` and final ``player_id``):
  - coverage: matched GT observations / all GT boxes on sampled frames;
  - id_switches: consecutive matched observations of one GT player carrying
    different IDs (unmatched frames skipped; includes fragment boundaries);
  - fragments: sum over GT players of (distinct IDs - 1);
  - mixed_ids: IDs whose matched observations cover >= 2 GT players;
  - contaminated_observations / purity: observations not belonging to their
    ID's majority GT player, observation-weighted (purity = 1 - share);
  - one_to_one_id_recall: best one-to-one GT-to-ID mapping (Hungarian on
    co-occurrence counts) / matched observations.
  These are custom metrics, not IDF1 or HOTA (no standard evaluator is run).
Teams and roles are evaluated against proxies only (TrackID3x3 indoor MOT has
no team or role labels); see the report notes.

  .venv/bin/python scripts/evaluate_tracking.py              # dev + held-out suite
  .venv/bin/python scripts/evaluate_tracking.py --split dev  # development only
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workers.video.identity import (  # noqa: E402
    PlayerIdentityTracker,
    propose_tracklet_merges,
    uniform_descriptor,
)
from workers.video.teams import (  # noqa: E402
    TeamGrouper,
    flag_non_players,
    torso_colour_feature,
)
from workers.video.tracker import ContinuityTracker, Tracker, iou  # noqa: E402

OUTPUT_ROOT = ROOT / "data" / "claude_tracking"
CACHE = OUTPUT_ROOT / "detections"
MANIFEST = OUTPUT_ROOT / "sources" / "manifest.json"
FRAME_OFFSET = 1
IOU = 0.5
# Fixed variants. "baseline" is the association currently used by the main pipeline.
VARIANTS = {
    "baseline": dict(tracker="iou", identity_continuity=False, appearance=False),
    "continuity_tracker+v1_identity": dict(
        tracker="continuity", identity_continuity=False, appearance=True
    ),
    "baseline_tracker+v2_identity": dict(
        tracker="iou", identity_continuity=True, appearance=False
    ),
    "continuity_tracker+v2_identity": dict(
        tracker="continuity", identity_continuity=True, appearance=True
    ),
}
PROPOSED = "continuity_tracker+v2_identity"
COUNT_KEYS = (
    "gt_boxes",
    "matched_observations",
    "id_switches",
    "fragments",
    "mixed_ids",
    "contaminated_observations",
    "one_to_one_matched",
    "ids_total",
    "ids_on_gt",
)


def load_sequence(tracks_path, report_path):
    report = json.loads(Path(report_path).read_text())
    meta = report["metadata"]
    width, height = meta["width"], meta["height"]
    grouped = defaultdict(list)
    for s in json.loads(Path(tracks_path).read_text()):
        grouped[s["timestamp_ms"]].append(s)
    if "sampled_frames" in report:
        sampled = [(f["index"], f["timestamp_ms"]) for f in report["sampled_frames"]]
    else:  # legacy pipeline output: frames with zero detections are unknown
        sampled = [(round(ts * meta["fps"] / 1000), ts) for ts in sorted(grouped)]
    frames = []
    for index, ts in sampled:
        boxes, keypoints = [], []
        for s in grouped.get(ts, []):
            x, y, w, h = s["bbox"]
            boxes.append([x * width, y * height, (x + w) * width, (y + h) * height])
            kp = s.get("keypoints") or []
            keypoints.append(
                np.array(
                    [
                        [p["x"] * width, p["y"] * height, p.get("visibility", 1)]
                        for p in kp
                    ]
                )
                if kp
                else None
            )
        frames.append(dict(ts=ts, index=index, boxes=boxes, keypoints=keypoints))
    return frames, meta, "sampled_frames" in report


def load_gt(path):
    gt = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        if line.strip():
            f, t, x, y, w, h = map(float, line.split(",")[:6])
            gt[int(f)].append((str(int(t)), [x, y, x + w, y + h]))
    return gt


def match(boxes, gt_boxes, threshold=IOU):
    out = [None] * len(boxes)
    if boxes and gt_boxes:
        scores = np.array([[iou(b, g[1]) for g in gt_boxes] for b in boxes])
        for r, c in zip(*linear_sum_assignment(-scores)):
            if scores[r, c] >= threshold:
                out[r] = gt_boxes[c][0]
    return out


def attach_appearance(frames, video):
    cap = cv2.VideoCapture(str(video))
    wanted = {f["index"]: f for f in frames}
    index = 0
    try:
        while wanted and cap.grab():
            if index in wanted:
                ok, image = cap.retrieve()
                if not ok:
                    break
                f = wanted.pop(index)
                f["uniform"] = [uniform_descriptor(image, b) for b in f["boxes"]]
                f["torso"] = [
                    torso_colour_feature(image, b, k)
                    for b, k in zip(f["boxes"], f["keypoints"])
                ]
                f["image"] = image
            index += 1
    finally:
        cap.release()
    if wanted:
        raise SystemExit(f"Could not decode {len(wanted)} sampled frames from {video}")


def run_variant(frames, spec, game="eval"):
    tracker = Tracker() if spec["tracker"] == "iou" else ContinuityTracker()
    identities = PlayerIdentityTracker(game, continuity=spec["identity_continuity"])
    track_ids, player_ids, tracklets = [], [], []
    for f in frames:
        uniform = f.get("uniform") or [None] * len(f["boxes"])
        if spec["tracker"] == "iou":
            ids = tracker.update(f["boxes"])
        else:
            ids = tracker.update(
                f["boxes"],
                uniform if spec["appearance"] else None,
                timestamp_ms=f["ts"],
            )
        associations = identities.update(ids, f["boxes"], uniform, f["ts"])
        track_ids.append([str(i) for i in ids])
        player_ids.append([a["player_id"] for a in associations])
        tracklets.append([a["track_id"] for a in associations])
    return track_ids, player_ids, tracklets, identities.summary()


def association_metrics(labels, predicted, gt_boxes):
    by_gt, by_pred = defaultdict(list), defaultdict(Counter)
    for gts, preds in zip(labels, predicted):
        for g, p in zip(gts, preds):
            if g is not None:
                by_gt[g].append(p)
                by_pred[p][g] += 1
    matched = sum(len(s) for s in by_gt.values())
    gt_names, pred_names = sorted(by_gt), sorted(by_pred)
    counts = np.array([[by_pred[p][g] for p in pred_names] for g in gt_names])
    one_to_one = 0
    if counts.size:
        rows, cols = linear_sum_assignment(-counts)
        one_to_one = int(counts[rows, cols].sum())
    return dict(
        gt_boxes=gt_boxes,
        matched_observations=matched,
        id_switches=sum(sum(a != b for a, b in zip(s, s[1:])) for s in by_gt.values()),
        fragments=sum(len(set(s)) - 1 for s in by_gt.values()),
        mixed_ids=sum(1 for c in by_pred.values() if len(c) >= 2),
        contaminated_observations=sum(
            sum(c.values()) - max(c.values()) for c in by_pred.values()
        ),
        one_to_one_matched=one_to_one,
        ids_total=len({p for preds in predicted for p in preds}),
        ids_on_gt=len(by_pred),
    )


def finish(counts):
    out = dict(counts)
    matched = counts["matched_observations"]
    out["coverage"] = (
        round(matched / counts["gt_boxes"], 4) if counts["gt_boxes"] else None
    )
    out["purity"] = (
        round(1 - counts["contaminated_observations"] / matched, 4) if matched else None
    )
    out["one_to_one_id_recall"] = (
        round(counts["one_to_one_matched"] / matched, 4) if matched else None
    )
    return out


def truth_by_key(labels, predicted):
    truth = defaultdict(Counter)
    for gts, preds in zip(labels, predicted):
        for g, p in zip(gts, preds):
            truth[p][g] += 1
    out = {}
    for p, c in truth.items():
        gt_only = Counter({k: v for k, v in c.items() if k is not None})
        top = gt_only.most_common(1)
        out[p] = dict(
            gt_fraction=sum(gt_only.values()) / sum(c.values()),
            majority_gt=top[0][0] if top else None,
        )
    return out


def samples_for(frames, predicted, width, height):
    return [
        dict(
            track_id=p,
            timestamp_ms=f["ts"],
            bbox=[
                b[0] / width,
                b[1] / height,
                (b[2] - b[0]) / width,
                (b[3] - b[1]) / height,
            ],
        )
        for f, preds in zip(frames, predicted)
        for b, p in zip(f["boxes"], preds)
    ]


def evaluate_roles_teams_merges(frames, labels, keys, width, height, crops_path=None):
    """Roles, teams and merge proposals on final-player tracklet keys (proxies only)."""
    samples = samples_for(frames, keys, width, height)
    aspect = width / height
    truth = truth_by_key(labels, keys)
    first = flag_non_players(samples, aspect=aspect)
    flagged = [k for k, v in first.items() if v["role_status"] != "unknown"]
    grouper = TeamGrouper()
    for f, preds in zip(frames, keys):
        for feature, p in zip(f.get("torso") or [], preds):
            grouper.observe(p, feature, f["ts"])
    teams, team_summary = grouper.assign(exclude=flagged)
    roles = flag_non_players(samples, aspect=aspect, team_assignments=teams)
    role_tally, gt_obs_flagged = Counter(), Counter()
    obs_per_key = Counter(p for preds in keys for p in preds)
    for k, flag in roles.items():
        kind = "mostly_gt_player" if truth[k]["gt_fraction"] >= 0.5 else "mostly_non_gt"
        role_tally[f"{flag['role_status']}/{kind}"] += 1
        if flag["role_status"] != "unknown":
            gt_obs_flagged[flag["role_status"]] += round(
                truth[k]["gt_fraction"] * obs_per_key[k]
            )
    per_gt = defaultdict(Counter)
    for gts, preds in zip(labels, keys):
        for g, p in zip(gts, preds):
            if g is not None:
                per_gt[g][teams.get(p, {}).get("team", "unknown")] += 1
    gt_obs = sum(sum(c.values()) for c in per_gt.values())
    assigned = sum(n for c in per_gt.values() for t, n in c.items() if t != "unknown")
    # A GT player carrying both team labels is an observed mistake (a player cannot
    # change sides). Whether two GT players belong together cannot be checked
    # automatically without team ground truth; see the torso crop sheet.
    conflicts = sorted(g for g, c in per_gt.items() if c["Team A"] and c["Team B"])
    minority = sum(
        min(c["Team A"], c["Team B"])
        for c in per_gt.values()
        if c["Team A"] and c["Team B"]
    )
    if crops_path is not None:
        team_contact_sheet(frames, labels, keys, teams, crops_path)
    descriptors = defaultdict(list)
    for f, preds in zip(frames, keys):
        for d, p in zip(f.get("uniform") or [], preds):
            if d is not None:
                descriptors[p].append(d)
    proposals = propose_tracklet_merges(
        samples,
        descriptors={p: np.mean(v, axis=0) for p, v in descriptors.items()},
        teams=teams,
        aspect=aspect,
    )
    merge_tally = Counter()
    for p in proposals:
        a, b = truth[p["source_track_id"]], truth[p["target_track_id"]]
        if a["gt_fraction"] < 0.5 or b["gt_fraction"] < 0.5:
            verdict = "unverifiable_non_gt"
        else:
            verdict = "correct" if a["majority_gt"] == b["majority_gt"] else "incorrect"
        merge_tally[f"{p['status']}/{verdict}"] += 1
    return dict(
        roles=dict(
            by_status=dict(role_tally),
            gt_player_observations_flagged=dict(gt_obs_flagged),
            flags={k: v for k, v in roles.items() if v["role_status"] != "unknown"},
        ),
        teams=dict(
            summary=team_summary,
            excluded_flagged_keys=sorted(flagged),
            gt_observations=gt_obs,
            assigned_observations=assigned,
            assigned_fraction=round(assigned / gt_obs, 4) if gt_obs else None,
            gt_players_with_any_label=sum(
                1 for c in per_gt.values() if c["Team A"] or c["Team B"]
            ),
            observed_side_conflicts=conflicts,
            observations_in_minority_label=minority,
            per_gt_player={g: dict(c) for g, c in sorted(per_gt.items())},
        ),
        merge_proposals=dict(
            count=len(proposals), by_status_and_verdict=dict(merge_tally)
        ),
    )


def team_contact_sheet(frames, labels, keys, teams, path):
    tiles = defaultdict(list)
    for f, gts, preds in zip(frames, labels, keys):
        if "image" not in f:
            continue
        for b, g, p in zip(f["boxes"], gts, preds):
            team = teams.get(p, {}).get("team", "unknown")
            if g is None or len(tiles[(g, team)]) >= 4:
                continue
            x1, y1, x2, y2 = (int(round(v)) for v in b)
            h = y2 - y1
            # Torso band only: no heads or faces in the review sheet.
            crop = f["image"][
                max(0, y1 + int(0.22 * h)) : y1 + int(0.6 * h), max(0, x1) : x2
            ]
            if crop.size:
                tiles[(g, team)].append(cv2.resize(crop, (48, 48)))
    rows = []
    for g, team in sorted(tiles):
        row = np.zeros((48, 48 * 4 + 160, 3), np.uint8)
        cv2.putText(
            row,
            f"gt{g} {team}",
            (2, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
        )
        for i, t in enumerate(tiles[(g, team)]):
            row[:, 160 + 48 * i : 160 + 48 * (i + 1)] = t
        rows.append(row)
    if rows:
        cv2.imwrite(str(path), np.vstack(rows))


def evaluate_sequence(name, tracks, report, gt_path, video, out_dir):
    frames, meta, complete = load_sequence(tracks, report)
    width, height = meta["width"], meta["height"]
    if video:
        attach_appearance(frames, video)
    gt = load_gt(gt_path)
    scan = {
        str(o): sum(
            sum(m is not None for m in match(f["boxes"], gt.get(f["index"] + o, [])))
            for f in frames
        )
        for o in range(-2, 4)
    }
    labels = [match(f["boxes"], gt.get(f["index"] + FRAME_OFFSET, [])) for f in frames]
    gt_boxes = sum(len(gt.get(f["index"] + FRAME_OFFSET, [])) for f in frames)
    detections = sum(len(f["boxes"]) for f in frames)
    matched = sum(sum(m is not None for m in ls) for ls in labels)
    result = dict(
        sampled_frames=len(frames),
        frames_without_detections=sum(1 for f in frames if not f["boxes"]),
        zero_detection_frames_known=complete,
        alignment_scan_diagnostic=scan,
        detector=dict(
            gt_boxes=gt_boxes,
            detections=detections,
            matched=matched,
            recall=round(matched / gt_boxes, 4) if gt_boxes else None,
            precision_vs_annotated_players=round(matched / detections, 4)
            if detections
            else None,
        ),
        variants={},
    )
    for variant, spec in VARIANTS.items():
        track_ids, player_ids, tracklets, summary = run_variant(frames, spec)
        result["variants"][variant] = dict(
            track_id=finish(association_metrics(labels, track_ids, gt_boxes)),
            player_id=finish(association_metrics(labels, player_ids, gt_boxes)),
            identity_summary=summary,
        )
        if variant == PROPOSED and video:
            result["roles_teams_merges"] = evaluate_roles_teams_merges(
                frames,
                labels,
                tracklets,
                width,
                height,
                out_dir / f"{name}_team_crops.jpg",
            )
    return result


def aggregate(sequences, names):
    out = {}
    for variant in VARIANTS:
        out[variant] = {}
        for kind in ("track_id", "player_id"):
            counts = {
                k: sum(sequences[n]["variants"][variant][kind][k] for n in names)
                for k in COUNT_KEYS
            }
            out[variant][kind] = finish(counts)
    return out


def print_table(report):
    print(
        f"{'sequence':22s} {'variant':31s} {'kind':9s}  cov    sw frag mix contam purity  1:1"
    )
    rows = [(n, s["variants"]) for n, s in report["sequences"].items()]
    rows += [(f"AGG:{k}", v["metrics"]) for k, v in report.get("aggregate", {}).items()]
    for name, variants in rows:
        for variant, kinds in variants.items():
            for kind in ("track_id", "player_id"):
                m = kinds[kind]
                print(
                    f"{name:22s} {variant:31s} {kind:9s} {m['coverage']:.3f} {m['id_switches']:4d}"
                    f" {m['fragments']:4d} {m['mixed_ids']:3d} {m['contaminated_observations']:6d}"
                    f"  {m['purity']:.3f} {m['one_to_one_id_recall']:.3f}"
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="all")
    parser.add_argument("--out", default="data/claude_tracking/eval")
    parser.add_argument("--no-appearance", action="store_true")
    # Single-sequence mode (e.g. the original pipeline output in data/first_run).
    parser.add_argument("--tracks")
    parser.add_argument("--report")
    parser.add_argument("--gt")
    parser.add_argument("--video")
    args = parser.parse_args()
    out = (ROOT / args.out).resolve()
    if OUTPUT_ROOT.resolve() not in [out, *out.parents]:
        raise SystemExit("Outputs must stay under data/claude_tracking/")
    out.mkdir(parents=True, exist_ok=True)
    if args.tracks:
        jobs = {
            "single": dict(
                split="single",
                tracks=ROOT / args.tracks,
                report=ROOT / args.report,
                gt=ROOT / args.gt,
                video=None
                if args.no_appearance or not args.video
                else ROOT / args.video,
            )
        }
    else:
        manifest = json.loads(MANIFEST.read_text())
        jobs = {
            name: dict(
                split=m["split"],
                tracks=CACHE / name / "tracks.json",
                report=CACHE / name / "report.json",
                gt=ROOT / m["annotation"],
                video=None if args.no_appearance else ROOT / m["video"],
            )
            for name, m in manifest.items()
            if args.split == "all" or m["split"] == args.split
        }
    report = dict(
        protocol=__doc__.split("  .venv")[0].strip(),
        fixed=dict(frame_offset=FRAME_OFFSET, iou=IOU, variants=VARIANTS),
        splits={n: j["split"] for n, j in jobs.items()},
        sequences={},
    )
    for name, job in jobs.items():
        report["sequences"][name] = evaluate_sequence(
            name, job["tracks"], job["report"], job["gt"], job["video"], out
        )
    for split in sorted({j["split"] for j in jobs.values()}):
        names = [n for n, j in jobs.items() if j["split"] == split]
        report.setdefault("aggregate", {})[split] = dict(
            sequences=names, metrics=aggregate(report["sequences"], names)
        )
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print_table(report)
    print("Report:", out / "report.json")


if __name__ == "__main__":
    main()
