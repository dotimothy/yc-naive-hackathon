# Local prototype handoff

## Current milestone

Autonomous local Phase 1 is complete under the user’s updated input → processing → output scope. Upload & Analyze and Analyze TrackID3x3 Sample queue analysis automatically; no clip approval or generation click is required. Cancellation stays available. Profiles, verified rosters and outreach are deferred. See [acceptance evidence](PHASE1_ACCEPTANCE.md); historical measurements below retain their original model context.

## Start

From the repository root on macOS:

```sh
brew install ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/download_pose_model.py
.venv/bin/python scripts/download_sample.py --outdoor
npm --prefix frontend ci
npm --prefix frontend run build
scripts/run_local.sh
```

Open http://127.0.0.1:8000. Submit Upload & Analyze or Analyze TrackID3x3 Sample; processing and clip generation start automatically. Pose uses the hash-pinned official YOLO26n-pose checkpoint (~7.9 MB), downloaded explicitly above. Keep the checkpoint and sample for offline use. Run `scripts/setup_ball_detector.sh` to also enable the optional ball/hoop detector. No external inference, Naive or sponsor service is used. The worker now defaults to accelerated device selection; see [acceleration setup and benchmarks](ACCELERATION.md).

For frontend development, run `npm --prefix frontend run dev` alongside the API and open the Vite URL. Vite proxies `/api` to port 8000. The production frontend build is served by FastAPI; restart the API after the first build. Python 3.9.6 and Node 26.13.0 were used locally. `requirements.lock.txt` records the macOS arm64/Python 3.9 resolution; other platforms should resolve `requirements.txt`. Linux CPU users can install matching CPU Torch/Torchvision wheels from the official PyTorch index before the remaining dependencies.

## Current vertical slice

- Rights-confirmed MP4 upload, probing and source SHA-256; 250 MiB/30-minute/4K input limits.
- Background person/pose inference with automatic CUDA/MPS/CPU selection with YOLO26n-pose (native MPS, CPU or CUDA), every fifth source frame by default. Pose uses COCO17 points, separately from TrackID3x3's ten-keypoint ground truth.
- IoU/Hungarian baseline tracking; game-local IDs are temporary tracks, not athlete identity. Outdoor sample uses a manually marked court polygon to reduce off-court detections. This does not classify referees or guarantee athlete-only output.
- Persisted SQLite jobs, audit, track batches, clips and replayable SSE. Request keys deduplicate analysis; reconnect uses Last-Event-ID. Only one active job per game and one inference worker per API process.
- Video viewer with synchronized sampled overlays and source-timed review clips.
- Automatic scored highlights are rendered with FFmpeg; two fixed review windows remain a fallback if no candidate qualifies. See [scoring and review](HIGHLIGHT_SCORING.md). They are neither discovered sports events nor verified athlete highlights. Coach-supplied time ranges can be sent through the API.
- Independent clip checks: source bounds, duration tolerance, full decoding, hashes and first-frame visual alignment sanity check (mean absolute pixel error at reduced resolution). Static imagery can pass that sanity check at multiple timestamps; the source/time edit recipe remains essential provenance.
- Transient OSError/timeouts retry the whole job at most twice, replacing that job's materialized outputs and retaining audit history. A server restart marks interrupted jobs failed; resubmit explicitly with a new request key. Other processing errors fail visibly.

## Sample selection

Preferred sample: first 12 seconds of TrackID3x3 outdoor `IMG_0104.MOV`, scaled to 1280×720, audio removed. Visual inspection of start/middle/end shows a centered, stationary view of the entire **3x3 court**. It is not a full-length 5-on-5 court. Original source is ~1.3 GB; the downloader checks range support and asks FFmpeg to extract a short derivative rather than downloading it all. Attribution and transform/hash manifest are stored with the local media.

The indoor `basket_S1T1_pre.mp4` is retained as the ground-truth alignment fixture: 1280×720, 19.98 fps, 168 frames, 8.408 seconds. Indoor boxes are annotated every frame. A fully automated dataset accuracy evaluation has not been implemented; do not conflate annotation playback with predictions.

## Verification

```sh
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend workers scripts
npm --prefix frontend run build
```

The tests use generated media and a stub inference worker only for API failure injection; real inference is exercised separately on downloaded basketball footage. Test fixtures cannot establish basketball model accuracy.

## Measured local run (2026-11-07)

Outdoor 12.012-second source: 360 decoded frames, 72 inference frames (stride 5), 545 track samples, 29 temporary track IDs. Initial CPU baseline: **1.47 inference FPS**, **51.5 seconds wall time**, 0.23× realtime factor. Two clips of 6.006 seconds each passed full decoding, range/duration checks and first-frame alignment sanity checks (MAE 1.34 and 1.61 / 255). Temporary ID count reflects fragmentation and non-athletes, not 29 players.

Chrome browser integration checked source and clip playback, seeking with overlay updates, a browser-triggered analysis reaching success, persisted reload, and 390px mobile layout with no horizontal overflow or JavaScript exceptions. During the observed live-update window, server-write-to-browser median transport delay was 115 ms over 20 updates; this is not end-to-end inference latency. Four automated tests and Python lint/frontend typecheck/build passed.

## Boundaries and next work

Autonomous local Stage 1 acceptance is complete for the revised scope; optional profiles/outreach are deferred. Private profiles and roster verification, outreach simulation, phase-specific resume, model accuracy evaluation, better occlusion tracking and durable scheduling remain to be implemented. `/api/profiles` returns an empty list until consent/identity handling exists. Highlight-priority scores and reviewer-confirmed outcome labels are now available; player suitability scores, automatic jersey identities and official event statistics are not generated.

The baseline can miss people, include referees and fragment IDs. It is sampled batch analysis, not demonstrated real-time live coverage. Measure latency from each actual run; browser transport delay is distinct from model inference speed.

Naive and sponsors remain deferred. Media, model weights, databases and generated outputs remain out of Git. API is local and unauthenticated; do not expose it as a hosted service. YOLO26n-pose/Ultralytics use AGPL-3.0 or an Ultralytics Enterprise license; see the pinned model manifest and upstream license. TrackID3x3 data is CC BY 4.0 per the pinned upstream README. No jersey-number pipeline is included. See [YOLO migration validation](YOLO26_POSE_VALIDATION.md) for the measured coverage tradeoff.
