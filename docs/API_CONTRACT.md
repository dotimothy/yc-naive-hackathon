# Prototype contract v0

Proposed local interface, to be implemented. FastAPI/Pydantic models will be canonical; frontend types and fixture payloads must match them. Integration lead owns changes. All IDs are opaque strings; source times are integer milliseconds; confidence is 0–1; unavailable values are null.

## First implementation status

The local API implements the video/job/track/clip subset plus game listing, sample loading and game job listing. Profiles currently return an empty list. The remaining profile/consent workflow is planned. Additional implemented analysis options: `stride` (1–20, default 5), `confidence` (0.1–0.99, default 0.75), and optional `device` (`auto`, `cpu`, `mps`, `cuda`, `cuda:N`; default runtime policy). Unavailable explicit devices return 422 before a job is queued. Reports include actual device/execution mode and synchronized timing. Sample court polygons are manually supplied game metadata.

Current deviations from the initial proposal: pose points expose `visibility` rather than an invented calibrated confidence; default segment origin is `review_window` and events remain unverified. SSE adds `server_timestamp_ms` for transport-delay measurement. Transient retries restart the whole job (max three executions) rather than resuming a phase; interrupted jobs fail explicitly on API restart. API validation errors use FastAPI’s `detail` shape. These limits must remain visible until their implementations change.

## Highlights extension

[HIGHLIGHTS_CONTRACT.md](HIGHLIGHTS_CONTRACT.md) defines per-analysis candidates, private track groups, review, scoring and generation endpoints. Automatic candidate origin is `visual_heuristic`; reviewed outcomes use `reviewer_confirmed`. Default analysis now generates selected highlights; fixed `review_window` clips remain a fallback when no candidate qualifies. `highlights.updated` SSE carries the current public bundle. These scores are highlight priority, not athlete merit or official statistics.

## Sport context

Each game includes `sport`; multipart upload accepts `sport`, defaulting to `basketball`. Stage 1 supports only `basketball` and rejects unsupported values explicitly. Pass sport through worker options and resolve sport-specific event vocabulary/court configuration there. Shared tracking and clip records remain sport-neutral; event types are interpreted in the parent game’s sport context. Other sports are planned, not implemented.

## Records

- `GameEvent`: `event_id, game_id, chunk_id, start_ms, end_ms, event_type, track_id, jersey_candidate, confidence, evidence_uri, clip_uri, verified, origin`. Track/jersey/URIs may be null. `origin`: `model | coach_annotation | fixture`. Event verification requires evidence; it does not imply verified athlete identity.
- `TrackSample`: `game_id, track_id, timestamp_ms, bbox, keypoints, confidence`. `bbox`: normalized `[x,y,width,height]`; `keypoints`: `[{name,x,y,confidence}]`, normalized coordinates, or empty if pose unavailable. Track IDs are local to a game and are not athlete identities.
- `Job`: `job_id, game_id, phase, status, attempts, error, artifacts`. `status`: `queued | running | succeeded | failed | needs_review`; `phase`: `ingest | track | render | verify | profile | complete`; attempts count per-phase executions; error nullable. Artifacts are registered records `{artifact_id, kind, uri, sha256}`.
- `AthleteProfile`: `profile_id, roster_id, consent_state, visibility, verified_clip_ids`. Consent: `pending | granted | revoked`; visibility is `private` in Stage 1. Clip linkage requires consent plus separately verified roster-to-track mapping.
- `Clip`: `clip_id, game_id, event_id, source_start_ms, source_end_ms, uri, sha256, qa_status, identity_verified`. QA: `pending | passed | failed`; preserve the source hash in the artifact manifest.
- `AuditEntry`: `id, job_id, actor, action, timestamp, attempt, outcome, details`; timestamp is UTC ISO 8601, details omit personal data/secrets.

Validate `0 <= start_ms < end_ms <= source_duration_ms`. Never expose machine paths to the browser.

## Local HTTP surface

| Method/path | Request | Result |
| --- | --- | --- |
| POST `/api/games` | multipart `video`, `rights_confirmed=true` | 201 `{game_id, duration_ms, source_sha256}` after validation |
| POST `/api/games/{game_id}/analyze` | `{candidate_ranges?: [{start_ms,end_ms,event_type}], replay_paced: boolean}` + `Idempotency-Key` | 202 `Job`; duplicate key returns existing job |
| GET `/api/jobs/{job_id}` | — | `Job` |
| GET `/api/games/{game_id}/events` | — | `{items: GameEvent[]}` |
| GET `/api/games/{game_id}/tracks` | optional `after_ms` | `{items: TrackSample[]}` |
| GET `/api/games/{game_id}/clips` | — | `{items: Clip[]}` |
| GET `/api/games/{game_id}/stream` | SSE, optional Last-Event-ID | persisted ordered update stream |
| GET `/api/profiles` | — | only consented private profiles in the local workspace |
| GET `/api/jobs/{job_id}/audit` | — | `{items: AuditEntry[]}` |
| GET `/api/media/{artifact_id}` | registered artifact ID | local media with range support |

