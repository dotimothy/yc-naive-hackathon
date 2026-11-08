"""Compare the baseline and continuity trackers on cached detections vs MOT ground truth.

Every tracker variant receives exactly the same detections (extracted from a
cached ``tracks.json`` produced by the pipeline), so differences reflect
association only. Detector coverage is reported separately.

Protocol (also written into the report):
* Frame alignment: source frame index = round(timestamp_ms * fps / 1000);
  MOT frame = index + ``--frame-offset`` (default 1, MOT is 1-based). An offset
  scan is reported so the choice can be checked.
* Detection↔GT matching: per frame, Hungarian on IoU, kept if IoU >= ``--iou``.
  The matching ignores predicted IDs and is therefore identical for all variants.
* ID switch: for one GT identity, two consecutive *matched* observations (unmatched
  frames skipped) carry different predicted IDs.
* Fragmentation: distinct predicted IDs per GT identity (1.0 is ideal).
* Mixed predicted ID: a predicted ID whose matched observations cover >= 2 GT
  identities (an identity mix, worse than a fragment).
* one_to_one_id_recall: best one-to-one GT↔predicted ID mapping (Hungarian on
  co-occurrence counts) / matched observations. This is *not* IDF1 or HOTA.
Stress scenarios (``stride10``, ``drop15``) are synthetic perturbations of the
same cached detections and are labelled as such.

Run (CPU only, reads cached predictions; writes only under data/claude_tracking):
  .venv/bin/python scripts/evaluate_tracking.py
"""

import argparse
import json
import random
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
from workers.video.teams import TeamGrouper, flag_non_players, torso_colour_feature  # noqa: E402
from workers.video.tracker import ContinuityTracker, Tracker, iou  # noqa: E402

OUTPUT_ROOT = ROOT / "data" / "claude_tracking"


def load_frames(path, fps, width, height):
    samples = json.loads(Path(path).read_text())
    grouped = defaultdict(list)
    for sample in samples:
        grouped[sample["timestamp_ms"]].append(sample)
    frames = []
    for ts in sorted(grouped):
        boxes, keypoints = [], []
        for s in grouped[ts]:
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
        frames.append(
            dict(ts=ts, index=round(ts * fps / 1000), boxes=boxes, keypoints=keypoints)
        )
    return frames


def load_gt(path):
    gt = defaultdict(list)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        f, t, x, y, w, h = map(float, line.split(",")[:6])
        gt[int(f)].append((str(int(t)), [x, y, x + w, y + h]))
    return gt


def match(boxes, gt_boxes, threshold):
    out = [None] * len(boxes)
    if not boxes or not gt_boxes:
        return out
    scores = np.array([[iou(b, g[1]) for g in gt_boxes] for b in boxes])
    rows, cols = linear_sum_assignment(-scores)
    for r, c in zip(rows, cols):
        if scores[r, c] >= threshold:
            out[r] = gt_boxes[c][0]
    return out


def attach_appearance(frames, video):
    cap = cv2.VideoCapture(str(video))
    wanted = {f["index"]: f for f in frames}
    index = 0
    try:
        while wanted:
            ok = cap.grab()
            if not ok:
                break
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


def scenario(frames, name, seed):
    if name == "full":
        return frames
    if name == "stride10":
        return frames[::2]
    if name == "drop15":
        rng = random.Random(seed)
        out = []
        for f in frames:
            keep = [i for i in range(len(f["boxes"])) if rng.random() >= 0.15]
            out.append(
                {
                    k: ([v[i] for i in keep] if isinstance(v, list) else v)
                    for k, v in f.items()
                }
            )
        return out
    raise ValueError(name)


def run_variant(frames, variant, game="eval", margin=None):
    if variant == "baseline":
        tracker = Tracker()
    else:
        tracker = (
            ContinuityTracker() if margin is None else ContinuityTracker(margin=margin)
        )
    identities = PlayerIdentityTracker(game)
    track_ids, player_ids = [], []
    for f in frames:
        uniform = f.get("uniform") or [None] * len(f["boxes"])
        if variant == "baseline":
            ids = tracker.update(f["boxes"])
        elif variant == "continuity_motion":
            ids = tracker.update(f["boxes"], timestamp_ms=f["ts"])
        else:
            ids = tracker.update(f["boxes"], uniform, timestamp_ms=f["ts"])
        track_ids.append([str(i) for i in ids])
        associations = (
            identities.update(ids, f["boxes"], uniform, f["ts"]) if f["boxes"] else []
        )
        player_ids.append([a["player_id"] for a in associations])
    extra = getattr(tracker, "stats", None)
    return track_ids, player_ids, extra


