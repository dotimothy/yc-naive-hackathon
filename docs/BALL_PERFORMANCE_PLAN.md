# Ball detector view-cost experiment (EXPERIMENTAL, not integrated)

This experiment is archived in `develop` for reproducibility. Its code is not
wired into production analysis; RF-DETR and the existing three-view policy remain active.

## Verdict: FAIL — do not promote yet

No cheaper view strategy met the strict non-regression gate on both authorized clips. The best candidate, `select` (full frame plus the production tile(s) containing the stationary hoop and last observed ball), trades detector time against a few observations:

| Clip | Detector time vs current | Ball result vs current | Hoop result vs current | Gate |
| --- | --- | --- | --- | --- |
| Outdoor 12 s (tuning clip) | **−20%** (49.15 → 39.20 s; median paired per-frame ratio 0.72) | identical: 108/180 frames, 110 observations, 13 segments, longest no-ball run 13 frames (934 ms), 0 lost, 0 added | 1 stable hoop, but per-frame hoop observations 170/180 (vs 180); median hoop confidence 0.485 (vs 0.571); stable-region IoU 0.915 | fails per-frame hoop only |
| Indoor 8.4 s (held out, untuned) | **−16%** (21.63 → 18.06 s; ratio 0.77) | 55/84 frames (vs 56); 3 of 63 reference observations lost, all low confidence (0.26–0.34); 17 segments (vs 18); longest no-ball run unchanged at 5 | 84/84 hoop frames; region IoU 0.997 | fails ball preservation |

Lost-ball trade-off: in the held-out clip, `select` misses a ball that the dropped fixed tile alone would have found. That happens when the ball is away from the hoop and away from the ball currently being followed. All three misses were low-confidence (0.26–0.34) detections. On the review sheet (`review_select_0.jpg`), they sit at players' feet or clothing. Whether they are real balls is **unreviewed**, so they are counted as losses.

"Coverage" below is the share of processed frames with at least one thresholded ball observation. It is agreement with the current production views, **not accuracy**: no ground-truth ball annotation exists. No makes, misses or shots are inferred. The `review_proposals` count stayed 0 for every strategy.

## What was built

| File | Purpose |
| --- | --- |
| `workers/video/adaptive_ball_detector.py` | Experimental policies, imported by nothing in production. `AdaptiveCropPolicy` places free crops of production tile size around the hoop and last ball. `TileSelectionPolicy` (`select`) only chooses among the production views `{full, left, right}`. Both use periodic full rediscovery (every 10 processed frames, plus 3 bootstrap frames) and fall back to all three views when there is no hoop consensus or no ball within 200 ms (`reacquire`). `predict_views()` mirrors `BasketballDetector.detect` but keeps each raw observation's view. `AdaptiveBallDetector(detector, policy).detect(frame, ts)` is a drop-in wrapper. Ball positions are never predicted into evidence: a ball outside the chosen views is simply not observed. |
| `scripts/benchmark_ball_views.py` | `preflight`, `diagnose` (CPU), `replay` (CPU, exact for production-view subsets) and `run` (GPU; needs `--execute`). |
| `backend/tests/test_adaptive_ball_detector.py` | 15 CPU-only tests using a fake predictor. They cover view planning, coordinate mapping, thresholds, tile selection, learned hoop tiles, agreement/verdict metrics and the job guard failing closed. These tests check bookkeeping, not recognition. |

`basketball_detector.py`, thresholds, model, dependencies, backend and frontend are unchanged.

## Measured runs (MPS, Apple, native, `PYTORCH_ENABLE_MPS_FALLBACK=0`)

Pinned for every run in `manifest.json`:

- Model: E-BARD RF-DETR Nano `aae99b56…`, checkpoint SHA-256 `759968ec…2400`, sanitized state `77643ba4…`, resolution 704.
- Thresholds: ball 0.25, hoop 0.35, player/referee 0.5; suppression IoU 0.5; tile width 0.6; ball gap 200 ms.
- Runtime: torch 2.8.0, rfdetr 1.3.0. Commit `7604d0d`, plus the three uncommitted experimental files listed above.
- Decode: stride 2, production frame selection and PTS rule. Every strategy saw identical `(frame_index, pts_ms, frame digest)`, and no index-clock fallback was needed.
- Production parity: `BasketballDetector.detect()` equals the mirrored reference on the first 3 frames of every run (`[True, True, True]`).

Timing is paired and warm. Each sampled frame runs every strategy back-to-back, rotating the order per frame, after untimed warm-up of the 1-, 2- and 3-view batch shapes. Only synchronized `predict()` time is counted. Cost is about linear in views: median 0.093 / 0.174 / 0.251 s for 1 / 2 / 3 views (indoor), and 0.185 / 0.268 s for 2 / 3 views (outdoor). So savings scale with views avoided. Model load took 2.8–3.5 s.

### Outdoor `outdoor_stationary_12s.mp4` (SHA-256 `144d758d…8b95`, CC BY 4.0 TrackID3x3), 180 processed frames