SSE envelope: `{sequence, kind, game_id, job_id, payload}`. `kind`: `job.updated | tracks.batch | event.created | clip.updated | profile.updated | audit.created`. Use sequence as SSE id, persist/replay on reconnect, send heartbeats. Errors use `{error:{code,message,retryable}}` with proper HTTP status; never return a success payload for failed processing.

## Worker boundary

Video owner implements a Python callable `process_video(source_path, output_dir, options, emit)` with no database/HTTP dependency. Options include game/job IDs, candidate ranges, CPU/device selection and replay pacing. `emit(kind,payload)` uses the records above. Return a manifest with source hash, duration, tracks/events, artifacts and measured timings. Rendering exposes `render_clip(source_path,start_ms,end_ms,output_path)`; QA separately probes the output and validates source range, duration tolerance and nonempty decodable video.

Backend supplies `emit`, records outputs, maps artifact paths to IDs/URIs and publishes SSE. Phase retries are capped (initial execution + at most two retries), persisted and idempotent; use stable artifact keys and atomic writes. Unsupported media fails without retry; ambiguous identity becomes `needs_review`. Frontend fixture producer uses this exact interface with `origin=fixture` and a visible fixture label.

### PlayLattice reviewed basketball statistics and identity corrections

Player cards expose `basketball_stats` derived from all approved source events, independently of clip budget. The ledger carries event source milliseconds, optional made-shot point value, actor confirmation, duplicate confirmation, and evidence links. No evidence displays a dash. Unknown shot values hold points; uncertain automatic multi-fragment groups hold attribution until corrected and confirmed. These are partial reviewed samples, never full-game totals.

`PUT /api/games/{game_id}/highlights/candidates/{candidate_id}/review` accepts `event_time_ms`, `points` (1/2/3 for made field goals, 1 for free throws), `actor_confirmed`, and `distinct_event_confirmed`. Supported outcomes include made/missed field goals, made/missed free throws, assists, rebounds, steals, blocks and turnovers. Event times must lie within the candidate's source range. Exact duplicate type/time pairs count once; nearby same-type events need explicit distinct-play confirmation.

Reviewer groups override automatic associations. `PUT .../highlights/players` rejects groups whose tracks have concurrent source observations. Changed group assignments reset affected event approvals to pending, clear actor confirmation and invalidate rendered highlights.

`POST .../highlights/identity/splits` accepts `{track_id, split_ms}` for an original source track. The requested time snaps to its nearest interior sampled timestamp. Source batches remain immutable; corrected track IDs are `original~before` and `original~{boundary_ms}`. Each fragment receives a separate unconfirmed anonymous identity. Corrections clear this analysis's saved groups, reviews and rendered highlights, rebuild candidates, increment `identity_revision`, and invalidate older ball actor mappings. `DELETE .../highlights/identity/splits` removes all splits with the same invalidation. Corrected samples are returned by the existing tracks endpoint. A split does not confirm athlete identity.

Tracking metadata optionally adds anonymous `team_label` (`Team A`, `Team B`, `unknown`) and `role_status` (`likely_non_player`, `review_required`, `unknown`). These are heuristic review prompts. Nobody is hidden or deleted. Reports include continuity counters and unconfirmed merge proposals. Provisional review clips retain track evidence but carry no confirmed `player_id`; `attribution_status` states confirmed/unconfirmed.

### Optional local basketball object analysis

`GET/POST /api/games/{game_id}/basketball` reads/queues a separate ball and hoop pass. POST requires `Idempotency-Key`, accepts device/stride/confidence thresholds and a bounded `max_seconds` (default60, max120). The optional worker environment and pinned checkpoint must be installed locally. `GET .../basketball/objects` returns observed ball/hoop boxes in normalized xywh and source milliseconds. UI overlays only the prior observed sample within200ms.

