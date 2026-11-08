# Tracking continuity, team grouping and non-player flags: handoff

Stream: Video (tracking). Branch `work/claude-tracking` (base `ac430fc`). Nothing here is wired into
`pipeline.py`, the API, the database or the frontend yet. The integration snippets below are proposals for
the stream owners and integration lead.

## Changed files

| File | Change |
| --- | --- |
| `workers/video/tracker.py` | Original `Tracker` and `iou` are **unchanged**. New `ContinuityTracker` adds constant-velocity prediction, short-occlusion recovery, optional appearance gating and ambiguity rejection. `update(boxes)` still works. |
| `workers/video/identity.py` | `PlayerIdentityTracker` and `uniform_descriptor` are **unchanged** (no original lines edited). New: `propose_tracklet_merges(...)`, which proposes reviewable merges with evidence. |
| `workers/video/teams.py` (new) | `torso_box`, `torso_colour_feature`, `TeamGrouper` (anonymous Team A/B/unknown) and `flag_non_players`. |
| `scripts/evaluate_tracking.py` (new) | Compares trackers on identical cached detections against MOT ground truth and writes a JSON report. |
| `backend/tests/test_tracking_continuity.py`, `backend/tests/test_teams.py` (new) | 24 deterministic tests. |

## API

```python
from workers.video.tracker import ContinuityTracker
tracker = ContinuityTracker()               # defaults: margin=0.08, max_lost_ms=1500, max_age=6
ids = tracker.update(boxes)                 # backward compatible; one update = one time unit
ids = tracker.update(boxes, descriptors, timestamp_ms=t)   # recommended
tracker.last_details  # per box: {track_id, status: continued|recovered|new|ambiguous, cost,
                      #           missed_updates, ambiguous_with: [track ids]}
tracker.stats         # {created, recovered, ambiguous_rejections}
```

* The tracker returns one integer per box in input order. The IDs follow people when detections are
  reordered. New IDs are numbered in input order.
* Timestamps are source milliseconds and must strictly increase. Use timestamps on every call or on
  none. With timestamps, a track expires after `max_lost_ms`. Without them, it expires after `max_age`
  missed updates.
* Descriptors are ignored for any box whose IoU with another detection is above 0.3, because the crop
  mixes two people's kits.
* Ambiguity: if another detection, another track seen at least as recently, or a swap of two pairs costs
  within `margin` of the chosen match, the match is rejected. The detection then gets a new
  *provisional* ID, and that ID carries a cost penalty for its first two updates so the contested
  original tracks can reclaim the person. When the evidence is unclear, the tracker fragments instead of
  switching.

```python
from workers.video.teams import TeamGrouper, flag_non_players, torso_colour_feature
feature = torso_colour_feature(frame_bgr, box_xyxy, keypoints_17x3_or_None)  # [0.5L*, a*, b*] or None
grouper = TeamGrouper(); grouper.observe(key, feature, timestamp_ms)
assignments, summary = grouper.assign(exclude=keys)   # {key: {team, reason, distance_*}}, summary
flags = flag_non_players(samples, aspect=w / h, court_polygon=None, team_assignments=assignments)
#  {key: {role_status: likely_non_player|review_required|unknown, signals, reason, evidence}}

from workers.video.identity import propose_tracklet_merges
proposals = propose_tracklet_merges(samples, descriptors={track: vec}, teams=assignments, aspect=w / h)
#  [{source_track_id, target_track_id, status: proposed|ambiguous, score, alternatives,
#    evidence{gap_ms, displacement_heights, size_ratio, appearance_distance, team_*},
#    identity_confirmed: False, requires_review: True}]
```

Team rules:
* Colour is read from the torso only: the shoulder-to-hip area from pose keypoints, or else a band at
  22–52% of box height. No head or face pixels are used.
* Per-key median colours are grouped into cohesive colour modes. Two modes become Team A and Team B
  only when each has at least two members, the two are at least 18 ΔE apart, and the second mode has at
  least 1.5× the support of any third mode. If only the largest mode dominates, it alone becomes Team A.
  Everything else stays `unknown`.
