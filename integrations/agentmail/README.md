# PlayLattice AgentMail intake

An email entry point for existing PlayLattice TikTok posts. It recommends a small catalog of published posts, accepts explicit source clipping requests, queues a local render, and replies with a verified preview link. It does not publish anything to social media or change the Naive posting policy.

The existing connected inbox is **playlattice@agentmail.to**. The AgentMail connector in chat and the unattended local worker are separate authentication contexts. The worker requires its own `AGENTMAIL_API_KEY` environment variable; it cannot reuse a connector token from chat.

## Conversation

Send an email such as:

```text
Recommend indoor basketball videos.
```

The reply contains up to three existing TikTok post URLs and stable `PL-<TikTok video ID>` references. When configured, Naive’s **outreach** agent interprets the customer message and selects from the supplied catalog. It can ask clarification questions and translate explicit natural-language clip requests into structured proposals. If Naive is unavailable, deterministic catalog matching and the CLIP command remain available as a fallback. It does not invent action tags or recognize a player from clothing. Availability is based on local posted receipts, not a fresh TikTok moderation check.

Reply with one selected ID and times in the **original source video**:

```text
CLIP PL-7705723023252376845 START=1 END=5
```

To overlay a temporary player track after confirming it in the film room:

```text
CLIP PL-7705723023252376845 START=1 END=5 TRACK=7
```

Use the actual source/video and track ID shown in your recommendations and film room; the values above illustrate the syntax. Source times are not timestamps in the edited TikTok. Maximum requested clip length is 30 seconds. Five requests per sender per rolling day are allowed. If a track has too many observations, a shorter range is requested. `PLAYER=white shirt` without a confirmed track asks for clarification instead of making an identity claim.

A queued receipt is sent first. The worker then cuts from the registered original source, optionally draws yellow boxes on observed track positions, independently verifies the media, and replies with an opaque link valid for seven days. The app and its shared connection must be online to serve the preview. Source-credit information accompanies the results.

## Inspect without reading or sending email

From the repository root:

```sh
.venv/bin/python -m integrations.agentmail.worker --catalog
```

Only posts with a local `posted` receipt, the expected PlayLattice TikTok URL, a succeeded source job, and registered original media appear in the catalog. Journal and source hashes must match. This mode prints no local source paths or email addresses.

## Activate the worker

Use an API key from the AgentMail account that owns `playlattice@agentmail.to`. Set it securely in the process environment; do not paste it into chat, commit it, or place it in frontend code. One way to enter it without shell history is:

```sh
read -s 'AGENTMAIL_API_KEY?AgentMail API key: '; echo
export AGENTMAIL_API_KEY
export AGENTMAIL_INBOX=playlattice@agentmail.to
export PLAYLATTICE_PUBLIC_ORIGIN=https://playlattice-film-room.doublename.chatgpt.site
.venv/bin/python -m integrations.agentmail.worker --live
```

The `read` command above uses zsh, the project's current host shell. The API, FFmpeg, and registered media must be available. No new Python dependencies are required beyond the repository environment.

The worker verifies inbox access before setting its first intake watermark. It starts with **new mail after activation**, so it does not reply to the historical inbox. It polls every 30 seconds while the process is running. `--once --live` performs one iteration after activation. Only one worker may hold the local lock.

All state lives in ignored `data/agentmail/`: a SQLite inbound ledger, preview request queue, immutable reply outbox, send receipts, and generated preview files. Stop the process with Ctrl-C. Restarting retains deduplication and pending work.

## Delivery and boundaries

- Uses AgentMail's official message list/get/reply endpoints and `Idempotency-Key` on replies. Network retries reuse the exact saved reply and key. An uncertain send older than 23 hours becomes `needs_reconciliation` instead of risking a resend after the provider's idempotency window.
- Replies only to the original From address. It does not reply-all, forward mail, honor arbitrary Reply-To addresses, fetch links from inbound messages, execute mail content, or upload attachments.
- Sent mail, spam/trash, unauthenticated messages, and automatic/list replies are ignored. Pending provider security scans are deferred.
- Commands are parsed from extracted new text; quoted previous messages are removed. Arbitrary email instructions cannot override source selection, clip bounds, storage paths, or publishing policy.
- Preview links are bearer links: recipients can share them. They expire after seven days, carry no sender address, and serve only registered ready previews. There is no public request-list endpoint exposing email content.
- Highlighting follows a supplied temporary track, not a named athlete or jersey inferred from prose. Track IDs remain provisional; boxes appear only where recorded observations exist.
- No automatic sending is started merely by importing the code or running the web app. `--live` and runtime credentials are required.

## Verification

```sh
.venv/bin/python -m pytest backend/tests/test_mailroom.py backend/tests/test_naive_proxy.py -q
```

References: [AgentMail messages](https://www.agentmail.to/docs/messages), [reply endpoint](https://docs.agentmail.to/api-reference/inboxes/messages/reply), [send idempotency](https://www.agentmail.to/docs/knowledge-base/preventing-duplicate-sends).

## Naive outreach team member

`outreach` is a real Naive agent in the CEO’s team, configured by `integrations/naive/src/outreach-configure.ts`. It is separate from the five required video-analysis specialists, so adding customer support does not change the pinned analysis workflow. Its configuration is recorded privately in `data/naive/outreach-agent.json`.

The AgentMail worker invokes `outreach-run.ts` for new eligible customer messages. Outreach receives the message text and public catalog fields, never the AgentMail key, sender address, or local media paths. It returns a bounded structured response: recommendations, clarification, or a proposed source edit. Local code alone sends email, validates video IDs and explicit original-source times, confirms any supplied track number, and queues rendering. Clothing descriptions cannot be converted into a track automatically.

Each email task reserves at most 50,000 micro-USD ($0.05) within the existing shared integration allocation; completed requests settle to measured usage. Saved requests, sessions, fingerprints, and idempotency keys prevent duplicate remote tasks. A provider failure uses local intake instead of inventing an agent reply. Agent sessions do not publish posts or initiate unsolicited emails.

The worker supports `SIGHUP` to reload its code at a loop boundary, retaining its existing process environment without persisting credentials.