| Strategy | Det. s | Paired ratio | Views/frame | Frames w/ ball | Segments | Longest gap (frames) | Ref. obs lost / added | Hoop frames | Stable hoops |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tiled3` (production reference) | 47.27 / 49.15 | 1.00 | 3.00 | 108 | 13 | 13 | 0 / 0 | 180 | 1 |
| `tiled3@repeat` | 47.08 | 1.00 | 3.00 | 108 | 13 | 13 | 0 / 0 (bit-identical) | 180 | 1 |
| `full` | 17.65 | 0.37 | 1.00 | 81 | 16 | 17 | 27 / 0 | **0** | **0** |
| `tiles2` | 32.56 | 0.68 | 2.00 | 99 | 11 | 16 | 10 / 0 | 180 | 1 |
| `adaptive` (free crops + full) | 37.34 | 0.71 | 2.33 | 107 | 13 | 15 | 7 / 6 | 117 | 1 |
| `adaptive:full=0` | 27.59 | 0.38 | 1.68 | 93 | 14 | 13 | 23 / 7 | 114 | 1 |
| `select` | 39.20 | 0.72 | 2.32 | 108 | 13 | 13 | 0 / 0 | 170 | 1 |
| `select:learnhoop=1` | 47.52 | 1.00 | 2.90 | 108 | 13 | 13 | 0 / 0 | 179 | 1 |

Rows `tiled3` (first time) through `adaptive:full=0` are from run `20261108T020834Z`. The `select` rows and the second `tiled3` time are from the paired confirmation run `20261108T021554Z`. Reference ran twice with identical output, so the model is deterministic on MPS for these inputs. `full` alone never found the outdoor hoop, which confirms the earlier smoke result.

### Indoor `basket_S1T1_pre.mp4` (SHA-256 `8af3f89c…aead`, CC BY 4.0 TrackID3x3), 84 processed frames — held out

| Strategy | Det. s | Paired ratio | Views/frame | Frames w/ ball | Segments | Longest gap | Ref. obs lost / added | Hoop frames |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tiled3` | 21.63 | 1.00 | 3.00 | 56 | 18 | 5 | 0 / 0 | 84 |
| `full` | 8.03 | 0.37 | 1.00 | 42 | 13 | 6 | 19 / 0 | 19 |
| `tiles2` | 14.62 | 0.68 | 2.00 | 52 | 11 | 9 | 7 / 0 | 84 |
| `select` | 18.06 | 0.77 | 2.46 | 55 | 17 | 5 | 3 / 0 | 84 |
| `select:learnhoop=1` | 17.05 | 0.70 | 2.32 | 54 | 18 | 5 | 3 / 0 | 84 |

Fewer segments is not automatically better. Here it comes from losing isolated single-observation segments.

## Findings

1. **Free-placed crops change the model's output.** The outdoor hoop was found in only about half of hoop-centred crops, against 166–180/180 in the fixed tiles. All 7 balls lost by `adaptive` lay inside its crop. The reference had found them only in the fixed left tile. Crop placement is not a neutral cost knob, so `TileSelectionPolicy` reuses only the production tile pixels. That makes it replayable exactly, and the GPU run reproduced the CPU replay to the observation.
2. **The full view is needed.** It alone supplies 10 outdoor and 7 indoor kept balls. Dropping it (`full=0`, `tiles2`) loses 9–11% of reference ball observations.
3. **The left and right tiles disagree on the outdoor hoop.** Left found it on 166/180 frames, right on 180/180. Keeping per-frame hoop observations means running the right tile whenever the ball is on the left. That is `learnhoop=1`, which costs about the full 3 views outdoors.
4. **Cheapest no-loss setting is clip-dependent.** Outdoor tuning, done by replay over 10+ parameter sets, chose the defaults (`refresh=10`, `reacquire=1`, `margin=0.05`). Every cheaper outdoor setting lost a ball or moved the stable hoop more (IoU < 0.9). These parameters are tuned in-sample on one clip. The held-out clip already shows losses.

## Commands

```sh
# 1. Guard: exit 1 if any data/*/scoutfilm.sqlite3 (or $SCOUTFILM_DATA) has a
#    queued/running/cancelling job, basketball_job or highlights record,
#    or if a worker/benchmark process or the benchmark lock is active.
.venv/bin/python scripts/benchmark_ball_views.py preflight

# 2. CPU only: recompute metrics on existing production artifacts (no view info available there)
.venv/bin/python scripts/benchmark_ball_views.py diagnose \
  data/local/processed/471f3c31b99342909708f1bc8461aeb9/basketball

# 3. CPU only: exact replay of production-view subsets from a recorded run (time is ESTIMATED)
.venv/bin/python scripts/benchmark_ball_views.py replay \
  data/benchmarks/ball_views/20261108T020834Z-144d758db532 \
  --strategies select select:learnhoop=1 select:reacquire=2 tiles2

# 4. GPU: dry-run plan first (no --execute), then the measured run
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-ball/bin/python scripts/benchmark_ball_views.py run \
  data/trackid3x3/outdoor_stationary_12s.mp4 --device mps \
  --expect-sha256 144d758db53298cadb887bcee996284e569a03e45ed93c43265586933dc08b95 \
  --strategies tiled3 select select:learnhoop=1 --execute
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv-ball/bin/python scripts/benchmark_ball_views.py run \
  data/trackid3x3/basket_S1T1_pre.mp4 --device mps \
  --expect-sha256 8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad \
  --strategies tiled3 full tiles2 select select:learnhoop=1 --execute

# Checks
.venv/bin/python -m pytest -q backend/tests/test_adaptive_ball_detector.py
.venv/bin/ruff check scripts/benchmark_ball_views.py workers/video/adaptive_ball_detector.py backend/tests/test_adaptive_ball_detector.py
```

