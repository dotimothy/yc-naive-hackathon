# One Analyze action, one source decoder

PlayLattice's Analyze Footage action now runs player pose/tracking and ball/hoop detection in one coordinated worker. A single OpenCV capture walks the original source, retrieves each needed frame once, and passes it to either or both models. Default sampling is player stride5 and ball stride2. Both models use the same source timestamps (decoder PTS, with an explicit CFR fallback).

The models remain separate. Player associations stay provisional, fragment reconnections require review, and ball trajectories never declare made/missed outcomes. Existing source-time corrections and reviewer attribution gates still apply.

## Runtime and API

The core API calls `workers.video.coordinated.run_analysis`. When the optional detector environment and pinned checkpoint are installed, it starts `scripts/run_analysis.py` under `.venv-ball/bin/python` (override `SCOUTFILM_BALL_PYTHON`). That one process loads both models and owns the shared decoder. Newline JSON carries updates to the API; synchronous acknowledgements preserve registered media URIs in the worker's clip manifests. Model logs go to the job's `worker.log`. Worker timeout defaults to3600 seconds (`SCOUTFILM_WORKER_TIMEOUT_SECONDS`), and timed-out/crashed children are reaped.

`POST /api/games/{id}/analyze` defaults to `basketball:true`, `stride:5`, `ball_stride:2` and analyzes the full source. Advanced callers can explicitly disable the optional model. If setup is missing, player processing continues and ball status is explicitly `unavailable`. If the detector fails, its status is `failed` and the player pass can finish. There are no invented detections.

`analysis.progress` SSE updates carry the common source timestamp plus player and ball processed-frame counts. `basketball.batch` worker updates are persisted as current-job observation batches. `GET .../basketball` and `GET .../basketball/objects` expose live and completed evidence only for the latest parent analysis. Object responses carry `run_id` and `parent_job_id`; the UI cancels old requests and polls while the parent job runs. Completed-job replay cannot overwrite identity corrections.

The former `POST .../basketball` returns410 and directs callers to Analyze Footage. The standalone `scripts/analyze_basketball.py` remains an offline diagnostic CLI. Source-linked proposals still enter review explicitly through the existing candidate import route. Identity corrections invalidate older actor mappings.

## Local acceptance (Apple MPS)

On the authorized stationary outdoor TrackID3x3 sample (12.012 seconds,360 source frames):

- One decoder;216 frame retrievals (union of the two sampling schedules).
- 72 player-model frames,180 ball-model frames.
- Ball detections on108/180 sampled frames,13 trajectory fragments,1 stable hoop proposal,0 shot proposals. Coverage is not accuracy.
- Eight rendered highlight clips passed source/ffprobe verification; media URI registration survived worker IPC.
- About94.3 seconds total;54.55 seconds ball inference. This remains offline batch analysis and is not a demonstrated speedup over the separate model passes.

A real CPU smoke processed an authorized0.6-second excerpt:18 source frames,4 frames per model at stride5,2 verified clips,8.37 seconds total. It checks the CPU path, not recognition accuracy.

Backend tests cover one capture and model cadence, shared timestamps, partial detector failure, optional setup fallback, live persistence and current-parent scoping, worker URI acknowledgements, timeouts and missing results. Browser checks cover one Analyze action, live polling, delayed-response cancellation, unavailable/failed states, overlays, playback and mobile overflow. Future detection-quality changes require labeled footage validation; combining the loop does not fix ball recall or identity swaps.
