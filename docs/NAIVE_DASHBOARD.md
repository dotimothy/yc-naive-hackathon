# Naive Crew dashboard

The **Naive Crew** tab shows the local integration's current batch, crew activity, AgentMail intake, and publishing state. It is served by the same app as the Film Room.

## What the dashboard reports

- **Data feed:** whether the browser is receiving snapshots. A live feed does not mean posting is enabled or every job is healthy.
- **Batch progress and workflow stages:** recorded outcomes and each video's last reported processing step. A cancelled batch's counts are historical; separate crew activity may still be running.
- **Crew activity:** current sessions plus a recent historical run, with agent state, assigned work, latest action, tools, timestamps, messages and handoffs. Expand a row to inspect the available event timeline. Older history is bounded and completed runs are labeled as historical.
- **AgentMail / outreach:** worker status, message/request counts, delivery state, and the customer-support agent's recorded sessions. Outreach is separate from the five required video-analysis specialists.
- **Workflow history and budget:** persisted results, preview/post links, publication errors, and locally accounted spending, including standalone tasks.

Missing data is displayed as unavailable rather than inferred. Agent state is based on Naive's actual session and event records; internal reasoning is not displayed. Metadata-only crew runs are not visual verification of footage.

## Controls

**Clear Warnings** hides the current status banners in that browser. Error history remains available, and changed warning content appears again. **Show Warnings** restores dismissed banners.

**Cancel Batch** stops queued work, requests cancellation of active analysis/crew work, disables posting, and prevents the recovery worker from restarting the cancelled batch. Cancellation does not remove completed results or existing social posts. A submitted external action may finish before cancellation takes effect.

Cancelled entries can be removed from the batch list without deleting their source videos or analysis artifacts. Existing request identity and delivery journals are retained to prevent duplicate work or posts.

## Services

| Service | Default local port | Purpose |
| --- | --- | --- |
| FastAPI | 8000 | Film Room API, built UI, and `/naive-api` forwarding |
| Vite | 5173 | UI development and API forwarding |
| Naive integration | 8011 | `/pipeline`, session activity, previews, and batch controls |
| Sharing gateway | 8002 | Shared entry point to the development UI and local backend |

The dashboard refreshes about every four seconds, and less frequently in a background browser tab. A stale/offline indicator preserves the last known snapshot rather than presenting old data as current.

If `/naive-api/pipeline` returns 404, verify that the backend or development proxy has the integration forwarding route. If it returns 503, check that the integration process is running. The project website on GitHub Pages is static and does not host these services.

## Implementation and checks

- UI: `frontend/src/NaiveDashboard.tsx`, `NaiveTeamActivity.tsx`, and `NaiveDashboard.css`.
- Feed and activity: `integrations/naive/src/pipeline-http.ts`, `team-activity.ts`, and `mailroom-status.ts`.
- Batch controls: `integrations/naive/src/batch-control.ts`.
- [Integration startup and checks](../integrations/naive/README.md), [AgentMail worker](../integrations/agentmail/README.md), and [API contract](API_CONTRACT.md).

Runtime state, private session records, credentials, media, and recovery snapshots belong in ignored local storage, not in the public repository.
