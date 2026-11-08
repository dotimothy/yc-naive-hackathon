# Tracking continuity, identity association, teams and roles: handoff

Stream: Video (tracking). Branch `work/claude-tracking`.

| Round | Commit | Contents |
| --- | --- | --- |
| Round 1 | `30b11b3` | `ContinuityTracker`, `teams.py`, merge proposals |
| Round 2 | this doc's commit | `PlayerIdentityTracker` continuity mode, multi-clip validation, team/role safety tests |

Nothing is wired into `pipeline.py`, the API, the database or the frontend. The snippets below are
proposals for the stream owners and the integration lead.

## Verdict

**Acceptance is met only by the conservative configuration.** That is `ContinuityTracker` followed by
the *original* `PlayerIdentityTracker` (`continuity=False`, the default). On the fixed held-out clips,
compared with the current pipeline:

| Held-out final player IDs | Baseline | Recommended |
| --- | --- | --- |
| Switches | 144 | 103 |
| Fragments | 136 | 96 |
| Contaminated observations | 18 | 14 |
| Observation-weighted purity | 0.978 | 0.983 |

No held-out sequence has more contamination than baseline. **The gain is modest.** Final players still
fragment a lot (about 34 switches per 10 s clip), so this configuration is **not adequate for unattended
player scoring or automatic highlight attribution**. Review gates must stay.

**The new continuity-first identity mode (`continuity=True`, "v2") is experimental and off by default.**
It met the goal on the development clips. On held-out clips it cut final-player switches from 144 to 56
and raised one-to-one recall from 0.543 to 0.705. But it **raised contaminated observations from 18 to
76** (purity 0.978 → 0.908). It trusts raw tracks, and on held-out footage the raw tracker's identity
swaps (66 contaminated raw observations) pass straight through. The v1 identity layer's strict
appearance gate removes most of those swaps, at the cost of fragmenting.

This is the trade-off: more continuity in final player IDs currently costs identity purity. I did not
tune anything after seeing held-out results.

## Changed files (round 2)

| File | Change |
| --- | --- |
| `workers/video/identity.py` | `PlayerIdentityTracker` gains `continuity=False` and `split_distance=0.6`, plus a `continuity_splits` count in `summary()`. Same call signature and output fields. The default path is behaviourally identical to v1 (all original tests pass; evaluator numbers for v1 variants are unchanged). |
| `scripts/cache_tracking_detections.py` (new) | Downloads the fixed clips, records and verifies SHA-256, and caches pretrained pose detections serially under a lock. |
| `scripts/evaluate_tracking.py` (rewritten) | Multi-sequence suite with fixed splits, fixed alignment, zero-detection frames, raw vs final ID metrics, purity, team/role proxies, per-sequence and aggregate results. |
| `backend/tests/test_tracking_continuity.py`, `backend/tests/test_teams.py` | 21 new tests (44 targeted in total; see Tests). |

`workers/video/tracker.py` and `workers/video/teams.py` are unchanged since `30b11b3`.

### Experimental identity v2 (`PlayerIdentityTracker(game, continuity=True)`)

* A raw track that keeps feeding the same provisional player stays with it, with status `continued`.
  There are only two pieces of contrary evidence:
  * a strong uniform contradiction, cosine distance ≥ 0.6;
  * an implausible jump, more than 1 + 2·gap(s) box heights.
* The 0.6 threshold comes from development footage. Same-person uniform distances reached 0.49
  (95th percentile 0.34), and the median distance between different people was 0.345. The descriptor is
  too weak for positive identity, so colour similarity never *creates* a continuation.
* New or fragmented raw tracks use the v1 re-identification rules. Ties are held as `ambiguous` with
  `association_confidence=None`. A player already continued in the frame is never a candidate for
  another detection.
* `association_confidence` is `None` when no motion/appearance cost exists (for example after a
  turn-back) rather than invented. All outputs keep `identity_confirmed=False` and
  `identity_source="automatic"`.

