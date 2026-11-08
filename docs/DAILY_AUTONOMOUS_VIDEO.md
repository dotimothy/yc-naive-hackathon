# Daily autonomous basketball workflow

PlayLattice discovers up to five new basketball videos per day, selects a bounded gameplay excerpt, submits it to local analysis, and exports captioned portrait content. The day boundary is America/Los_Angeles. This workflow has its own policy and state and does not resume the canceled overnight batch.

The existing Scout searches for reusable footage. The runner independently validates source metadata and reuse evidence, bounds download size and duration, checks source duplication, and selects a suitable excerpt before upload. Unsupported or inaccessible sources are skipped. Camera claims must match inspected footage; a low-motion excerpt does not make a handheld source a stationary full-court recording.

Local analysis runs through the existing API and Naive crew. Final exports alternate between prediction quizzes and captioned storytelling. Stories describe measured evidence in setup, observation, and takeaway beats; they do not invent player names, made shots, official stats, or intent. Tracking and continuity gates still apply. A day can finish with fewer than five usable outputs when sources, quality checks, services, or the approved budget prevent completion.

## Operation

Private configuration: `data/naive/daily-discovery-policy.json`.
Private persistent state: `data/naive/daily-discovery-state.json`.

```sh
cd integrations/naive
node node_modules/tsx/dist/cli.mjs src/daily-discovery.ts --status
node node_modules/tsx/dist/cli.mjs src/daily-discovery.ts --once
```

The per-user macOS launch agent runs one bounded tick every five minutes. State persists between ticks and across restarts; launchd prevents overlapping instances of this job. The runner also protects its state with a process lock.

```sh
python3 integrations/naive/scripts/install_daily_discovery.py --install
python3 integrations/naive/scripts/install_daily_discovery.py --uninstall
```

The Mac must be awake and signed in, and the local API and crew dispatcher must be available. The installed job does not host those services. Logs are `data/naive/daily-discovery.stdout.log` and `daily-discovery.stderr.log`. Set the policy's `enabled` to `false` to pause new work. Individual analyses can still be canceled from the app.

## Costs and publication

Scout tasks and downstream Naive analysis use the existing budget ledger: $9 monthly integration allocation and $1 per-video ceiling. These are intentionally lower than the account balance. Reservations and actual consumption remain visible through the existing workflow budget reporting. The runner stops initiating paid work when the allocation cannot cover it.

This discovery workflow creates local outputs. It does not change the existing TikTok publication policy or reconnect accounts. Previously canceled jobs stay canceled. A social-provider error must never be represented as a published video.