* Names come from first appearance, never from colour.

Non-player rules:
* `likely_non_player` needs at least 2 signals, and one of them must be positional (`stationary` or
  `outside_court`).
* One posture or position signal gives `review_required`.
* Colour alone never flags anything.
* Tracks with fewer than 6 observations, or spanning less than 3 s, stay `unknown`.
* Nothing is deleted. Referees are not targeted.

## Proposed pipeline integration (not applied)

In `workers/video/pipeline.py` (Video stream owner):

```python
from workers.video.tracker import ContinuityTracker
from workers.video.teams import TeamGrouper, flag_non_players, torso_colour_feature
from workers.video.identity import propose_tracklet_merges

tracker, samples, processed = ContinuityTracker(), [], 0      # was Tracker()
teams = TeamGrouper()
...
boxes = result["boxes"][keep].numpy()
keypoints = result["keypoints"][keep].numpy()                 # (N, 17, 3) pixels + visibility
descriptors = [uniform_descriptor(frame, box) for box in boxes]
ids = tracker.update(boxes, descriptors, timestamp_ms=timestamp)
associations = identities.update(ids, boxes, descriptors, timestamp)
for j, box in enumerate(boxes):
    teams.observe(associations[j]["track_id"], torso_colour_feature(frame, box, keypoints[j]), timestamp)
...
# after the frame loop, before build_highlights(...)
aspect = metadata["width"] / metadata["height"]
court = options.get("court_polygon")   # same normalized image polygon the pipeline already uses
first = flag_non_players(samples, aspect=aspect, court_polygon=court)
team_map, team_summary = teams.assign(
    exclude=[k for k, v in first.items() if v["role_status"] != "unknown"]
)
roles = flag_non_players(samples, aspect=aspect, court_polygon=court, team_assignments=team_map)
merge_proposals = propose_tracklet_merges(samples, teams=team_map, aspect=aspect)
for s in samples:
    s["team_label"] = team_map.get(s["track_id"], {}).get("team", "unknown")
    s["role_status"] = roles.get(s["track_id"], {}).get("role_status", "unknown")
# report: team_summary, roles, merge_proposals (all reviewable, identity_confirmed=False)
```

Notes for the integration lead:
* `team_label`, `role_status` and merge proposals are **new contract fields**. Add them to
  `docs/API_CONTRACT.md`, the backend and `frontend/src/types.ts` before relying on them.
* Team labels and roles are computed only at the end of the video, so live `tracks.batch` events will not
  carry them unless the grouper is also queried periodically.
* Exclude every flagged track from team grouping, as in the snippet above. On the indoor clip, the
  dark-clothed spectators formed a fake "team" until they were excluded.
* For the most continuity, use `ContinuityTracker(margin=0.0)`. That turns off ambiguity rejection; the
  measured trade-off is in the margin sweep below.

## Measured comparison

Command (CPU only, cached detections, about 2 s):

```sh
.venv/bin/python scripts/evaluate_tracking.py
# outdoor (no ground truth: ID counts only)
.venv/bin/python scripts/evaluate_tracking.py --tracks data/benchmarks/cpu/tracks.json \
  --report data/benchmarks/cpu/report.json --gt none \
  --video data/trackid3x3/outdoor_stationary_12s.mp4 --out data/claude_tracking/outdoor_cpu_nogt
```

Outputs: `data/claude_tracking/indoor_S1T1/report.json`, `team_crops.jpg` (torso-only crops) and
`data/claude_tracking/outdoor_cpu_nogt/report.json`.

Inputs:
* Cached keypoint R-CNN detections in `data/first_run/tracks.json`: `basket_S1T1_pre.mp4`, stride 5,
  34 sampled frames, 300 detections.
* TrackID3x3 indoor MOT ground truth `basket_S1T1_pre.txt` (CC BY 4.0, pinned
  `9822a3dd…`; see `docs/datasets/TRACKID3X3.md`).
* MOT frame = round(ts·fps/1000) + 1. An offset scan confirmed +1/+2 as the best alignment (182/183
  matched boxes).