## Fixed validation set

Split rule, fixed before any results:
* S1T1 is the original development clip.
* Every other session contributes its **longest annotated sequence**, with ties going to the lower
  trial number.
* S2 is development; S3, S4 and S6 are held out.

The first pick, "first trial of each session", was replaced before any scoring, because S2T1/S3T1 have
only 30 and 62 annotated frames.

| Sequence | Split | Video SHA-256 | MOT SHA-256 | Source |
| --- | --- | --- | --- | --- |
| basket_S1T1_pre | dev | `8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad` | `7432a4697a9de24f…` | drive file `1ONVOjVBPmNdpQWQ_9WJCNYte__4NJ5Ol` |
| basket_S2T6_pre | dev | `1379e11ae6c7c4a2ddca8ad74ee502c6ff2881630b7c5e57583a73e3fe4c6b9a` | `fc4c11a96b312e5e…` | drive file `1A_mD5nadl4603dE_R9zeqYfEBNMBw28I` |
| basket_S3T4_pre | held-out | `2ca9223c69cfd3421aa14dd513aea0268a8017c6c441f1a5a416943d33b922e8` | `032d8d522a5b8c61…` | drive file `1sRF-LZZG9VbPIh7SOTXearyrrzA9B-eg` |
| basket_S4T2_post | held-out | `fe1a7795dc434bd2382a581e5c30422a6bd567224324147707c7536cea87cd77` | `8c289255f3b7b640…` | drive file `12VCupNldXtXU1pYMtOK_eC6c7-2LUKrw` |
| basket_S6T2_post | held-out | `853cd2c8ca1834ce1297281d9f21d90b9ae4a1aa60b8901f4b5380e716537290` | `7e46f19c1a5f47c5…` | drive file `1BV9izBdD3I1EySL68JCXbaBj_cIAkftB` |

Source details:
* Videos come from the public TrackID3x3 Indoor folder
  (`https://drive.google.com/drive/folders/1q0L_Mmf5cwb-Yv51NTjgQcF12Is0fYCB`).
* Annotations are the pinned upstream MOT files (repo `9822a3dd`).
* Dataset: TrackID3x3, Yamada et al., **CC BY 4.0**. The only transformations are frame sampling and
  model inference.

Data locations:
* Full hashes are in `data/claude_tracking/sources/manifest.json`.
* Media is in `data/claude_tracking/media/` and detections in `data/claude_tracking/detections/`.
* All of it is ignored by Git and nothing is committed.
* The S1T1 hash equals the existing pinned sample.

Detections:
* Model: torchvision keypoint R-CNN, COCO_V1, pretrained (no training), the same settings as the
  pipeline.
* Score ≥ 0.75, every 5th frame.
* Run on CPU, serialized, at about 3.2 inference FPS per clip on an Apple M5.
* The re-cached S1T1 gives the same 300 detections as `data/first_run`.

Fixed parameters for every variant:
* `ContinuityTracker()` defaults from `30b11b3`: margin 0.08, max_lost_ms 1500, appearance gate 0.35.
* `PlayerIdentityTracker` defaults, plus `continuity`/`split_distance=0.6` for v2.
* IoU 0.5; MOT frame = source index + 1.

## Commands

```sh
# 1. download (public Drive), hash and cache detections; serialized; ~80 s CPU for 5 clips
.venv/bin/python scripts/cache_tracking_detections.py --device cpu
# 2. evaluate all variants on dev + held-out (CPU, ~10 s)
.venv/bin/python scripts/evaluate_tracking.py            # -> data/claude_tracking/eval/report.json
.venv/bin/python scripts/evaluate_tracking.py --split dev --out data/claude_tracking/eval_dev
# single legacy pipeline output (frames with zero detections unknown there)
.venv/bin/python scripts/evaluate_tracking.py --tracks data/first_run/tracks.json \
  --report data/first_run/report.json --video data/trackid3x3/basket_S1T1_pre.mp4 \
  --gt data/trackid3x3/ground_truth/Indoor/MOT/basket_S1T1_pre.txt --out data/claude_tracking/eval_single_check
```