Reports contain detection coverage, stable hoop proposals, trajectory candidates, measured device/wall time and analyzed source scope. Made/missed outcomes remain unknown. `POST .../basketball/candidates/{candidate_id}/review` imports a pending highlight proposal only after a reviewer selects a current track and explicitly confirms the actor. Parent analysis and identity correction revision must match. Importing does not approve an outcome or add a statistic.

Production identity uses `reconnection="review"`: cross-track and long-gap links become `identity_merge_suggestions` in the job report, never automatic merges. Each suggestion includes its fragment `track_id`, source `timestamp_ms`, ranked `candidates` (`player_id`, label, `track_ids`, matching cost, gap), and excluded simultaneous alternatives. The UI offers unselected pairings that load the existing reviewer group form; saving requires explicit confirmation. Historical evaluator variants explicitly use `reconnection="automatic"` for comparison. See `docs/IDENTITY_REVIEW_HANDOFF.md` for validation and remaining errors.

### Unified analysis update

Analyze Footage now coordinates both models in one decoded source pass; `POST .../analyze` defaults to `basketball:true`, `ball_stride:2` alongside player `stride:5`. The former separate `POST .../basketball` is retired (410). Its GET/report/object and proposal-review routes remain. Unified ball jobs use the parent analysis ID as `run_id`; live object responses include parent/run scope. `analysis.progress` SSE carries common source milliseconds, player `processed_frames` and `basketball_processed_frames`. Reports expose `analysis_mode:"shared_decoder"`, `retrieved_frames`, `basketball_status`, and the final basketball report. Optional setup absence is `unavailable`; detector errors are explicit partial failures, with no outcome attribution. See [unified analysis](UNIFIED_ANALYSIS.md) for runtime, protocol, measured performance and limits.

### YOLO26 Nano pose model

New analyses default to `ultralytics/yolo26n-pose`, input size1280, confidence0.25 and NMS-free end-to-end inference. These thresholds are model-specific; old persisted R-CNN reports and cached tracks are unchanged. Reports record `pose_model_manifest` (official release/hash/license), `pose_image_size`, `pose_confidence_threshold`, `pose_end_to_end` and `keypoint_visibility_threshold`. `execution_mode:"mps"` denotes native MPS inference; the old hybrid path is retired from production. Decode/letterboxing/output serialization still use CPU. The worker disables MPS fallback and Ultralytics online telemetry.

Track keypoints retain normalized `x`, `y`, integer `visibility` (YOLO score>=0.5), and optionally raw uncalibrated `confidence`. The legacy COCO-17 joint order and boxes contract are preserved. Native MPS/CPU smoke runs and frozen detector/identity comparisons are documented in `docs/YOLO26_POSE_VALIDATION.md`. That comparison found materially lower player coverage, so reviewer attribution remains required.

### Analysis cancellation

`POST /api/jobs/{id}/cancel` cancels a queued or running analysis. Running work transitions through `cancelling` until the worker stops, then `cancelled`; queued work becomes `cancelled` without starting inference. Repeated requests are idempotent, and completed/failed jobs are unchanged. Cancelling still owns the active timeline until termination, so a replacement cannot race the old worker.

A durable per-job marker survives restart. The coordinated worker checks every250ms and kills/reaps its isolated process group (including render subprocesses); the in-process fallback checks between operations. No automatic retries occur after cancellation. Partial tracks, basketball observations, highlights, reports, events and generated clip registrations are cleared. The uploaded source and other jobs remain intact. Cancelled state is terminal and historical stream replay cannot restore old outputs. The UI offers Cancel Analysis beside both the main action and video controls; a cancelled video can be re-run with a new request key.

### Camera shots and gameplay categories

`GET /api/games/{game_id}/shots` returns current `{job_id,status,items}`. Items include string `shot_id`, source `start_ms/end_ms`, raw `category` (`gameplay_candidate`, `non_gameplay_candidate`, `uncertain`), evidence/reasons and `review_status` (`pending`, `confirmed_gameplay`, `confirmed_non_gameplay`). PUT `.../shots/{shot_id}/review` accepts `{job_id,decision:gameplay|non_gameplay|pending}`. POST `.../shots/{shot_id}/clip` accepts `{job_id}` and returns a cached, source-verified camera-shot clip. The original upload is retained. See [camera shots](CAMERA_SHOTS.md).

Worker `shots.updated` carries live ranges. New track and object samples add `shot_id`; tracker IDs are scoped across camera changes. Highlight/ball candidates and rendered clips are bounded by shots. Unknown/non-gameplay categories require reviewer gameplay confirmation before automatic selection. This is a provisional visual-view classifier, not an official game-event or athlete-identity classifier.
