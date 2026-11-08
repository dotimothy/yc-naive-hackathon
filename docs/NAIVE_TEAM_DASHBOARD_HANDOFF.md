# Claude: detailed Naive team activity dashboard

User request: integrate more detailed, live statuses for the Naive team into the existing dashboard. This is an implementation handoff, not a request to resume posting.

Inspect Naive SDK 0.12.3 and available session/event APIs before designing the feed. Show only observable data the service actually exposes. Current roles: CEO/coordinator, scout, clipper, caption-editor, channel-manager, analyst.

For each agent, show its current video/job and task, actual session state, latest reported action, start/last-update timestamps, tool activity, outputs and errors. Distinguish waiting on another agent/provider from working when evidence supports it; show unknown/unavailable when it doesn't. Expand each card into a timestamped activity timeline. Include sender/recipient messages and handoffs only if the API exposes real records. Do not invent conversations, progress percentages or internal reasoning. Preserve stale/offline indicators and avoid describing old completed sessions as current work.

Relevant files:
- frontend/src/NaiveDashboard.tsx and NaiveDashboard.css
- integrations/naive/src/pipeline-http.ts (GET /pipeline via /naive-api)
- integrations/naive/src/analysis-dispatcher.ts, analysis-engine.ts and analysis-store.ts
- integrations/naive/src/crew.ts

Existing workflow records expose member session_id, agent_id, status, stop_reason and cost_micro_usd. The dashboard currently polls every four seconds. Prefer bounded incremental activity fetching, pagination and cached results; don't replay whole transcripts on every poll. Never expose credentials or private local paths.

Preserve the recently added Clear Warnings / Show Warnings and Cancel Batch controls. Warning dismissal persists in browser storage until the warning fingerprint changes. Batch cancellation persists in batch-control.json, disables posting, and requests cancellation of active work; completed results remain. See batch-control.ts and tests.

IMPORTANT: automatic posting and recovery retries are paused at the user's request through November 9, 2026, 8 AM America/Los_Angeles, pending review. Do not enable publication, submit test posts, change credentials, or restart provider recovery for this UI work.

The shared app proxies the Mac: UI Vite 5173, backend 8000, Naive integration 8011, sharing gateway 8002. UI changes refresh automatically through the shared link. Preserve other uncommitted work and coordinate backend/API changes. Validate frontend build and integration typecheck; add meaningful tests for activity mapping, missing/stale data and bounded fetching. Report which activity/message fields are actually available and any limitations.