The evaluator also writes `data/claude_tracking/eval/<sequence>_team_crops.jpg`, which shows torso-band
crops only.

## Metric protocol

* **Identical detections and alignment for every variant.** The MOT frame is the source index + 1
  (1-based MOT). That offset is fixed. An offset scan (−2…+3) is reported as a diagnostic: +1 was the
  best or within 2 matched boxes of the best on every clip, and it was never chosen from scores.
* **Detector coverage** is reported separately. Sampled frames with zero detections are kept in the
  denominator; none of these clips had any.
* Detections are matched to ground truth with Hungarian assignment on IoU ≥ 0.5, ignoring predicted IDs.
* Every ID metric is computed for both the raw `track_id` and the final `player_id`:
  * **coverage**: matched ground-truth observations / all ground-truth boxes;
  * **switches**: consecutive matched observations of a player carrying different IDs;
  * **fragments**: Σ(distinct IDs per player − 1);
  * **mixed IDs**: IDs covering two or more players;
  * **contaminated observations** and **purity**: observations not belonging to their ID's majority
    player, weighted by observation count;
  * **one-to-one ID recall**: the best one-to-one player↔ID mapping / matched observations.
* These are **custom metrics, not IDF1 or HOTA**; no standard evaluator was run.

## Results

Detector (identical for all variants). Precision counts the non-annotated people (spectators, bench,
staff) as misses:

| Sequence | Split | Sampled frames | Zero-det frames | GT boxes | Detections | Recall | Precision vs annotated |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1T1 | dev | 34 | 0 | 204 | 300 | 0.892 | 0.607 |
| S2T6 | dev | 54 | 0 | 324 | 469 | 0.858 | 0.593 |
| S3T4 | held-out | 56 | 0 | 336 | 439 | 0.827 | 0.633 |
| S4T2 | held-out | 48 | 0 | 288 | 337 | 0.840 | 0.718 |
| S6T2 | held-out | 60 | 0 | 360 | 414 | 0.856 | 0.744 |

Final `player_id` per sequence. Each cell is baseline → recommended (continuity + v1) → experimental
(continuity + v2):

| Sequence | Split | Switches | Fragments | Mixed IDs | Contaminated obs | Purity | 1:1 recall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1T1 | dev | 32 → 19 → 12 | 29 → 15 → 7 | 3 → 2 → 2 | 4 → 3 → 3 | .978 → .984 → .984 | .582 → .670 → .874 |
| S2T6 | dev | 62 → 62 → 31 | 52 → 51 → 22 | 8 → 6 → 9 | 10 → 7 → 11 | .964 → .975 → .960 | .554 → .547 → .799 |
| S3T4 | held-out | 46 → 42 → 19 | 42 → 39 → 16 | 5 → 3 → 5 | 5 → 3 → 24 | .982 → .989 → .914 | .417 → .464 → .647 |
| S4T2 | held-out | 50 → 33 → 17 | 47 → 31 → 14 | 3 → 1 → 2 | 3 → 2 → 26 | .988 → .992 → .893 | .512 → .566 → .686 |
| S6T2 | held-out | 48 → 28 → 20 | 47 → 26 → 15 | 9 → 5 → 5 | 10 → 9 → 26 | .968 → .971 → .916 | .682 → .701 → .773 |

Aggregates, raw `track_id` and final `player_id`:

