# Cross-frame ball batching (EXPERIMENTAL, not integrated)

This experiment is archived in `develop` for reproducibility. Its code is not
wired into production analysis; RF-DETR and the existing three-view policy remain active.

## Verdict

| Gate | Result |
| --- | --- |
| **Quality** (outputs identical to production within tolerance) | **PASS**: fb2 and fb4 are *bit-identical* to fb1 on both clips: 0.0 max box and confidence difference, no kept or raw observation added or lost, identical ball, segment, gap, hoop and proposal metrics |
| **Speed** (measurable detector speedup) | **FAIL**: no speedup. Isolated runs: fb2 ×1.022 outdoor / ×0.987 indoor; fb4 ×1.010 outdoor / ×0.912 indoor. That is within or below the ±2–3% drift between two fb1 runs of the same clip |

**Recommendation: do not integrate.** On this Apple M5 (MPS, 16 GB), RF-DETR Nano throughput is already saturated by the production call of 3 images. `predict()` time is linear in image count (0.247 / 0.484 / 0.981 s for 3 / 6 / 12 images). Batching also has costs:

- **Latency:** each frame waits for the rest of its batch (+67–300 ms of source time).
- **GPU memory:** the MPS driver pool grows from 2.2 GB to 3.7 GB (fb2) and 7.3 GB (fb4).
- **Swapping:** fb4 pushed this machine into swap.
- **Out-of-memory:** larger batches swap heavily or fail (see Memory).

No production file was changed.

## What was built

| File | Purpose |
| --- | --- |
| `workers/video/batched_ball_detector.py` | `BatchedBallDetector(detector, frames_per_batch).detect_batch([(frame, ts), …])` runs the same 3 production views (full frame plus two fixed 60% crops) for 1–16 frames in one `predict()` call. It uses the same thresholds and the same per-frame same-class suppression (IoU 0.5). Each frame's observations are mapped with that frame's own size and source timestamp and returned separately. No cross-frame sharing, interpolation or fabricated events. |
| `scripts/benchmark_ball_batches.py` | `preflight`; `run` (paired, or `--single` for one batch size in a fresh process; needs `--execute`); `compare` (CPU-only, for isolated runs). |
| `backend/tests/test_batched_ball_detector.py` | 10 CPU tests using a content-dependent fake model. Batched output equals the **real** `BasketballDetector.detect` code per frame for k = 1/2/4. Also covers: frames stay separate, short final batch, invalid sizes, result-count mismatch, explicit tolerances, the gate failing on a dropped ball, the guard failing closed, out-of-memory detection. |

## Pinned settings (every `manifest.json`)

- Model: E-BARD RF-DETR Nano revision `aae99b56…`, checkpoint SHA-256 `759968ec…2400`, sanitized state `77643ba4…`, resolution 704.
- Thresholds: ball 0.25, hoop 0.35, player/referee 0.5. Views `[(0,0,1280,720), (0,0,768,720), (512,0,1280,720)]`.
- Decode: stride 2, production frame selection and source-PTS rule. No index-clock fallback was needed. Every strategy saw identical `(frame_index, pts_ms, frame digest)`.
- Runtime: torch 2.8.0, rfdetr 1.3.0, native MPS (`PYTORCH_ENABLE_MPS_FALLBACK=0`), default MPS high-watermark. Commit `7604d0d` plus uncommitted experimental files.
- Production parity: fb1 kept output was exactly equal to `BasketballDetector.detect()` on the parity frames of every fb1 run (3 frames in the paired runs, 1 per isolated fb1 run).
- Tolerances (fixed before running): normalized box 1e-4 (0.13 px at 1280 wide), confidence 1e-4. Observed maximum difference: **0.0** for both.

## Measurements

### Isolated processes: the speed numbers to use

Each batch size ran alone in a fresh process, so it had a fresh MPS memory pool. Runs were sequential, fb1 → fb2 → fb4 → fb1, to bracket drift. Speedups are against the first fb1. This timing is **not paired**.

"Predict" is synchronized `model.predict()` only. "Pipeline" adds colour conversion, mapping and suppression. Decode is measured separately.