def association_metrics(frames, labels, predicted):
    by_gt = defaultdict(list)
    by_pred = defaultdict(Counter)
    for f, gts, preds in zip(frames, labels, predicted):
        for g, p in zip(gts, preds):
            if g is not None:
                by_gt[g].append(p)
                by_pred[p][g] += 1
    switches = sum(sum(a != b for a, b in zip(seq, seq[1:])) for seq in by_gt.values())
    matched = sum(len(seq) for seq in by_gt.values())
    gt_names, pred_names = sorted(by_gt), sorted(by_pred)
    counts = (
        np.array([[by_pred[p][g] for p in pred_names] for g in gt_names])
        if gt_names
        else np.zeros((0, 0))
    )
    rows, cols = linear_sum_assignment(-counts) if counts.size else ([], [])
    all_ids = {p for preds in predicted for p in preds}
    return dict(
        matched_observations=matched,
        id_switches=int(switches),
        pred_ids_per_gt=round(float(np.mean([len(set(s)) for s in by_gt.values()])), 3)
        if by_gt
        else None,
        mixed_pred_ids=sum(1 for c in by_pred.values() if len(c) >= 2),
        pred_ids_on_gt=len(by_pred),
        pred_ids_total=len(all_ids),
        one_to_one_id_recall=round(float(counts[rows, cols].sum()) / matched, 4)
        if matched
        else None,
    )


def tracklet_truth(frames, labels, predicted):
    truth = defaultdict(Counter)
    for gts, preds in zip(labels, predicted):
        for g, p in zip(gts, preds):
            truth[p][g] += 1
    out = {}
    for p, counts in truth.items():
        total = sum(counts.values())
        gt_counts = Counter({k: v for k, v in counts.items() if k is not None})
        top = gt_counts.most_common(1)
        out[p] = dict(
            total=total,
            gt_fraction=round(sum(gt_counts.values()) / total, 3),
            majority_gt=top[0][0] if top else None,
        )
    return out


def samples_for(frames, predicted, width, height):
    samples = []
    for f, preds in zip(frames, predicted):
        for b, p in zip(f["boxes"], preds):
            samples.append(
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
            )
    return samples


def evaluate_merges(frames, labels, predicted, width, height):
    descriptors = defaultdict(list)
    for f, preds in zip(frames, predicted):
        for d, p in zip(f.get("uniform") or [], preds):
            if d is not None:
                descriptors[p].append(d)
    means = {p: np.mean(v, axis=0) for p, v in descriptors.items()}
    proposals = propose_tracklet_merges(
        samples_for(frames, predicted, width, height),
        descriptors=means,
        aspect=width / height,
    )
    truth = tracklet_truth(frames, labels, predicted)
    tally = Counter()
    for p in proposals:
        a, b = truth[p["source_track_id"]], truth[p["target_track_id"]]
        if a["gt_fraction"] < 0.5 or b["gt_fraction"] < 0.5:
            verdict = "unverifiable_non_gt"
        else:
            verdict = "correct" if a["majority_gt"] == b["majority_gt"] else "incorrect"
        p["evaluation"] = verdict
        tally[(p["status"], verdict)] += 1
    return dict(
        proposals=len(proposals),
        by_status_and_verdict={f"{s}/{v}": n for (s, v), n in sorted(tally.items())},
        examples=proposals[:12],
    )


