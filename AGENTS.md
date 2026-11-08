# PlayLattice agent instructions

## Goal and source of truth

Build a sports video intelligence platform, with basketball as the first working local prototype. Product name is PlayLattice; repository name is `yc-naive-hackathon`.
Requirements: [YC Naive Hackathon brainstorming](https://docs.google.com/document/d/1kEtq_jr14fybmyRyH_d0h5GALDyeX0YExyt-AdtnJrM/edit), especially “Implementation Order — Local Core → Naive SDK → Sponsors (Authoritative)”. See `docs/PROTOTYPE_PLAN.md` and `docs/API_CONTRACT.md` for the implementation baseline. These are proposed interfaces, not existing functionality.

## Sports scope

Keep ingest, tracking records, clipping, jobs, consent and UI infrastructure reusable across sports. Carry `sport` on each game and worker options; use `basketball` for the first prototype. Keep sport-specific event vocabulary, court geometry and analysis rules in a small sport configuration/module rather than shared pipeline code. Implement only basketball now; add other sports after the first prototype works. Do not build a generalized plugin framework upfront or claim unvalidated sports support.

## Build order

1. Bootstrap FastAPI + SQLite, React/Vite/TypeScript, local media storage and a shared fixture contract.
2. Submit one authorized basketball MP4 → probe → real player detection/pose/tracking → timestamped updates → dashboard → two rendered, verified clips.
3. Add a private, explicitly consented roster profile, persisted jobs, bounded retries and audit evidence. Keep outreach simulated locally.
4. Stop at Stage 1 until the team accepts the local demonstration. Only then add Naive orchestration; sponsor integrations follow later.

Do not put cloud accounts, payment, email, hosted storage or sponsor SDKs on the prototype critical path. Do not train a custom event classifier. Use replay-paced MP4 and label it accurately. Coach-supplied time ranges are an acceptable event fallback; detection/tracking must still be real and identified as such.

## Independent workstreams and file ownership

| Stream | Owns | First deliverable |
| --- | --- | --- |
| Integration lead | root config, dependencies, `docs/`, `backend/app/models/`, migrations, shared fixtures, CI | freeze contract, bootstrap, integrate and run acceptance |
| Video | `workers/video/`, `workers/render/`, `backend/app/cv/`, `backend/tests/video/` | process a local MP4 into tracks/events; render and independently verify two clips |
| Backend | `backend/app/api/`, `backend/app/services/`, `backend/app/db/`, `backend/app/agents/`, `backend/tests/api/` | durable local job API, worker bridge, SSE, consent and audit |
| Frontend | `frontend/` | upload/replay dashboard, tracking overlay, timeline, clip viewer, private profile |

With two people: Person A owns Video; Person B owns Backend + Frontend + integration. With more coding sessions, split the three streams above. Parallel implementation is encouraged once the lead commits the shared contract. Do not spawn agents merely to restate this plan.

Claim a stream and state the files you will edit before starting. Read current files and git status. Never overwrite another stream's changes or reset shared work. Only the integration lead edits shared models, root manifests/lockfiles, migrations and contracts; request changes through the lead. Use short feature branches/worktrees for independent sessions, small commits and frequent integration. Do not merge a branch over uncommitted shared work.

## Contract and handoff discipline

Use `docs/API_CONTRACT.md`; milliseconds always refer to the original source. Video workers receive paths and emit typed data/artifact manifests, never own API routes or database state. Backend owns persistence and converts local artifact paths into media URLs. Frontend uses a service layer and a labeled fixture mode while workers are built; real mode must consume actual output.

Each handoff includes changed files, callable interface/run command, input/output example, checks run, known limitations and the next integration step. Raise interface changes before implementing them. Do not invent SDK methods or advertise scaffolded capabilities as working.

## Verification and completion

Test meaningful boundaries: invalid media; time ranges; actual tracks; ffprobe validation of rendered clips; job persistence/retry; consent filtering; SSE reconnect. Run checks appropriate to changes (Python tests/lint, TypeScript typecheck/build once tooling exists). Add a CPU smoke path; GPU performance must be measured locally. Never claim tests passed unless executed.

First thin-slice gate: one MP4 submission produces actual tracking updates visible in the UI and two playable source-linked clips, with exact commands and measured inference FPS/UI delay. Full Stage 1 additionally requires a consented private profile, a persisted report/audit, one injected failure with bounded retry or safe escalation, and local allowed/suppressed outreach simulation. Preserve an offline demo.

## Data boundaries

Use only authorized footage; request a source if none exists. Synthetic media can test plumbing but cannot validate basketball recognition. Keep uploads, rosters, generated clips, databases, checkpoints and secrets out of Git. Commit only small licensed fixtures or retrieval manifests with hashes and license/permission notes. Bind local services to loopback by default; serve only registered media artifacts, never arbitrary paths.

Do not fabricate events, identities, accuracy, latency or official stats. Unknown jerseys remain unverified. Link profile clips only through a supplied verified roster mapping and explicit consent; profiles stay private. No facial identity recognition, athlete rankings, unsolicited recruiting outreach or public publication. External sends, publishing and real payments are outside the local prototype.