| Clip | Run | Images/call | Predict s | Pipeline s | Speedup (pipeline) | Decode s | MPS driver pool | Pageouts during run | Added latency |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Outdoor 12 s, 180 frames | fb1 | 3 | 44.56 | 44.67 | 1.000 | 0.44 | 2,236 MB | 0 | 0 |
| | fb2 | 6 | 43.61 | 43.70 | 1.022 | 0.43 | 3,739 MB | 0 | 67 ms |
| | fb4 | 12 | 44.13 | 44.24 | 1.010 | 0.47 | 7,269 MB | 378 | 200 ms |
| | fb1 (repeat) | 3 | 45.65 | 45.76 | 0.976 | 0.47 | 2,236 MB | 25 | 0 |
| Indoor 8.4 s, 84 frames (held out) | fb1 | 3 | 20.60 | 20.63 | 1.000 | 0.08 | 2,236 MB | 25 | 0 |
| | fb2 | 6 | 20.86 | 20.90 | 0.987 | 0.08 | 3,739 MB | 3 | 100 ms |
| | fb4 | 12 | 22.59 | 22.67 | 0.910 | 0.19 | 7,269 MB | 1,560 | 300 ms |
| | fb1 (repeat) | 3 | 21.30 | 21.34 | 0.967 | 0.09 | 2,236 MB | 0 | 0 |

Per-call medians in the outdoor isolated runs: 0.247 s for 3 images (fb1), 0.484 s for 6 (fb2), 0.981 s for 12 (fb4). The first-10 and last-10 medians are within 2%, so there was no warm-up drift.

### Where the time goes

Synchronized `predict()` is **99.7%** of the ball detector pipeline (outdoor fb1: 44.56 of 44.67 s). Host-side colour conversion and mapping/suppression total about 0.04 s each for the whole clip. Decoding the sampled frames takes 0.4 s outdoor and 0.1 s indoor. Process RSS stays around 0.9–1.0 GB, and model load takes 2.5–3.1 s.

Because the end-to-end gain is bounded by the predict gain, the end-to-end speedup equals the pipeline speedup to within 0.004. The ball pass is GPU-compute-bound per image, about 82 ms per 704×704 image.

### Paired run (same process): quality evidence; timing contaminated

| Clip | fb1 | fb2 | fb4 | fb1 repeat | Max MPS driver pool |
| --- | --- | --- | --- | --- | --- |
| Outdoor predict s | 64.41 | 176.79 | 225.39 | 71.16 | ~11.4 GB |
| Indoor predict s | 21.57 | 23.55 | 24.83 | 21.58 | ~10.4 GB |

Interleaving three batch shapes in one process kept every shape's MPS buffers alive: the driver pool reached about 11.4 GB during the outdoor passes, and swap reached about 9 of 10 GB. Even fb1 ran 64 s instead of the 44–49 s measured in isolation. The 8/16-frame probe ran only after these passes, but the indoor paired run started from that swapped state. **Use these paired runs for output equality only, not speed.** Outputs were still bit-identical across all four passes on both clips.

### Memory and out-of-memory behaviour (outdoor, after the paired run, same process)

| Frames/call | Images | Result |
| --- | --- | --- |
| 8 | 24 | Completed, but driver pool 16.5 GB on a 16 GB machine. Heavy swapping: median 78.9 s per call (9.9 s per frame), against 0.08 s per image normally |
| 16 | 48 | **MPS out of memory**: "MPS allocated: 3.31 GiB, other allocations: 13.33 GiB, max allowed: 20.13 GiB. Tried to allocate 4.04 GiB." Caught, reported, process continued |

The MPS error suggests `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0`. That removes the safety limit ("may cause system failure"), and I did not use it. The probe is now opt-in (`--probe-batches`), because it can push a 16 GB machine into swap.

## Commands