def evaluate_teams(frames, labels, predicted, exclude=()):
    oracle, tracked = TeamGrouper(), TeamGrouper()
    for f, gts, preds in zip(frames, labels, predicted):
        for feature, g, p in zip(f.get("torso") or [], gts, preds):
            tracked.observe(p, feature, f["ts"])
            if g is not None:
                oracle.observe("gt-" + g, feature, f["ts"])
    oracle_assign, oracle_summary = oracle.assign()
    tracked_assign, tracked_summary = tracked.assign(exclude=exclude)
    per_gt = defaultdict(Counter)
    for gts, preds in zip(labels, predicted):
        for g, p in zip(gts, preds):
            if g is not None:
                per_gt[g][tracked_assign.get(p, {}).get("team", "unknown")] += 1
    return (
        dict(
            note="GT has no team labels; oracle uses GT identities only to isolate colour grouping "
            "from tracking errors. Partition correctness requires visual review "
            "(see team_crops.jpg).",
            oracle_gt_tracks=dict(summary=oracle_summary, assignments=oracle_assign),
            tracked=dict(
                summary=tracked_summary,
                excluded_flagged_tracks=sorted(exclude),
                label_counts=dict(Counter(a["team"] for a in tracked_assign.values())),
            ),
            tracked_label_distribution_per_gt={
                g: dict(c) for g, c in sorted(per_gt.items())
            },
        ),
        oracle_assign,
        tracked_assign,
    )


def evaluate_non_players(frames, labels, predicted, width, height, teams):
    flags = flag_non_players(
        samples_for(frames, predicted, width, height),
        aspect=width / height,
        team_assignments=teams,
    )
    truth = tracklet_truth(frames, labels, predicted)
    tally = Counter()
    for p, flag in flags.items():
        kind = "mostly_gt_player" if truth[p]["gt_fraction"] >= 0.5 else "mostly_non_gt"
        flag["gt_fraction"] = truth[p]["gt_fraction"]
        tally[(flag["role_status"], kind)] += 1
    return dict(
        note="non-GT detections are people not among the six annotated on-court players "
        "(spectators, bench, staff) or poorly localized boxes; this is a proxy, not "
        "role ground truth.",
        by_status={f"{s}/{k}": n for (s, k), n in sorted(tally.items())},
        flags=flags,
    )


