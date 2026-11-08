# Overnight Stationary-Camera Drive Queue

The operator authorized public TikTok publication of the portrait quiz videos and overnight processing of the remaining stationary-camera Drive sources on 2026-11-08. The existing account is @playlatticeai. The earlier test post is retained.

## Active run

The private queue contains 111 remaining inputs: 70 LOW/NEXT source clips and 41 additional indoor TrackID3x3 clips. LOW/NEXT media were already cached and analyzed; matching source hashes let the queue reuse that work. The TrackID3x3 files were materialized through the connected Drive plugin. Stationary-camera evidence comes from the source manifests' camera reviews and the fixed-camera dataset. Restricted APIDIS and unrelated broadcast footage are outside this queue.

Each input follows source/hash validation → existing raw clip import → local analysis if needed → existing Naive crew package → player-focused portrait rendering → grounded quiz and original music selection by the existing clipper → TikTok upload and receipt. Cached raw clips do not need another discovery/VLM clipping pass. Analysis and captions retain provisional identities and make no unsupported scoring/outcome claims. Ineligible clips are skipped; provider errors stop that delivery instead of looping duplicate posts.

New work stops at **08:00 America/Los_Angeles on 2026-11-08**, with the publication queue checking the same cutoff. A publication already submitted may finish afterward. Existing integration limits remain $1/video and $9/month, with $1 of the approved $10 allocation reserved for onboarding/diagnostics. Quiz reservations settle to actual costs; posting fees are reserved before submission. The Mac must remain powered and online; caffeinate was started for this run. This is a local detached process, not hosted compute.

## Monitoring

Open the main UI's **Naive Crew** tab: http://127.0.0.1:5173/#naive. It polls actual integration state every four seconds and retains the last snapshot when disconnected. Backend feed: `GET http://127.0.0.1:8011/pipeline`. The feed hides source/render filesystem paths. It reports stages, agent sessions/costs, preview links, skip/failure reasons, spend limits and confirmed TikTok links.

Private ignored state/logs:

- `data/naive/overnight-state.json`: source queue and outcomes.
- `data/naive/publishing-sources.json`: authorized source hashes and actual attribution.
- `data/naive/publishing-policy.json`: deadline and explicit existing-job inclusions.
- `data/naive/overnight-overnight.log`, `publish-watch-overnight.log`, `analysis-dispatcher-overnight.log`.

`integrations/naive/scripts/prepare_overnight.py` rebuilds the queue from verified manifests while preserving recorded progress. `src/overnight.ts` owns a PID lock and processes one source at a time. `src/publish-watch.ts` alone performs social delivery. Restart the queue with `node_modules/.bin/tsx src/overnight.ts` from `integrations/naive`; do not delete journals or reset terminal publication outcomes to retry blindly. Stop its PID from `overnight.lock` with SIGTERM. Disable `publishing-policy.json.enabled` to stop new posts. Source analysis still supports cancellation from the film room.

## Verified posts

- Initial portrait quiz sample: https://www.tiktok.com/@playlatticeai/video/7705720030452911374
- First additional Drive source (LOW-002): https://www.tiktok.com/@playlatticeai/video/7705721629380332814
- Next successful source (LOW-004): https://www.tiktok.com/@playlatticeai/video/7705722419830377742

The live dashboard was checked against the actual 111-item queue in isolated Chrome with no browser exceptions. Integration typecheck, 40 TypeScript tests, 9 Python renderer/music tests and frontend production build passed during this change.
