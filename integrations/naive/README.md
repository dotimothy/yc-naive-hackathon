# PlayLattice Naive SDK onboarding

Server-side TypeScript integration, separate from the Python GPU worker and React frontend. The SDK and CLI are pinned in package-lock.json; no platform credentials are stored in this repository.

## Current status

- Authenticated to the operator's existing PlayLattice AI organization with its existing active plan; no subscription/top-up/plan changes were made.
- Created `playlattice-clip-curator` using a catalog-verified `z-ai/glm-5.3-flash` model.
- Operator-approved limits were read back from the server: **$10/month, $1/task**. The platform enforces them; the integration never automatically raises them.
- The agent has **no tools connected** and defaults to deny. It cannot publish, send messages, execute shell commands, access video files or call the local API from its hosted runtime.
- A real SDK session received structured metrics from the local Phase 1 API and returned the matching job ID, status, clip count and unverified-identity flag. No video bytes were uploaded and no new inference ran for that smoke.
- Recorded agent/session spend: **510 micro-USD ($0.00051)**. Observed remaining account balance after the test: **$249.331081**. These are a point-in-time observation, not a promised current balance.
- The provider intermittently returned capacity/rate-limit responses and status-read timeouts. The first session completed its reply, then cleanup cancelled that already-finished session after an observation timeout. Event readback verifies `end_turn` and the expected assistant output before cancellation. Replaying the stable smoke idempotency key reconciled the same session without another paid task.

**Next integration step:** connect narrowly scoped PlayLattice tools through authenticated connectivity or a worker-pulled queue. Hosted Naive cannot access this Mac's localhost. Agent-selected clip-plan rendering and end-to-end remote tool orchestration are not implemented by onboarding.

## Commands

From the repository root:

```sh
npm --prefix integrations/naive ci --ignore-scripts
npm --prefix integrations/naive run typecheck
npm --prefix integrations/naive test
npm --prefix integrations/naive run smoke:local

# Interactive hidden entry; do not put a key in a command argument or chat.
npm --prefix integrations/naive run login
npm --prefix integrations/naive run doctor

# Read-only plan for the single agent; no sessions or resources created.
npm --prefix integrations/naive run onboard
# Create/reuse only this agent after verifying the printed plan and approved limits.
npm --prefix integrations/naive run onboard -- --apply

# Starts one paid session if this local job has not already been used for the v1 smoke.
npm --prefix integrations/naive run smoke:naive

# One snapshot, or a read-only local watcher polling about once per minute.
npm --prefix integrations/naive run monitor
npm --prefix integrations/naive run monitor -- --watch
npm --prefix integrations/naive run session:inspect -- SESSION_ID
```

The watcher continues only while its local process and machine are running. Ctrl-C stops it. It does not send notifications, create scheduled cloud jobs, buy credits or change budgets. The agent's cap does not cap unrelated agents or subscription charges in the same organization. No increase above $1/task is authorized by this setup; ask the operator before changing limits.

## Credentials and environment

The integration reuses the CLI's owner-only profile at `$XDG_CONFIG_HOME/vetta/config.json` or `~/.config/vetta/config.json`. It also accepts `NAIVE_API_KEY` from a server environment. `VETTA_PROFILE` selects a saved profile. `NAIVE_BASE_URL` overrides the API origin and must use HTTPS. `PLAYLATTICE_URL` selects the local evidence API (default `http://127.0.0.1:8000`). None of these values belongs in frontend code.

Rotate a key that was shared in chat, then run `login` again with the replacement. Do not commit a credential file. The scripts redact credential-like error text; diagnostic commands never print the SDK client's configuration or authorization headers.

## Files and spending evidence

The approved agent definition is in `src/agent-spec.ts`. `src/onboard.ts` requires an active plan, positive balance and a valid model before creating the single agent; existing agents with different settings are left unchanged. A stable create idempotency key protects against ambiguous retries. All other organization's agents remain untouched.

Ignored local state is under `data/naive/`:

- `agent.json`: created agent ID, verified settings and baseline account metadata.
- `smoke-latest.json`: session ID, assistant result, server usage and balance snapshots.
- `spend-latest.json` / `spend-history.jsonl`: timestamped server-reported balance and spend.
- `monitor-health.json`: success/failure and time of the latest monitoring attempt. Always inspect timestamps; a stale snapshot is not a fresh balance.

Read-only requests retry capacity/rate-limit/server/network failures within a bounded policy and honor bounded Retry-After delays. Writes are not automatically retried. The smoke uses a stable key tied to the local job, checks that agent limits remain unchanged, and records the server-reported spend. Only assistant output is considered a response; a user-message echo cannot make the smoke pass. The evidence reader skips games whose latest analysis is failed/cancelled instead of mixing an older job with current-game clip routes.