```sh
# Guard (exit 1 if any data/*/scoutfilm.sqlite3 or $SCOUTFILM_DATA has a queued/running/
# cancelling job, basketball_job or highlights record, a worker/benchmark process
# is running, or the shared GPU lock data/benchmarks/ball_views/.gpu.lock is held)
.venv/bin/python scripts/benchmark_ball_batches.py preflight

# Isolated speed runs: one fresh process per batch size (fb1 first and last)
export PYTORCH_ENABLE_MPS_FALLBACK=0
for k in 1 2 4 1; do
  .venv-ball/bin/python scripts/benchmark_ball_batches.py run data/trackid3x3/outdoor_stationary_12s.mp4 \
    --device mps --expect-sha256 144d758db53298cadb887bcee996284e569a03e45ed93c43265586933dc08b95 \
    --single --frame-batches $k --execute || break
done
# Indoor: data/trackid3x3/basket_S1T1_pre.mp4, sha 8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad

# CPU-only comparison and gate (reference first = an fb1 run folder)
.venv/bin/python scripts/benchmark_ball_batches.py compare REF_FB1_DIR FB2_DIR FB4_DIR FB1_REPEAT_DIR \
  --output data/benchmarks/ball_batches/compare.json

# Paired same-process run (output equality; timing contaminated by pooled memory)
.venv-ball/bin/python scripts/benchmark_ball_batches.py run data/trackid3x3/outdoor_stationary_12s.mp4 \
  --device mps --expect-sha256 144d758d… --frame-batches 1 2 4 --execute   # add --probe-batches 8 only if you accept swapping

# Checks
.venv/bin/python -m pytest -q backend/tests/test_batched_ball_detector.py
.venv/bin/ruff check scripts/benchmark_ball_batches.py workers/video/batched_ball_detector.py backend/tests/test_batched_ball_detector.py
```

Guard behaviour:

- `run` refuses without `--execute`, requires an explicit `--device`, and refuses while the guard is blocked.
- It re-reads every job database before **every frame group**, which is between batches, and checks processes every 8 groups.
- If an analysis appears, it aborts with exit 2 and writes a summary marked `aborted`.
- Out-of-memory errors are caught per strategy and reported, not retried.

Evidence is in `data/benchmarks/ball_batches/` (git-ignored):

- **Paired runs:** `20261108T023332Z-144d758db532` (outdoor, plus the 8/16-frame probe) and `20261108T024729Z-8af3f89ccd35` (indoor, plus the 8-frame probe).
- **Isolated outdoor:** `20261108T025210Z…`, `…025303Z…`, `…025355Z…`, `…025449Z…`.
- **Isolated indoor:** `…025544Z…`, `…025611Z…`, `…025640Z…`, `…025712Z…`.
- **Gate output:** `isolated_outdoor_compare.json` and `isolated_indoor_compare.json`.
- **Per-run files:** `fbK.jsonl` holds per-frame kept and raw observations with PTS, digest and batch position; `fbK_calls.jsonl` holds per-call timing and memory.

## Limitations

- One machine (M5, 16 GB) under real background memory use. About 8 GB of swap was already in use before the isolated runs, from other apps. Pageouts are recorded per run. The fb4 runs caused their own swapping, which probably explains the indoor fb4 slowdown. On a larger-memory Mac, fb4 might break even, but nothing here suggests a gain.
- CPU and CUDA were not measured. On a CUDA GPU with spare occupancy, batching could behave differently. Re-run this harness there before drawing conclusions.
- Two short clips, 264 frames in total. Quality equality is exact on those frames only.
- Detector-only experiment. The coordinated worker also runs pose on the same decoded frames, which was not measured here.

## Suggested next experiments (not run)

Batching cannot help while one 3-image call already saturates MPS. To cut the about 45 s of ball inference per 12 s of footage, the per-image compute must drop. Each option below changes numerics or recall, so each needs this harness's tolerance gate plus a coverage/segment review:

1. RF-DETR's `optimize_for_inference()` (traced model, optionally fp16) at the same resolution.
2. Running the ball pass at stride 3 instead of 2. This changes what is sampled, so it needs the non-regression review used in `docs/BALL_PERFORMANCE_PLAN.md`.
3. Overlapping ball inference with pose and decode in the coordinated worker. This needs no change to model outputs; measure wall clock, not detector time.
