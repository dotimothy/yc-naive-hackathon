# Phase 1 acceptance — autonomous local video processing

Status: **complete for the user's revised Phase 1 scope**, validated 2026-11-07.

## Authoritative scope

The user clarified that Phase 1 requires **input → unattended processing → outputs**, with no human intervention between submission and results. The human may cancel at any time. This supersedes the earlier planning requirement to finish private profiles, manual roster verification and simulated outreach before moving on.

An authorized upload is the input. Automatic tracking, ball/hoop observations, shot segmentation and source-verified clip rendering run without approvals. The dashboard opens the Clips tab when that newly submitted job completes. Optional identity corrections and reviewed statistics remain available, but do not gate the automatic workflow. Profiles and outreach are deferred. Naive integration is the next phase and is not installed or called here.

## What changed to close the gap

- **Upload & Analyze** sends multipart `auto_analyze=true`. The server durably queues the unified worker before returning the game and `analysis_job_id`. Closing the browser does not own/stop the worker.
- **Analyze TrackID3x3 Sample** queues the same workflow. Repeated sample submissions reuse the active/completed job instead of duplicating inference. Explicit new sample input after failure/cancellation can start another job.
- No React effect submits a second analysis. Advanced API clients retain upload-only behavior unless they opt into `auto_analyze`.
- Queueing errors preserve the registered upload and produce a durable `analysis_error`; partially created queued jobs become failed with an audit entry.
- Completed reports expose `automatic_output`: `status` is `clips_ready` or `no_eligible_clips`, with `clip_count`, explanatory `reason`, and `requires_human_action:false`.
- If uncertain/non-gameplay shots yield no eligible source ranges, the job finishes with an honest empty result. It does not pause for gameplay confirmation or fabricate highlights.
- Existing Cancel Analysis remains available for queued/running jobs, clears partial outputs, preserves the source and prevents retries after cancellation.

## Executed acceptance evidence

| Check | Actual result |
| --- | --- |
| Full outdoor sample, MPS | 12.012 s / 360 frames; 72 player inference frames, 385 track samples, 108/180 ball frames; six QA-passed clips; successful attempt took 54.97 s |
| Injected transient failure | Acceptance harness raises one OSError before real inference; automatic second attempt succeeds; audit records error → retry → completion; no manual resume or duplicate clip IDs |
| CPU smoke on real footage | 2.002 s excerpt / 60 frames; 12 player inference frames, 63 track samples; two QA-passed clips; 16.48 s; no GPU required |
| Browser input → output | One real upload POST; no browser Analyze/Generate/Review mutation; MPS analysis completed in 14.65 s on the 2.002 s excerpt; two clips; Clips tab selected automatically; no JavaScript exceptions |
| Browser cancellation | A second real automatic upload was cancelled through the existing button; terminal `cancelled`, at most one attempt, partial tracks removed |
| Output integrity | All six outdoor and two CPU clips independently fully decoded by FFmpeg; hashes, source bounds, shot-boundary containment and HTTP media range responses verified |
| Restart persistence | Acceptance harness re-enters app lifecycle and rereads persisted job/tracks/clips/audit. Live API was also stopped/restarted as a fresh process: completed job/report unchanged, media still playable, cancelled job remains cancelled |
| Automated checks | 196 backend tests pass; Python Ruff passes; frontend TypeScript/Vite production build passes |
| UI checks | Existing desktop/mobile tab/search/pagination/draft tests plus automatic-ingest transport/error test and real browser completion/cancellation check |

These are individual local runs on this Mac, not general speed or basketball-accuracy benchmarks. The injected retry uses the real worker for its successful attempt; mocks are confined to software boundary tests. The CPU excerpt is derived from the authorized TrackID3x3 sample, not synthetic recognition footage.

## Reproduce

Install the existing core and ball environments/models first; see [local setup](LOCAL_PROTOTYPE.md). Run serially without another analysis consuming GPU resources. Each acceptance output directory must be new.

```sh
.venv/bin/python scripts/accept_phase1.py \
  data/trackid3x3/outdoor_stationary_12s.mp4 \
  --device mps --output data/phase1-outdoor-new \
  --inject-transient --execute

# Prepare a short real-video CPU smoke source.
ffmpeg -i data/trackid3x3/outdoor_stationary_12s.mp4 -t 2 \
  -c:v libx264 -crf 18 -preset fast -an data/phase1-cpu-source.mp4
.venv/bin/python scripts/accept_phase1.py data/phase1-cpu-source.mp4 \
  --device cpu --output data/phase1-cpu-new --execute

.venv/bin/python -m pytest backend/tests -q
.venv/bin/ruff check backend workers scripts
npm --prefix frontend run build
```

`accept_phase1.py` uses the same FastAPI handlers as the dashboard in isolated storage. It makes no correction, event approval or highlight-generation request after upload. It verifies real tracks, at least two clips for these specific acceptance inputs, playback bytes, hashes, camera-safe bounds, audit and persistence. `--inject-transient` is a harness-only failure injection; there is no production backdoor or fixture substitution for inference.

Ignored evidence on this machine:

- `data/phase1_outdoor_acceptance/acceptance.json`: MPS job `7404082e9e6e470b9ffbc59bb7084c96` and its DB/artifacts.
- `data/phase1_cpu_acceptance/acceptance.json`: CPU job `55e9da9a133c49368551dbcef2bd3eef` and its DB/artifacts.
- `data/phase1_browser_acceptance.json` and `data/phase1-autonomous-output.png`: real browser flow. Completed job `18408b5ab0be47d3af388c0fc3be84d5`; cancelled job `09eb58c0f64f431b9b7c3e1dafc26441` in the main app.

Input source hashes: outdoor `144d758db53298cadb887bcee996284e569a03e45ed93c43265586933dc08b95`; CPU excerpt `f7d51388bdd0bcef5ddf0767a01ff8fb3da33a4a1419acd654d64dac09357fef`. TrackID3x3 attribution/terms remain in [dataset provenance](datasets/TRACKID3X3.md). Media and checkpoints stay out of Git.

## What completion does not claim

- Automatic highlights are editorial visual-activity selections, not verified sports outcomes or player ability rankings.
- Sparse ball detections, identity swaps/fragmentation and provisional gameplay labels remain quality limitations. Missing observations do not prove no play occurred. No unsupported identity is confirmed automatically.
- Unknown events, uncertain identities and lack of eligible clips are outputs, not human approval gates. Optional manual corrections do not change this default contract.
- CUDA execution, broadcast-wide evaluation, verified rosters/profiles, outreach, remote authentication/deployment and Naive SDK integration remain unimplemented or unvalidated.

Phase 2 can consume the completed source/job-bound evidence and clips without requiring the Phase 1 worker to wait for a human. Preserve technical clip validation and the optional cancellation override when adding orchestration.
