# Identity review policy: handoff

Stream: Video (identity). Owned files: `workers/video/identity.py`, `backend/tests/test_identity.py`,
this doc. Nothing committed. Builds on the tracking follow-up `0cbde6f`. Experimental
`continuity=False` stays the default.

## Verdict

**Automatic cross-track identity reconnection should not stay enabled for production.**

On the fixed TrackID3x3 clips with the production pairing (`ContinuityTracker` + v1 identity), the
identity layer made 36 automatic reconnections:
* 23 joined fragments of the same annotated player;
* **4 joined two different players** (3 dev, 1 held-out);
* 9 cannot be checked, because they involve people the dataset does not annotate (spectators, staff).

So about 1 in 7 checkable merges was wrong. One wrong merge puts another person's actions on a
player's clips and stat line. With the experimental v2 identity, reconnection is the *only* source of
contamination above the raw tracker: held-out contamination is 76 with automatic reconnection and 66
(exactly the raw-track level) without it.

`PlayerIdentityTracker` therefore now **withholds** cross-track links by default
(`reconnection="review"`) and records them as review suggestions. The legacy behaviour stays available
as `reconnection="automatic"`, which reproduces the existing comparator exactly.

The cost is more fragments, which is acceptable because preventing wrong merges matters more than
having fewer IDs.

## Interface

```python
PlayerIdentityTracker(game_id, short_gap_ms=2000, margin=0.12, continuity=False,
                      split_distance=0.6, reconnection="review")   # or "automatic"
tracker.update(track_ids, boxes, descriptors, timestamp_ms)      # unchanged fields
tracker.reconnection_suggestions()                               # call after the last frame
tracker.summary()  # adds reconnection_policy, withheld_reconnections
```

* An invalid `reconnection` value raises `ValueError`.
* **`"review"` (default).** A player can only continue on the raw track it already owns, within
  `short_gap_ms`. These links are never applied automatically:
  * a link to a *different* raw track;
  * a raw ID that reappears after more than `short_gap_ms` (a reused or restarted ID).

  The fragment gets a new provisional player instead, with status `ambiguous`, the plausible earlier
  players in `identity_candidates`, and `association_confidence=None`. No new status value was added;
  `ambiguous` already sends the automatic player group to `needs_review` in `highlights.py`.
* Withheld links still count as rivals in the ambiguity test. A same-track continuation that has a
  near-equal cross-track rival is held, which is more conservative than legacy.
* **`"automatic"`.** This is the old behaviour, status `reidentified`. Use it for evaluator
  reproduction only, never for attributing stats or clips.

`reconnection_suggestions()` returns one item per withheld fragment. This is the actual output of the test scenario
`test_default_withholds_occlusion_reconnection_and_suggests_it`:

```json
{"track_id": "8", "raw_track_id": "8", "provisional_player_id": "game-player-2",
 "timestamp_ms": 1250, "status": "needs_review", "method": "withheld-reconnection-v1",
 "identity_confirmed": false, "requires_review": true,
 "candidates": [{"player_id": "game-player-1", "player_label": "Player 1", "score": 0.0036,
                 "gap_ms": 750, "last_track_id": "1", "track_ids": ["1"]}],
 "excluded_candidates": []}
```

* Candidates are ranked by cost. `score` is a cost, not a probability.
* A candidate seen at the same source time as the fragment (it is a different person) moves to
  `excluded_candidates` with `reason: "visible_at_same_time"`. A suggestion with no candidates left is
  dropped.
* If a reviewer accepts a link, the new reviewer group is the candidate's `track_ids` plus the
  fragment's `track_id`, sent through the existing `PUT /api/games/{id}/highlights/players` with
  `confirmed: true`. That route already rejects groups with fragments visible at the same time.
  Nothing is ever applied automatically.

## Validation (frozen settings, no tuning on held-out)

Setup:
* Cached detections in `data/claude_tracking/detections/` (keypoint R-CNN COCO_V1, score ≥ 0.75,
  stride 5).