* Detections are matched to ground truth by Hungarian assignment on IoU, with **IoU ≥ 0.5**. The
  matching ignores predicted IDs, so every variant is scored on the same matches.

Definitions:
* **ID switch**: consecutive *matched* observations of the same ground-truth player that carry
  different predicted IDs. This includes switches caused by fragmentation.
* **IDs/GT**: distinct predicted IDs per ground-truth player.
* **Mixed IDs**: predicted IDs that cover two or more ground-truth players. These are identity
  contamination, the worst error for player highlights.
* **1:1 ID recall**: matched observations covered by the best one-to-one ground-truth↔predicted ID
  mapping. **This is not IDF1 or HOTA**; no standard evaluator was run.

Detector coverage is the same for every variant:

| Scenario | GT recall @0.5 | Detections matching GT |
| --- | --- | --- |
| full (34 frames, 250 ms) | 0.892 | 0.607 |
| stride10 (every 2nd frame, synthetic) | 0.892 | 0.615 |
| drop15 (15% detections dropped, seed 7, synthetic) | 0.745 | 0.608 |

About 40% of detections are people who are not among the six annotated players: spectators, bench and
staff.

Association results. Each cell shows the raw `track_id` result, then the result after the existing
`PlayerIdentityTracker` (`player_id`):

| Scenario | Variant | ID switches | Mixed IDs | IDs/GT | 1:1 ID recall |
| --- | --- | --- | --- | --- | --- |
| full | baseline `Tracker` | 34 / 32 | 7 / 3 | 6.33 | 0.429 / 0.582 |
| full | continuity, motion only | 23 / 30 | 5 / 2 | 3.83 | 0.670 / 0.560 |
| full | continuity + uniform descriptor | **13 / 19** | **2 / 2** | **2.50** | **0.841 / 0.670** |
| stride10 | baseline | 40 / 28 | 10 / 3 | 7.17 | 0.396 / 0.505 |
| stride10 | continuity + uniform | 16 / 24 | 3 / 3 | 3.33 | 0.626 / 0.527 |
| drop15 | baseline | 34 / 28 | 7 / 1 | 6.67 | 0.434 / 0.632 |
| drop15 | continuity + uniform | 21 / 24 | 3 / 2 | 3.50 | 0.750 / 0.684 |

Margin sweep for continuity + uniform, full clip (switches / mixed / 1:1):

| Margin | Switches / mixed / 1:1 |
| --- | --- |
| 0.0 | 12 / 3 / 0.846 |
| 0.04 | 12 / 3 / 0.846 |
| 0.08 (default) | 13 / 2 / 0.841 |
| 0.15 | 16 / 2 / 0.808 |

With motion only, rejection costs more continuity:

| Margin | Switches / mixed / 1:1 |
| --- | --- |
| 0.0 | 13 / 6 / 0.681 |
| 0.08 | 23 / 5 / 0.670 |

On drop15 with appearance, 0.08 gives 21 / 3 / 0.750 against 14 / 4 / 0.836 at margin 0.

Conclusion: on this clip, rejection trades fragments for slightly fewer mixed IDs. The difference is
1 mixed ID, which is within noise. Every margin setting improves on the baseline.

Outdoor `outdoor_stationary_12s` (72 sampled frames, 545 detections; no ground truth, so only total IDs,
lower is not proven better):

| Scenario | Baseline IDs | Continuity + uniform IDs |
| --- | --- | --- |
| full | 29 | 23 |
| stride10 | 50 | 21 |
| drop15 | 33 | 19 |

Teams, indoor:
* With ground-truth identities as keys (an oracle, to isolate colour grouping), the three red/orange-bib
  players became **Team A**. The lime, blue and black shirts on the other side stayed **unknown**
  (`one_dominant_colour_group_only`).
* On tracked IDs, the same three bib players were Team A for 92 of 93 matched observations, and every
  other player was unknown.
* The ground truth has no team labels. The partition was checked visually in `team_crops.jpg` (torso
  crops only). It is correct, and no player received the wrong team.