Strategy specs:

- `tiled3`, `full`, `tiles2`, `sparse:every=N`
- `adaptive[:crop=,bootstrap=,refresh=,reacquire=,full=0|1]`
- `select[:margin=,refresh=,reacquire=,bootstrap=,full=0|1,learnhoop=0|1]`
- `@tag` repeats a spec

The first strategy must be `tiled3`.

GPU safety:

- `run` refuses without `--execute`, requires an explicit `--device`, and refuses while the guard is not clear.
- It takes `data/benchmarks/ball_views/.gpu.lock`.
- It re-reads every job database before **every frame**, and the process list every 15 frames.
- If an analysis appears, it stops after the current inference, writes a summary with `aborted`, and exits 2.
- `--allow-active-jobs` overrides this, and the override is recorded in the manifest.
- The guard checks local job databases and process names only. It cannot see GPU use by unrelated apps.

Outputs go to `data/benchmarks/ball_views/<UTC>-<sha12>/` (ignored):

- `manifest.json`: provenance.
- `<strategy>.jsonl`: one record per frame with index, PTS, decoder PTS, frame digest, views, reason, seconds, raw per-view and kept observations.
- `summary.json`: metrics, agreement, verdicts, derived `full`/`tiles2` from reference raw views.
- `review_<strategy>_N.jpg`: up to 16 LOST/EXTRA rows per strategy. Each row is reference full frame, reference zoom, candidate full frame, candidate zoom, with view rectangles in grey.

Measured evidence:

- `data/benchmarks/ball_views/20261108T020834Z-144d758db532`: outdoor, 7 strategies.
- `data/benchmarks/ball_views/20261108T021554Z-144d758db532`: outdoor, `select` confirmation.
- `data/benchmarks/ball_views/20261108T021824Z-8af3f89ccd35`: indoor held-out.

## Verdict gate (`verdict()` in the script)

PASS requires all of the following against `tiled3` on the same frames:

- lower detector time
- frames with ball ≥ reference
- zero reference ball observations unmatched (centre distance ≤ 0.015 normalized)
- segments ≤ reference
- longest no-ball run ≤ reference
- near-duplicate ball pairs ≤ reference
- same stable-hoop count
- stable-hoop IoU ≥ 0.9
- per-frame hoop observations ≥ reference

`passed_except_per_frame_hoop` is reported separately, because the stationary-hoop consensus does not need a hoop on every frame. Accepting that relaxation, or any ball loss, is a reviewer decision. Candidate-only observations are reported but not penalised. Only the free-crop `adaptive` strategies produced any (6–7 outdoors; one at 0.26 sits on a player's head in `review_adaptive_0.jpg`). Every `select` and fixed-view run had 0.

## Limitations

- Two short clips, one camera each, 264 processed frames in total. Parameters were tuned on the outdoor clip.
- No ball ground truth: lost and added observations are unreviewed. The review sheets support human inspection only.
- MPS timings come from one machine. CPU and CUDA were not measured for this experiment.
- `replay` is exact only for subsets of the recorded production views. Its time is estimated from paired medians.
- Detector time excludes decode, colour conversion and suppression. The end-to-end analysis time saving will be smaller than the detector-time saving.

## Suggested next experiments (not run)

1. **Cross-frame batching (no quality change expected).** Pass 2–4 sampled frames × 3 views to one `predict()`. Batch composition did not change outputs here (`tiles2` run = `tiles2` derived from reference). Measure throughput only; the gate should pass trivially.
2. **`select` plus a low-confidence guard.** Run both tiles whenever the previous frame had a second or low-confidence ball candidate. This targets the 3 indoor losses, which were all ≤ 0.34. Validate on a third clip, not the two above.
3. **Annotate ball presence** for these two clips: a few hundred frames, image-level, with the reviewer's own label set. Then decide whether the 0.26–0.34 detections `select` drops are balls. Only then can a cost/recall trade-off be stated.
4. If the per-frame hoop requirement is relaxed, `select` is the candidate to re-test. If not, the view-selection approach saves at most about 3–5% on these clips, and effort is better spent on (1).
