# Prototype contract v0

Proposed local interface, to be implemented. FastAPI/Pydantic models will be canonical; frontend types and fixture payloads must match them. Integration lead owns changes. All IDs are opaque strings; source times are integer milliseconds; confidence is 0–1; unavailable values are null.

## First implementation status

The local API implements the video/job/track/clip subset plus game listing, sample loading and game job listing. Profiles currently return an empty list. The remaining profile/consent workflow is planned. Additional implemented analysis options: `stride` (1–20, default 5), `confidence` (0.1–0.99, default 0.75), and optional `device` (`auto`, `cpu`, `mps`, `cuda`, `cuda:N`; default runtime policy). Unavailable explicit devices return 422 before a job is queued. Reports include actual device/execution mode and synchronized timing. Sample court polygons are manually supplied game metadata.

Current deviations from the initial proposal: pose points expose `visibility` rather than an invented calibrated confidence; default segment origin is `review_window` and events remain unverified. SSE adds `server_timestamp_ms` for transport-delay measurement. Transient retries restart the whole job (max three executions) rather than resuming a phase; interrupted jobs fail explicitly on API restart. API validation errors use FastAPI’s `detail` shape. These limits must remain visible until their implementations change.

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