Non-players, indoor tracks:
* 1 `likely_non_player`: a seated spectator at the image edge.
* 2 `review_required`: a standing spectator and a far seated person.
* All three are 0% ground-truth matched. **No ground-truth player was flagged.**
* 4 short non-ground-truth tracks stayed `unknown`.

Merge proposals, indoor (with appearance):
* Only 3 proposals: 1 ambiguous but correct, and 2 involving spectator tracks that cannot be verified.
* That is too few to estimate precision.

CPU cost on an Apple M5, about 8.8 detections per frame:

| Step | ms/frame |
| --- | --- |
| `ContinuityTracker` | 0.72 (baseline 0.09) |
| `torso_colour_feature` | 0.37 |

Both are negligible next to about 565 ms/frame of inference (1.77 FPS measured earlier).

## Tests run

```sh
.venv/bin/python -m pytest -q backend/tests/test_tracking_continuity.py backend/tests/test_teams.py \
  backend/tests/test_identity.py backend/tests/test_highlights.py \
  backend/tests/test_local.py::test_tracker_reorder_and_expiration     # 38 passed
.venv/bin/ruff check <owned files>; .venv/bin/ruff format --check <owned files>   # clean
```

The tests cover:
* adversarial exact-overlap crossings, with and without appearance (no ID may cover both people);
* players who turn back, where motion prediction alone is wrong;
* permuted detection order;
* a 2-update occlusion with recovery, and a long gap that starts a new track;
* same-colour ambiguous return and same-colour merge ambiguity;
* contaminated descriptors from overlapping boxes, and time/input validation;
* head-pixel invariance and kit-colour/label invariance, plus brightness invariance;
* referee outlier, all-same-colour and mixed-side cases, and the conservative non-player rules.

The full `test_local.py` API/pipeline suite was not run. It loads the pose model, and the task was to
avoid GPU workloads while the other session runs.

## Limitations

* The evaluation uses **one 8.4 s indoor clip** with 6 annotated players and 34 sampled frames. The
  results are indicative, not established performance. Mixed-ID differences of 1–2 are noise. Session
  splits and more sequences are needed before tuning further. The TrackID3x3 indoor video for other
  sequences has not been downloaded.
* `stride10` and `drop15` are synthetic perturbations of the same detections.
* The existing `PlayerIdentityTracker` loses part of the tracker's continuity: 1:1 recall falls from
  0.841 on `track_id` to 0.670 on `player_id`. Its own margin/appearance holds start new players even
  when the raw track continues. It was left unchanged, as required. The identity owner should re-tune
  it against this report.
* Exact overlaps cannot be resolved by appearance (the descriptors are contaminated), so the tracker
  fragments there. Reconnection is left to the identity layer and to reviewable merge proposals.
* Team grouping needs a cohesive kit. Pickup or bib-versus-mixed games leave the mixed side `unknown`.
  Dark-clothed spectators must be excluded first. Thresholds are in colour units tuned on one clip.
  There is no lighting model beyond median CIELAB with half-weighted lightness.
* Non-player flags rely on image-space cues: stationarity over at least 3 s, seated aspect, frame edge,
  and an optional supplied court polygon. There is no calibration or ground plane. A player standing
  still for a long time at the frame edge could get `review_required`. They would never be removed, and
  the posture rules can't distinguish referees or coaches.
* Merge proposals have too little evidence to estimate precision. They are for review only.
* Nothing here identifies people. There is no face, jersey-number or name inference, and every output
  carries `identity_confirmed=False` or anonymous labels.

## Next integration step

1. The integration lead accepts or adjusts the new optional fields (`team_label`, `role_status`,
   `merge_proposals`) in `docs/API_CONTRACT.md`.
2. The Video owner applies the pipeline snippet and reruns `data/first_run`. The evaluator can then score
   fresh `tracks.json` directly with `--tracks`.
3. The identity owner re-tunes the `PlayerIdentityTracker` margins using `player_id` metrics in the
   report.
4. The frontend shows `unknown` and `review_required` as review states, never as facts.