## Verification and sources

Typecheck and five focused tests pass (read retry, no implicit write replay, HTTPS credential transport, error redaction, and current-job evidence scoping). A real local API read and the paid SDK smoke were also executed.

Source of truth: installed SDK types/source, live `/v1/openapi.json`, [SDK reference](https://usenaive.ai/docs/sdk/typescript), [onboarding instructions](https://usenaive.ai/skill.md), [CLI reference](https://usenaive.ai/docs/cli/naive). This setup uses the SDK directly for an existing project; it does not clone/deploy a media blueprint or run `naive up` over the existing app.

### Existing sports crew

Use the existing CEO, scout, clipper, caption-editor, channel-manager and analyst; IDs and ingress/egress mapping live in `src/crew.ts`. The onboarding curator is separate and is not dispatched by this path.

```sh
npm run crew:check
npm run crew:check -- --evidence
```

Both commands are read-only. The second also reads completed local analysis and prints a metadata handoff preview; it does not inspect video, create sessions or publish. Missing members or CEO roster drift fail validation. Existing account budgets are reported, not changed. Dispatch is disabled until scoped connectivity, cancellation and the approved $1/task and $10/month integration limits are enforced.

### Analysis-to-crew dispatcher

```sh
npm run crew:configure
npm run crew:watch
# Explicitly process one already-completed game for an integration test:
npm run crew:watch -- --game GAME_ID
```

Configuration creates dedicated versions on the **existing six agent IDs**, then restores each agent's normal default. It preserves budgets and records a private recovery journal under `data/naive/crew-versions.json`. The integration coordinator pins the dedicated member versions. These versions deny external tools and publishing; only the CEO can delegate to its roster. A partially applied update is recovered on the next configure run, with version checks to avoid overwriting concurrent changes.

The dispatcher runs alongside FastAPI, reads its loopback API, and processes newly completed analyses automatically. First startup skips existing completed games unless `--game` explicitly selects one. It never submits video or starts GPU inference. Only registered source/job-matched, decoded, QA-passed clip metadata is sent to Naive. No names, source URLs, media bytes or tracking IDs are sent. All five specialists participate through waited delegation under one **$1 aggregate root-session cap**. Local SQLite reservations limit dispatcher allocation to **$9 per UTC month**, leaving $1 of the approved $10 for onboarding/diagnostics. This does not cap unrelated company activity or replace server-side billing controls.

The output is an evidence-constrained selection of existing clips plus generic captions, reasons and limitations. This metadata-only stage cannot recognize specific basketball events or establish player identity. Discovery/VLM preprocessing, arbitrary new clip rendering, public publishing and main-dashboard integration are not implemented by this dispatcher.

State and costs survive restarts in `data/naive/analysis-workflows.sqlite3`. Request bodies and idempotency keys are stable across uncertain create responses; no new workflow is created for the same job. Outstanding reservations are retained until remote costs/cancellation are reconciled. Completed output is withheld if evidence changes, any specialist fails, or cancellation wins.

The sidecar listens on **127.0.0.1:8011**:

- `GET /health`
- `GET /workflows` — results, source/job IDs, session links, member statuses and recorded costs
- `POST /workflows/JOB_ID/cancel` with `X-PlayLattice-Local: 1` and no Origin header — optional cancellation during agent work

Cancelling/superseding the local analysis is also detected. The existing UI's CV cancel button does not yet control agent work after CV completes; use the sidecar cancellation route for this stage. Keep the dispatcher process running for automatic completion handoffs; stopping it preserves queued/running work for restart.

### Unlisted publication test

The operator authorized automatic **unlisted-only** publication. Run:

```sh
npm run publish:test
```

This narrow acceptance test takes one selected clip from a completed crew result for the pinned indoor TrackID3x3 sample. It verifies current analysis/clip provenance and the media hash, requires exactly one connected YouTube account, uploads the MP4, posts with explicit `visibility: unlisted`, and reconciles its status and URL. TikTok is excluded. Captions contain only grounded duration/uncertainty and the source's CC BY 4.0 credit. No inference or extra agent sessions are started.

Private `data/naive/publish-test-*.json` journals retain upload/post IDs and stable request bodies. Reruns reconcile existing files/posts, and post creation reuses one idempotency key; ambiguous uploads are reconciled rather than blindly repeated. A post with a reported visibility mismatch is removed. A pending or failed provider response is not reported as successful publication. This is an explicit test command, not a general automatic publisher for all uploaded sources.
