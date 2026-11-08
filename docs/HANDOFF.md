# PlayLattice handoff

Updated 2026-11-07. Implementation baseline: `develop` at `d6f247a` (Title Case tab labels). This document is the starting point for the next developer or coding session; follow [AGENTS.md](../AGENTS.md) for ownership and repository rules.

## Start here

- **Product:** PlayLattice, a local sports video intelligence app; basketball is the first supported sport.
- **Repository:** private [`dotimothy/yc-naive-hackathon`](https://github.com/dotimothy/yc-naive-hackathon).
- **Integration branch:** `develop`. Push further work here unless the user changes that instruction. Do not push to `main` by default.
- **Current checkout:** `/Users/tim/Documents/GitHub/yc-naive-hackathon`.
- **Local app:** http://127.0.0.1:8000. Health endpoint: `/api/health`. At handoff it responds successfully with MPS selected and FFmpeg/ffprobe available.
- **Order of work:** finish and accept the local core → integrate Naive SDK agents → add other sponsor plugins. Preserve sport-neutral ingest/storage/job infrastructure; implement basketball behavior first.
- **No integration task is in flight.** Prior agent experiments have finished. Their useful code and results are already included in `develop`; rejected optimizations remain experimental.

The app processes videos, shows real tracking/pose and object observations, generates playable highlights, and supports reviewer-confirmed basketball stats. It does not automatically produce reliable player ability rankings, official game stats or verified athlete identities.

## Run and verify

For a fresh checkout on macOS:

```sh
brew install ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/download_pose_model.py
scripts/setup_ball_detector.sh
.venv/bin/python scripts/download_sample.py --outdoor
npm --prefix frontend ci
npm --prefix frontend run build
scripts/run_local.sh
```

The two Python environments are intentional: `.venv` hosts the API/core dependencies, and `.venv-ball` supplies RF-DETR while sharing the core Torch installation. Download scripts pin model provenance and checksums. Raw videos, models, databases and generated output are ignored by Git and are **not** supplied by cloning the repo.

For an existing installation, reuse the environments and downloaded models. Start `scripts/run_local.sh` only if port 8000 is free. Rebuild the frontend after UI changes; FastAPI serves `frontend/dist`. Restart the API after Python server changes when no analysis is active. Vite development is available through `npm --prefix frontend run dev` and proxies API requests to port 8000.

```sh
curl http://127.0.0.1:8000/api/health
.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend workers scripts
npm --prefix frontend run build
```

Python 3.9.6 and macOS arm64 were exercised locally. Use `requirements.txt` across platforms; the lockfile captures a particular local environment. CUDA hardware/execution has not been validated on this Mac.

## Current user workflow

1. Upload an authorized basketball MP4, or load the TrackID3x3 sample.
2. Run **Analyze Footage**. One coordinated worker performs player and ball inference with persisted progress. **Cancel Analysis** stops the job and clears its partial derived output; source footage remains.
3. In **Film**, replay the source with sampled boxes, pose and ball/hoop overlays. Inspect provisional tracks and split identities at a swap if needed.
4. In **Players & Highlights**, use the searchable, paginated player table. **Details** opens source-backed stats/identity information; **Review clips** opens that group's candidate review.
5. Use the nested **Candidate Review** and **Identity Corrections** tabs for approvals and fragment grouping. Actor confirmation, source event time and outcome are required for basketball stats. Uncertain or duplicate evidence is held from totals.
6. **Camera Shots** provides provisional cut/gameplay segments, reviewer corrections and exact shot exports. **Ball & Hoop** exposes observations and review-required proposals. **Clips** contains paginated exports; **Analysis Details** contains job/runtime diagnostics.

Tabs remain mounted so review drafts and the source playhead survive navigation. Leaving a video panel pauses its playback. Seeking evidence from another tab returns to Film. Keyboard arrow/Home/End keys navigate section tabs. The film review queue is collapsed by default to save space.

## Architecture and entry points

| Area | Entry points | Responsibility |
| --- | --- | --- |
| API and persistence | `backend/app/main.py`, `backend/app/highlight_routes.py`, `backend/app/basketball_routes.py`, `backend/app/shot_routes.py` | Uploads, durable jobs, SQLite records, replayable SSE, reviews and registered media |
| Worker boundary | `workers/video/coordinated.py`, `scripts/run_analysis.py` | Start the isolated worker; newline JSON updates plus acknowledgements for registered media URIs |
| Analysis | `workers/video/pipeline.py`, `workers/video/runtime.py`, `workers/video/basketball_pass.py` | One source decoder, source timestamps, inference, tracking, reports and clip generation |
| Detection and identity | `workers/video/basketball_detector.py`, `workers/video/tracker.py`, `workers/video/identity.py` | RF-DETR observations, continuity, provisional identity and correction rules |
| Shots and highlights | `workers/video/shots.py`, `workers/video/shot_candidates.py`, `workers/video/highlights.py`, `workers/render/` | Camera boundaries, conservative event proposals, highlight selection and FFmpeg render/QA |
| Frontend | `frontend/src/main.tsx`, `SectionTabs.tsx`, `FilmPlayer.tsx`, `HighlightsPanel.tsx`, `PlayerHighlightsTable.tsx`, `PlayerStatsCard.tsx` | Film-room tabs, synchronized replay, tables, candidate review and evidence |
| Other panels | `LiveBoard.tsx`, `ShotSegmentsPanel.tsx`, `BasketballPanel.tsx`, `useGameRoom.ts` in `frontend/src/` | Summary, shot review, object evidence and live job state |

All time ranges are milliseconds on the original source. Decoder PTS is preferred, with an explicit CFR fallback. Camera shots use half-open ranges; downstream tracking continuity and highlight ranges must respect those boundaries.

Default local state is in `data/local/scoutfilm.sqlite3`, with job artifacts under `data/local/processed/<job_id>/`. Worker diagnostics are in `worker.log`; completed jobs have `report.json`, tracks and rendered clips. Only registered media artifacts are served. `SCOUTFILM_DATA` selects another local data directory.

## Actual ingress and egress for the Naive adapter

The linked [Naive SDK handoff tab](https://docs.google.com/document/d/1kEtq_jr14fybmyRyH_d0h5GALDyeX0YExyt-AdtnJrM/edit?tab=t.mnl0iz2yxfxu) contains the verified implementation update. The following HTTP surface exists now; proposed SDK method names are not implemented functions.

| Operation | Current interface | Actual contract |
| --- | --- | --- |
| Upload | `POST /api/games` | Multipart `video`, `rights_confirmed=true`, `sport=basketball`; returns 201 game metadata, source hash and registered source URI |
| Analyze | `POST /api/games/{game_id}/analyze` | JSON options + required `Idempotency-Key`; returns 202 public job, not a completed manifest |
| Status/cancel | `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/cancel` | States `queued`, `running`, `cancelling`, `cancelled`, `succeeded`, `failed`; inspect `phase`, `attempts`, `error`, `artifacts` and completed `report` |
| Tracks | `GET /api/games/{game_id}/tracks?after_ms=-1` | `{items:[...]}` for the latest analysis, with identity corrections and reviewer overrides applied |
| Objects | `GET /api/games/{game_id}/basketball/objects` | `{run_id,parent_job_id,items:[...]}`; normalized ball/hoop observations; check parent scope |
| Shots | `GET /api/games/{game_id}/shots` | `{job_id,status,items:[...]}`; shot bounds, provisional gameplay category and review status |
| Highlights/clips | `GET /api/games/{game_id}/highlights`, `GET /api/games/{game_id}/clips` | Candidate/review/stats bundle and `{items:[...]}` clip list; media through `/api/media/{id}` |
| Updates/audit | `GET /api/games/{game_id}/stream`, `GET /api/jobs/{job_id}/audit` | Persisted SSE with sequence IDs/reconnect support and `{items:[...]}` audit entries |

Actual analysis defaults: `basketball:true`, `ball_stride:2` (1–5), `stride:5` (1–20), `confidence:0.25` (0.1–0.99), `device:null` (auto policy), `replay_paced:false`, `candidate_ranges:[]` (at most 10 source-bounded ranges). A caller does not provide `job_id` or arbitrary `output_dir` through this HTTP request. The server allocates IDs and owns paths. Generic source-URL/stream ingress, requested-output schemas, world court coordinates, verified rosters and annotated-preview exports are not part of this route. The old separate `POST .../basketball` returns 410; use the shared analysis route.

```sh
# Use the game_id returned by the upload; choose a fresh key for a new analysis.
curl -X POST http://127.0.0.1:8000/api/games/GAME_ID/analyze \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: analysis-request-001' \
  -d '{"basketball":true,"stride":5,"ball_stride":2,"device":"mps"}'
```

Track and object `bbox` values are normalized **xywh**, not xyxy. Timestamps are `timestamp_ms` on the original source. Player keypoints are named objects `{name,x,y,visibility,confidence}`; visibility is a thresholded indicator, and confidence is an uncalibrated detector score. Source PTS is not a promised `frame_index/source_pts_ms` observation schema. Missing detections are absent observations, not interpolated evidence or confirmed misses.

An observed completed job from the portrait test (selected actual fields):

```json
{
  "job_id": "b5613cbf58224c659b4d3f1d540096ba",
  "game_id": "8c8c0a4924fc41c8be6f08fc04fcd362",
  "status": "succeeded",
  "phase": "complete",
  "attempts": 1,
  "error": null
}
```

Its actual report includes `analysis_mode:"shared_decoder"`, `device:"mps"`, `model:"ultralytics/yolo26n-pose"`, 360 decoded frames, 72 player-model frames, 335 track samples and 26.659291042 seconds elapsed. These are one local run's results, not API defaults or a performance promise.

Worker callable: `workers.video.pipeline.process_video(source_path, output_dir, options, emit)`. API entry: `workers.video.coordinated.run_analysis(source, output, options, emit)`. The CLI `scripts/run_analysis.py` expects an initial stdin JSON object `{source,output,options}` and an acknowledgement `{payload:...}` after every emitted update; simply piping one request into it without acknowledgements is not a valid standalone invocation. Final stdout is `{kind:"result",payload:report}`; diagnostics go to stderr/`worker.log`.

For an offline ball-only diagnostic, use `.venv-ball/bin/python scripts/analyze_basketball.py SOURCE --output OUTPUT --device mps`. It is not the unified production worker.

The Naive layer still needs authenticated access if the worker leaves loopback, job-scoped observation retrieval or explicit latest-parent guards, tool-level idempotency, bounded retry ownership and manifest normalization. Do not stack agent retries on the API's whole-job retry policy without accounting for duplicate executions. Actual SDK function names, GPU availability and deployment/authentication behavior must be verified during that integration.

## Active model configuration

| Task | Current setting |
| --- | --- |
| Player/pose | Pinned Ultralytics YOLO26n-pose, input size 1280, confidence 0.25, FP32, 17 keypoints; default stride 5 |
| Ball/hoop | E-BARD basketball-trained RF-DETR Nano, resolution 704, ball confidence 0.25 and hoop confidence 0.35; default stride 2 |
| Views | Landscape: full frame plus two overlapping 60%-width crops. Portrait: full frame only |
| Device | Auto: CUDA → MPS → CPU; explicit unavailable requests fail instead of silently selecting another device |

YOLO26 pose runs its learned model and tensor postprocessing on MPS with operator fallback disabled. Decode/preprocessing and serialized output still involve CPU work. RF-DETR inference is also selected for MPS here. The worker is a shared decode loop with separate model calls, not a single combined learned model or a proven concurrent-inference implementation.

The YOLO26 pose switch improved measured pose speed but reduced player-box coverage in held-out evaluation. Preserve that caveat; it was not a quality-preserving migration. Read [pose validation](YOLO26_POSE_VALIDATION.md).

Model/runtime/dataset licenses differ. Ultralytics uses AGPL-3.0 or Enterprise terms; E-BARD model weights are labeled CC BY 4.0; RF-DETR code is Apache 2.0. TrackID3x3 footage is CC BY 4.0 per the pinned upstream README. Keep attribution with shared demo assets.

## Evidence and completed checks

- **Backend:** 183 tests pass in the current checkout. Tests cover software boundaries; they do not establish basketball recognition accuracy.
- **Frontend:** typecheck/production build passes. Chrome desktop/mobile checks exercised tabs, player search/pagination, clip pagination, draft preservation, return-to-film seeking, hidden-video pausing, keyboard navigation, and no mobile horizontal page overflow or JavaScript exceptions.
- **Camera cuts:** a deliberate outdoor→indoor cut at 2 seconds was detected at the correct boundary. Identities reset and generated clips stayed within their shot. Two uncut short samples did not produce false cuts. This is not a broadcast cut-detection precision/recall benchmark.
- **Cancellation:** a real worker stopped in approximately 378 ms in the measured cancellation test; partial derived records were cleared.
- **Portrait:** a 12.012-second fixed crop of the outdoor sample, 396×704 (9:16), completed on MPS in 26.66 seconds: 335 player samples, ball observations on 47/180 sampled frames and 55 hoop observations. Eight exported clips retained 396×704 and fully decoded. No confirmed shot outcomes were inferred.

Portrait evidence remains locally in `data/portrait_acceptance/`. Its game is `8c8c0a4924fc41c8be6f08fc04fcd362`, job `b5613cbf58224c659b4d3f1d540096ba`; look for **TrackID3x3-portrait-9x16.mp4** in the UI. These local IDs are not portable fixtures. Crop recipe:

```sh
ffmpeg -i data/local/f4adfa16088142bbaa5d2cb4fbc9f9cc.mp4 \
  -vf 'crop=396:704:410:8,setsar=1' -c:v libx264 -crf 18 -preset fast -an \
  data/portrait_acceptance/TrackID3x3-portrait-9x16.mp4
```

The [README dashboard GIF](assets/dashboard.gif) is a real UI recording with persisted sample results. Its displayed job timings/model come from that saved run, not a fresh benchmark of the current pose model.

## Rejected experiments — keep the production defaults

| Experiment | Finding | Action |
| --- | --- | --- |
| Basketball-trained E-BARD YOLOv8n | Same 264 frames/three-view settings: 9.2 s detector time vs RF-DETR 62.5 s, but only 39 ball frames vs 164, and zero hoop frames vs 264 | Keep RF-DETR |
| Generic YOLOE-26n prompt-free | Executed on MPS but returned no ball or hoop detections across the two sample clips | Experimental adapter only |
| Adaptive view selection | About 16–20% faster in tested settings, but lost reference observations | Not enabled |
| RF-DETR cross-frame batching | Preserved outputs on the samples but no meaningful MPS speedup; increased memory and larger batches swapped/OOMed | Not enabled |

These are local measurements, not general architecture comparisons. Coverage and agreement with RF-DETR are not labeled accuracy. Experimental adapters, downloaders, benchmark scripts and tests are committed so the work is reproducible; none is wired into production analysis.

Details: [model comparison](EBARD_MODEL_BENCHMARK.md), [YOLOE candidate](YOLO_BASKETBALL.md), [view selection](BALL_PERFORMANCE_PLAN.md), [batching](BALL_BATCHING_RESULTS.md).

## Next priorities

1. **Accept one representative end-to-end video.** Upload → analysis → identity corrections → reviewer-confirmed stats → playable player highlights. Current evidence is mostly short stationary 3x3 clips; include occlusion, camera changes and footage held out from tuning. Record failures rather than silently merging identities or inventing events.
2. **Prioritize identity correctness.** Prevent swaps and incorrect merges, preserve uncertain fragments, and use reviewer corrections before attributing stats/clips. Validate on held-out footage before claiming improvement. The user explicitly made this a priority.
3. **Improve RF-DETR runtime only with paired quality checks.** Shared decoding and the model-alternative search are already done. A next candidate is the model's inference-optimization/tracing path at the same resolution; it has not been benchmarked. Compare actual job wall time as well as synchronized model time. CUDA needs its own hardware validation.
4. **Prepare a narrow Naive integration after local acceptance.** Use deterministic, idempotent tools for submitting an analysis, polling status, cancellation and retrieving source-linked metadata/clips. Carry job/source IDs, timestamps, shot ranges, detector provenance and review status. Investigate actual SDK/runtime support before writing integration code; no Naive API or GPU availability has been established here.
5. **Sponsor integrations follow Naive.** Verified roster consent/private profiles and any external workflows are separate unfinished work, not features already present.

Do not continue model shopping without new evidence. The agreed default is RF-DETR. Do not describe highlight-priority scores as player skill, unknown outcomes as misses, provisional groups as verified athletes, or lack of observations as proof no play occurred.

## Branch/worktree notes

Everything accepted is pushed to `develop`; its working tree was clean before this handoff document. Historical worktrees still exist for ball/hoop, cancellation, UI, camera shots, stats, tracking, unified analysis and YOLO pose.

Some old worktrees show untracked experiment files that have already been copied into `develop`. The old UI tree also retains a pre-integration design draft that later fixes supersede. A branch can show unique commit hashes even when its functionality was integrated with another commit. Compare contents before proposing another merge. Do not reset/delete these worktrees or overwrite a collaborator's changes just to tidy status.

Use `git status`, `git worktree list` and the remote tracking state before editing. For GitHub CLI-authenticated HTTPS operations on this machine, the working credential form is:

```sh
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin develop
```

Follow the file ownership rules in `AGENTS.md`. If parallel implementation is requested, assign disjoint files and a concrete acceptance check. Do not launch duplicate GPU benchmarks while a user analysis is running.

## Reference documents

[README](../README.md) · [API contract](API_CONTRACT.md) · [local setup](LOCAL_PROTOTYPE.md) · [shared analysis](UNIFIED_ANALYSIS.md) · [reviewed statistics](REVIEWED_STATS_CONTRACT.md) · [identity handoff](IDENTITY_REVIEW_HANDOFF.md) · [camera shots](CAMERA_SHOTS.md) · [acceleration](ACCELERATION.md) · [dataset provenance](datasets/TRACKID3X3.md)

Older documents retain historical timing/model information. Prefer each document's current-model notice and the actual job report over historical R-CNN measurements.
