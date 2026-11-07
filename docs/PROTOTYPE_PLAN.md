# Fast local prototype

Source: [planning doc](https://docs.google.com/document/d/1kEtq_jr14fybmyRyH_d0h5GALDyeX0YExyt-AdtnJrM/edit), read 2026-11-07. The final implementation-order section supersedes earlier cloud-first schedules.

## Start together, then separate

Integration lead first bootstraps Python/FastAPI/SQLite and React/Vite/TypeScript, commits canonical models and one JSON fixture, and records exact setup commands. Check Python, Node, FFmpeg/ffprobe and CPU/GPU support; choose and pin compatible dependencies. No SDK or GPU is assumed installed. Acquire one rights-cleared basketball MP4 and 2–3 explicitly supplied roster identifiers. If footage is unavailable, proceed with fixture UI and synthetic plumbing tests, and report real CV validation as blocked.

| Stream/branch | Can start independently | Ready for integration when |
| --- | --- | --- |
| Video / `feature/cv-pipeline` | local file + contract | actual tracks/pose, timestamped candidates, two FFmpeg clips and QA manifest |
| Backend / `feature/local-api` | contract + fixture worker | SQLite jobs/audit, worker callback adapter, media serving and reconnectable SSE |
| Frontend / `feature/dashboard` | contract + labeled fixture service | upload/status, video overlay, timeline and source-linked clips work through service abstraction |

First integration happens as soon as the worker emits a single real track batch; do not wait for sophisticated event classification. Backend and frontend must replace fixture mode before claiming the core works. The lead alone changes shared contracts and dependencies. For two developers, combine backend/frontend under Person B; Person A stays on video.

## Milestones

1. **First visible slice:** one upload → background analysis → actual timestamped tracks on video → two generated playable clips with source ranges and independent QA. Log actual processing FPS and source-to-UI delay. This is the first prototype checkpoint.
2. **Complete local Stage 1:** persist/restart job state; demonstrate one failed chunk retry; expose audit/report; add one consented private profile with verified roster linkage. Demonstrate allowed and suppressed outreach using synthetic records and a local outbox, without sending messages.
3. **Team acceptance:** clean-checkout setup works, smoke test succeeds on CPU, repeat one unattended end-to-end run, document limitations and commands. Only after acceptance integrate Naive; sponsors follow afterward.

## Acceptance evidence

- Invalid upload is rejected; authorized valid footage has hash, probe metadata and durable job.
- UI receives real tracking output, displays source timestamps and distinguishes candidates from verified events. Include at least one actual pose/track visualization.
- Two clips are decodable, source-linked and within original duration; QA reports measured duration/tolerance and output hash.
- Unconfirmed identity is withheld; revoked/missing consent prevents discovery linkage.
- Injected transient failure retries within bounds without duplicate artifacts; permanent failure escalates visibly. Job/audit survive backend restart.
- Profile, report and simulated outreach demonstrate local permissions without external services.

## Cut scope in this order

Cut automated sports-event classification, complex multi-player analytics, live camera ingest, then all optional integrations. Preserve actual tracking, source verification, clear replay labeling, consent and audit. Use coach-provided candidate time ranges explicitly labeled as annotations. Never replace real processing with fabricated fixture results in the demo.

No implementation or performance checks have run yet: this commit establishes instructions and boundaries only.