* Splits and alignment from `docs/TRACKING_HANDOFF.md`: dev = S1T1 and S2T6; held-out = S3T4, S4T2
  and S6T2.
* Tracker and identity defaults unchanged. The only variable is `reconnection`.
* Run on CPU, Apple arm64, Python 3.9.6.

### Reproducibility

`reconnection="automatic"` reproduces `data/claude_tracking/eval/report.json` exactly: every
aggregate, every per-sequence variant, and the team/role/merge sections. This was checked twice:
* by forcing the legacy default (`data/claude_tracking/eval_identity_legacy/`);
* with the main agent's updated `scripts/evaluate_tracking.py`
  (`data/claude_tracking/eval_identity_integrated/`). All 8 historical aggregate rows are identical.

### Final `player_id`, ContinuityTracker + v1 identity: automatic → review

| Split | Switches | Fragments | Mixed IDs | Contaminated obs | Purity | 1:1 recall | Players |
| --- | --- | --- | --- | --- | --- | --- | --- |
| dev | 81 → 90 | 66 → 83 | 8 → 6 | 10 → 8 | .978 → .983 | .596 → .548 | 92 → 115 |
| **held-out** | 103 → 108 | 96 → 102 | 9 → 8 | **14 → 12** | .983 → **.986** | .582 → .589 | 143 → 153 |

Per held-out sequence (automatic → review):

| Sequence | Switches | Fragments | Mixed | Contaminated | Purity |
| --- | --- | --- | --- | --- | --- |
| S3T4 | 42 → 45 | 39 → 44 | 3 → 2 | 3 → 2 | .989 → .993 |
| S4T2 | 33 → 31 | 31 → 28 | 1 → 1 | 2 → 2 | .992 → .992 |
| S6T2 | 28 → 32 | 26 → 30 | 5 → 5 | 9 → 8 | .971 → .974 |

No sequence gets more contamination or more mixed IDs under the review policy.

### Experimental v2 (`continuity=True`, ContinuityTracker): automatic → review

| Split | Switches | Fragments | Mixed | Contaminated | Purity | 1:1 |
| --- | --- | --- | --- | --- | --- | --- |
| dev | 43 → 47 | 29 → 36 | 11 → 9 | 14 → 13 | .970 → .972 | .828 → .767 |
| held-out | 56 → 59 | 45 → 50 | 12 → 11 | 76 → 66 | .908 → .920 | .705 → .681 |

Under review, held-out v2 player IDs equal the raw tracks (66 contaminated, the raw-tracker level). v2
is still unsafe: its contamination comes from raw-tracker swaps, not from reconnection.

### Reconnection audit (automatic, ContinuityTracker + v1)

| Split | Reconnections | Same player | **Different players** | Not verifiable |
| --- | --- | --- | --- | --- |
| dev | 22 | 16 | **3** | 3 |
| held-out | 14 | 7 | **1** | 6 |

"Not verifiable" means the fragment or its earlier owner is not an annotated player.

### Review-queue quality (review policy, top-ranked candidate vs ground truth)

| Split | Suggestions | Top candidate correct | Top candidate wrong | Not verifiable | With alternatives |
| --- | --- | --- | --- | --- | --- |
| dev | 61 | 37 | 14 | 10 | 38 |
| held-out | 64 | 45 | **9** | 10 | 40 |

The top candidate is wrong for 1 in 6 checkable held-out fragments. Suggestions must stay a choice for
the reviewer, never a default acceptance.

### Commands

```sh
.venv/bin/python -m pytest -q backend/tests/test_identity.py                     # 19 passed
.venv/bin/python -m pytest -q backend/tests          # 94 passed, 3 failed (see request 1)
.venv/bin/ruff check workers/video/identity.py backend/tests/test_identity.py    # clean
.venv/bin/ruff format --check workers/video/identity.py backend/tests/test_identity.py  # clean
.venv/bin/python scripts/evaluate_tracking.py --out data/claude_tracking/eval_identity_integrated
```