| Split | Variant | ID | Switches | Fragments | Mixed | Contaminated | Purity | 1:1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dev | baseline | track | 107 | 101 | 19 | 42 | .909 | .404 |
| dev | baseline | **player** | 94 | 81 | 11 | 14 | .970 | .565 |
| dev | continuity + v1 | track | 47 | 35 | 10 | 14 | .970 | .780 |
| dev | continuity + v1 | **player** | 81 | 66 | 8 | 10 | .978 | .596 |
| dev | continuity + v2 | **player** | 43 | 29 | 11 | 14 | .970 | .828 |
| held-out | baseline | track | 221 | 205 | 49 | 82 | .901 | .368 |
| held-out | baseline | **player** | 144 | 136 | 17 | 18 | .978 | .543 |
| held-out | continuity + v1 | track | 59 | 50 | 11 | 66 | .920 | .681 |
| held-out | continuity + v1 | **player** | **103** | **96** | **9** | **14** | **.983** | .582 |
| held-out | continuity + v2 | **player** | 56 | 45 | 12 | 76 | .908 | .705 |
| held-out | IoU + v2 (unsafe) | **player** | 138 | 122 | 40 | 130 | .843 | .512 |

Matched-observation coverage equals detector recall for every variant: dev 0.871, held-out 0.842.

Observations:
* The recommended pairing improves on baseline on every held-out clip for switches, fragments and
  contamination.
* On dev S2T6 the recommended pairing does not reduce switches (62 → 62).
* v1 identity still loses about 44 switches of raw-tracker continuity (held-out raw 59 → final 103).
  That is the price of its purity filtering.
* v2 must never be paired with the IoU tracker.

### Teams (continuity + v2 tracklet keys, flagged tracks excluded)

TrackID3x3 has no team labels. I checked the sides visually on torso crops: in all five clips the
red/orange-bib side is ground-truth players 1, 3 and 5. These clips only; no general claim.

| Split | Annotated-player observations | Labelled (Team A/B) | Assigned fraction | Labelled obs on the wrong side |
| --- | --- | --- | --- | --- |
| dev | 460 | 178 | 0.387 | 3 |
| held-out | 828 | 520 | 0.628 | 20 (13 of them S4T2, a blue-shirt player on a tracklet contaminated by a bib player) |

* In 4 of 5 clips only the bib side was labelled (`one_dominant_colour_group_only`). The mixed-colour
  side stays `unknown` by design, so **this is not full team identification**; at best half of the
  players get a side.
* S6T2 labelled both sides. Team B (blue plus black/white) is correct, but only because the
  colour-cohesion threshold is loose. The lime player stayed unknown.
* Team mistakes come mostly from contaminated tracklets, not from colour clustering.

### Roles (non-player flags)

* Across all 5 clips, every `likely_non_player` tracklet (7) was a spectator or other non-annotated
  person. **No annotated player was flagged `likely_non_player`.**
* One annotated-player tracklet in S6T2 (12 observations) was `review_required`. It is a review state:
  nothing is deleted or excluded from tracking, only from team-colour fitting.
* New tests confirm the safe behaviour:
  * a stationary on-court defender gets `review_required` (signal `stationary` only);
  * an active player at the frame edge gets `review_required` (`frame_edge` only);
  * the input samples are never modified.

### Merge proposals (review only)

| Split | Correct | Incorrect | Not verifiable (non-annotated people) |
| --- | --- | --- | --- |
| dev | 11 | 3 | 9 |
| held-out | 14 | 3 | 7 |

Of the 6 incorrect proposals, 5 were already marked `ambiguous` and 1 (dev S2T6) was `proposed`. They remain review suggestions only.

## Proposed pipeline integration (not applied)