def team_contact_sheet(frames, labels, assignments, path):
    tiles = defaultdict(list)
    for f, gts in zip(frames, labels):
        if "image" not in f or len(tiles) > 6 and f["ts"] % 4:
            continue
        for b, g in zip(f["boxes"], gts):
            if g is None:
                continue
            team = assignments.get("gt-" + g, {}).get("team", "unknown")
            if len(tiles[(team, g)]) >= 4:
                continue
            x1, y1, x2, y2 = (int(round(v)) for v in b)
            h = y2 - y1
            # Torso band only for the sheet as well: no heads or faces.
            crop = f["image"][
                max(0, y1 + int(0.22 * h)) : y1 + int(0.6 * h), max(0, x1) : x2
            ]
            if crop.size:
                tiles[(team, g)].append(cv2.resize(crop, (48, 48)))
    rows = []
    for team, g in sorted(tiles):
        row = np.zeros((48, 48 * 4 + 160, 3), np.uint8)
        cv2.putText(
            row,
            f"{team} gt{g}",
            (2, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
        )
        for i, t in enumerate(tiles[(team, g)]):
            row[:, 160 + 48 * i : 160 + 48 * (i + 1)] = t
        rows.append(row)
    if rows:
        cv2.imwrite(str(path), np.vstack(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tracks", default="data/first_run/tracks.json")
    parser.add_argument(
        "--report",
        default="data/first_run/report.json",
        help="pipeline report providing fps/width/height",
    )
    parser.add_argument(
        "--gt", default="data/trackid3x3/ground_truth/Indoor/MOT/basket_S1T1_pre.txt"
    )
    parser.add_argument(
        "--video",
        default="data/trackid3x3/basket_S1T1_pre.mp4",
        help="source video for appearance descriptors; 'none' to skip",
    )
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--frame-offset", type=int, default=1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="data/claude_tracking/indoor_S1T1")
    args = parser.parse_args()

    out = (ROOT / args.out).resolve()
    if OUTPUT_ROOT.resolve() not in [out, *out.parents]:
        raise SystemExit("Outputs must stay under data/claude_tracking/")
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((ROOT / args.report).read_text())["metadata"]
    fps, width, height = meta["fps"], meta["width"], meta["height"]
    frames = load_frames(ROOT / args.tracks, fps, width, height)
    if args.video != "none":
        attach_appearance(frames, ROOT / args.video)
    gt = load_gt(ROOT / args.gt) if args.gt != "none" else None

    report = dict(
        protocol=__doc__.split("Run (")[0].strip(),
        inputs=dict(
            tracks=args.tracks,
            gt=args.gt,
            video=args.video,
            fps=fps,
            width=width,
            height=height,
            iou_threshold=args.iou,
            frame_offset=args.frame_offset,
            sampled_frames=len(frames),
            detections=sum(len(f["boxes"]) for f in frames),
            appearance=args.video != "none",
        ),
        variants={},
    )
    variants = ["baseline", "continuity_motion"]
    if args.video != "none":
        variants.append("continuity_appearance")
    if gt is not None:
        scan = {}
        for offset in range(-2, 4):
            hits = sum(
                sum(
                    m is not None
                    for m in match(
                        f["boxes"], gt.get(f["index"] + offset, []), args.iou
                    )
                )
                for f in frames
            )
            scan[str(offset)] = hits
        report["alignment_scan_matched_gt_boxes"] = scan
    for name in ["full", "stride10", "drop15"]:
        subset = scenario(frames, name, args.seed)
        labels = [
            match(f["boxes"], gt.get(f["index"] + args.frame_offset, []), args.iou)
            if gt
            else [None] * len(f["boxes"])
            for f in subset
        ]
        block = {}
        if gt is not None:
            gt_boxes = sum(
                len(gt.get(f["index"] + args.frame_offset, [])) for f in subset
            )
            matched = sum(sum(m is not None for m in ls) for ls in labels)
            dets = sum(len(f["boxes"]) for f in subset)
            block["detector_coverage"] = dict(
                gt_boxes=gt_boxes,
                detections=dets,
                matched=matched,
                gt_recall=round(matched / gt_boxes, 4) if gt_boxes else None,
                detections_matching_gt=round(matched / dets, 4) if dets else None,
            )
        for variant in variants:
            track_ids, player_ids, stats = run_variant(subset, variant)
            entry = dict(tracker_stats=stats)
            if gt is not None:
                entry["track_id"] = association_metrics(subset, labels, track_ids)
                entry["player_id_after_identity_layer"] = association_metrics(
                    subset, labels, player_ids
                )
            else:
                entry["track_ids_total"] = len({p for ids in track_ids for p in ids})
            block[variant] = entry
            if name == "full" and variant == variants[-1]:
                final = (subset, labels, track_ids)
        report["variants"][name] = block
        if gt is not None:
            sweep = {}
            for variant in variants[1:]:
                for margin in (0.0, 0.04, 0.08, 0.15):
                    ids, _, stats = run_variant(subset, variant, margin=margin)
                    metrics = association_metrics(subset, labels, ids)
                    sweep[f"{variant}@margin={margin}"] = dict(
                        ambiguous_rejections=stats["ambiguous_rejections"],
                        **{
                            k: metrics[k]
                            for k in (
                                "id_switches",
                                "mixed_pred_ids",
                                "pred_ids_per_gt",
                                "one_to_one_id_recall",
                            )
                        },
                    )
            report.setdefault("margin_sweep", {})[name] = sweep
    if gt is not None:
        subset, labels, track_ids = final
        report["merge_proposals"] = evaluate_merges(
            subset, labels, track_ids, width, height
        )
        first_pass = flag_non_players(
            samples_for(subset, track_ids, width, height), aspect=width / height
        )
        flagged = [k for k, v in first_pass.items() if v["role_status"] != "unknown"]
        if args.video != "none":
            team_report, oracle_assign, tracked_assign = evaluate_teams(
                subset, labels, track_ids, exclude=flagged
            )
            report["teams"] = team_report
            team_contact_sheet(subset, labels, oracle_assign, out / "team_crops.jpg")
        else:
            tracked_assign = None
        report["non_players"] = evaluate_non_players(
            subset, labels, track_ids, width, height, tracked_assign
        )
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    print(
        json.dumps(
            {k: report[k] for k in ["inputs", "variants"]}, indent=2, default=str
        )
    )
    print("Report:", out / "report.json")


if __name__ == "__main__":
    main()