The reconnection audit and the suggestion-quality tables came from a throwaway script that is not in
the repo. It reuses `evaluate_tracking` loaders, runs each policy, and labels each `reidentified`
observation and each suggestion's top candidate against the MOT majority label. The integration lead
can add it to the evaluator if wanted.

## Tests added or changed (`backend/tests/test_identity.py`)

The three legacy reconnection tests now pass `reconnection="automatic"` and are named `test_legacy_*`.

New tests:
* The default withholds an occlusion reconnection and returns it as a suggestion (candidates, gap,
  tracklets, summary counters).
* The default never reconnects after a long absence.
* **Same-kit crossing fragments are never merged** into either earlier identity (v1 and v2).
* A same-kit fragment next to a visible teammate is not merged, while the teammate's own raw track
  continues.
* **A reused raw ID after a long gap does not mix players.** The test also shows that legacy mode
  would merge it on colour alone.
* A raw ID that teleports starts a new player (`1~2`).
* A duplicate raw ID in one frame never shares a player.
* Same-track continuation is identical under both policies.
* A suggestion drops a candidate that is visible at the same time as the fragment.
* Suggestions are copies and are never applied.
* An unknown policy value is rejected.

## Requests for the main agent (files I do not own)

1. **`backend/tests/test_tracking_continuity.py`.** Three tests assert legacy reconnection and fail
   under the new default:
   * `test_player_is_never_assigned_to_two_raw_tracks_in_one_frame[False]` and `[True]`;
   * `test_true_return_after_long_absence_needs_unique_distinct_kit`.

   Verified fix (25/25 pass on a copy):
   * in `run_identity`, add `options.setdefault("reconnection", "automatic")`;
   * in the two-raw-tracks test, construct with `reconnection="automatic"`.
2. **Already done by the main agent, verified here:** the evaluator keeps historical variants on
   `"automatic"` and adds `continuity_tracker+review_fragments`; the pipeline passes
   `reconnection="review"` and reports `identity_merge_suggestions`. That report key and its item
   shape (above) belong in the API contract and `frontend/src/types.ts` before the UI reads it.
3. The UI should show `identity_merge_suggestions` as a reviewer choice: candidates, excluded
   candidates and evidence, with no pre-selected answer. Accepting a suggestion goes through the
   existing confirmed-group route.

## Limitations

* **Contamination is not zero.** 12 held-out observations (purity 0.986) remain. They are swaps
  *inside* one raw track that the v1 appearance gate did not catch. The identity layer cannot remove
  them without a stronger appearance signal. Stats and clips still need reviewer-confirmed groups.
* Fragmentation rises (held-out 96 → 102 fragments, 153 provisional players over about 41 s of
  footage). Reviewer workload goes up: 64 held-out suggestions.
* The evidence is five clips from one indoor venue (2 dev, 3 held-out). The wrong-merge counts are
  small (4 of 27 checkable), so the rate is uncertain. It is enough to reject unattended merging, not
  to estimate a production error rate.
* The descriptor is an HSV uniform histogram. It barely separates people in the same kit, so a
  same-kit return can never be reconnected automatically under either policy.
* The new eval reports under `data/claude_tracking/eval_identity_*` are ignored by Git and only on
  this machine.

## Next integration step

Apply request 1. Then expose the review queue (request 3) before any per-player stat or clip
attribution relies on automatic groups. Validate any future stronger descriptor on dev first, then on
new held-out sessions (for example S5), not on S3/S4/S6 again.

## Integration verification

The integration lead completed the requested legacy-test updates, explicit evaluator policies, review-suggestion UI and source-time split correction. The final backend suite passes97 tests; the production frontend build and Ruff checks pass. A real browser check confirmed corrected IDs survive reload and historical SSE replay. The frozen five-clip evaluation was independently reproduced at `data/claude_tracking/playlattice_identity_gate/report.json` (ignored local artifact). New validation for future tuning must use previously unused sessions.