```python
from workers.video.tracker import ContinuityTracker
from workers.video.identity import PlayerIdentityTracker, propose_tracklet_merges, uniform_descriptor
from workers.video.teams import TeamGrouper, flag_non_players, torso_colour_feature

tracker, samples, processed = ContinuityTracker(), [], 0       # was Tracker()
identities = PlayerIdentityTracker(game)                        # keep v1 (continuity=False)
teams = TeamGrouper()
...
boxes = result["boxes"][keep].numpy()
keypoints = result["keypoints"][keep].numpy()
descriptors = [uniform_descriptor(frame, box) for box in boxes]
ids = tracker.update(boxes, descriptors, timestamp_ms=timestamp)
associations = identities.update(ids, boxes, descriptors, timestamp)
for j, box in enumerate(boxes):
    teams.observe(associations[j]["track_id"], torso_colour_feature(frame, box, keypoints[j]), timestamp)
...
aspect = metadata["width"] / metadata["height"]
court = options.get("court_polygon")
first = flag_non_players(samples, aspect=aspect, court_polygon=court)
team_map, team_summary = teams.assign(exclude=[k for k, v in first.items() if v["role_status"] != "unknown"])
roles = flag_non_players(samples, aspect=aspect, court_polygon=court, team_assignments=team_map)
merge_proposals = propose_tracklet_merges(samples, teams=team_map, aspect=aspect)
for s in samples:
    s["team_label"] = team_map.get(s["track_id"], {}).get("team", "unknown")
    s["role_status"] = roles.get(s["track_id"], {}).get("role_status", "unknown")
```

* Only flip to `PlayerIdentityTracker(game, continuity=True)` for experiments. **Never** pair it with
  the IoU `Tracker`: contamination rose to 130 held-out observations.
* `team_label`, `role_status` and merge proposals are new contract fields. The integration lead must
  add them to `docs/API_CONTRACT.md`, the backend and `frontend/src/types.ts` first.
* `highlights.build_highlights` groups by `player_id`. With the recommended configuration, player
  fragments remain frequent, so per-player highlight attribution must stay reviewer-confirmed.

## Tests

```sh
.venv/bin/python -m pytest -q backend/tests/test_tracking_continuity.py backend/tests/test_teams.py \
  backend/tests/test_identity.py      # 44 passed
.venv/bin/python -m pytest -q backend/tests   # full backend suite: 58 passed
.venv/bin/ruff check / ruff format --check on owned files   # clean
```

New identity tests, which use v2 unless stated:
* a continued raw track survives colour noise up to 0.45 and a turn-back (and v1 is shown to split);
* a reused raw ID with a strongly different kit gets a new player (`1~2`);
* a teleport on a raw track splits;
* an occlusion bridged by the raw tracker keeps the player;
* players follow reordered detections;
* same-colour crossing fragments get `new`/`ambiguous`, are never forced into an existing identity, and
  carry no confidence;
* a player is never on two raw tracks in one frame (v1 and v2);
* a true return after a long absence needs a unique distinct kit; a same-kit return stays ambiguous;
* v2 is opt-in and the default reports the v1 method.

## Known failures and limitations

* **v2 identity raises contamination on held-out clips** (see the verdict above). The cause is raw-track
  swaps inside `ContinuityTracker` (66 contaminated raw observations on held-out vs 14 on dev). A
  weak-match cost cap (`max_cost`) was tried on dev only. It increased final contamination (to 19–29
  dev observations), so it was not kept.
* **The recommended configuration still fragments heavily**: 103 switches over about 41 s of held-out
  footage. Dev S2T6 shows no switch reduction.
* The uniform descriptor (HSV histogram) barely separates people in matching kits. Re-identification
  was wrong on 2 of 10 dev cases.
* Five clips is little evidence: 2 dev, 3 held-out, one indoor venue and pickup-style bib play.
  Distance from the camera, other venues and broadcast footage are untested. The outdoor clip has no
  ground truth.
* Team labels cover only cohesive kits. Team mistakes follow tracklet contamination. The side ground
  truth used here is my visual check, not an annotation.
* No IDF1/HOTA. The custom metric names are deliberate.

## Next steps

1. The integration lead decides on the recommended configuration and the new contract fields.
2. To make v2 safe, reduce raw-tracker swaps first. Possible routes are a stronger appearance
   descriptor (pose-aligned torso colour, or a licensed re-ID embedding) and keeping the v1 split gate
   only where the raw tracker reported a contested or recovered association. Re-tune on dev clips, then
   add *new* held-out sessions (S5, unused trials) rather than reusing S3/S4/S6 as tuning data.
3. Keep reviewer confirmation for any player-level highlight attribution.
