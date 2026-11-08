# PlayLattice → Naive → TikTok workflow acceptance

**The current attributed test-source workflow passes through public TikTok delivery.** The existing CEO and five specialists select/package the local analysis, and a deterministic publisher prepares and delivers the clip without asking for approval.

Published video: https://www.tiktok.com/@playlatticeai/video/7705703545487527182

Naive provider post: `5c2a8c1f-c579-4758-87d5-54c26a7ebc3c`. The provider reports `posted`; TikTok's public oEmbed endpoint also returned a valid embed. Account: `@playlatticeai` (reconnected account ID `4db506ad-a0b6-422d-99fa-7c556d40ace6`).

## Observed stages

One attributed TrackID3x3 indoor video was uploaded with `rights_confirmed=true` and `auto_analyze=true`. No Generate click, clip review or approval was required between input and the captioned package. The existing local worker ran once. Publishing conversion did not rerun inference.

| Stage | Observed result |
| --- | --- |
| Input | 8.4-second indoor TrackID3x3 sample; SHA-256 `8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad` |
| Game / analysis | `4f65b414bb6f46149e0b10e17efea151` / `dd462eafa4bd40ada68a844eb9e28566` |
| Local analysis | Succeeded in **19.261 seconds**, 34 sampled pose frames and seven decoded QA-passed clips |
| Existing crew | CEO `ses_0yxwxfwb4tyyqda0g4k849bfhs`; scout, clipper, caption-editor, channel-manager and analyst all finished with `end_turn` |
| Selection | One clip, 1003–8256 ms; the seven candidates overlap, so a nonoverlapping multi-clip set is impossible |
| Caption | “Basketball gameplay · 7.3 seconds. Player identities unverified.” plus source/license credit |
| Agent spend | **28,865 micro-USD = $0.028865**, root plus five members; separate from publishing charges |
| Input-to-package | Approximately 4 minutes 30 seconds, 07:06:47.780 → 07:11:17.349 UTC |
| Publishing preparation | Original 19.98 FPS → **30 FPS**, H.264/yuv420p, AAC, source dimensions and duration preserved within 120 ms |
| Media integrity | Input hash checked; derivative independently probed and fully decoded; immutable preparation receipt retained |
| Delivery | Autonomous publisher uploaded the derivative, submitted to TikTok only, reconciled the live status and verified public embed access |

This demonstrates metadata-grounded selection and packaging. No VLM inspected footage. Semantic event recognition, verified athlete identity and autonomous source discovery remain outside this acceptance.

## Format problem found and corrected

The original file and the provider-hosted copy were both **19.98 FPS** (`999/50`). [TikTok's Content Posting media requirements](https://developers.tiktok.com/docs/en/content-posting-api-media-transfer-guide) require **23–60 FPS**. The old asset therefore violated a documented requirement. [TikTok's status reference](https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status) lists `frame_rate_check_failed` as a possible rejection reason.

The publishing-only profile `tiktok-h264-30fps-v1` now creates a 30 FPS derivative and validates codec, pixel format, dimensions, frame rate, duration, hash and complete decoding. Frame duplication preserves the timeline; it does not invent intermediate motion. The original analysis/source media stay unchanged. Prepared derivative SHA-256: `23ff32ccc7ded71ef0062a67308cf95a799d3365f81c9f69f03dd6fa53e2aa6c`.

Publication succeeded with the compliant derivative. Naive never exposed the original rejection reason, so the FPS violation is established but cannot be proven to be the only upstream issue. Bundle.social documents per-platform `errors.TIKTOK` and `errorsVerbose.TIKTOK`; Naive's raw response exposed only `failed` and `error: null`. Passing those upstream diagnostic fields through would improve future investigations.

Earlier failed receipts are retained privately. They include stored-file and provider-uploaded-media attempts and retries before/after account reconnection. They have not been relabeled as successful.

## Autonomous operation and limits

All six current Naive crew roles received the operator's unattended-work instruction. Their normal defaults no longer offer operator-question tools or independent posting tools for this workflow. The integration continues using pinned metadata specialists; the channel-manager owns the package, while the local publisher alone commits delivery. This prevents competing agent posts.

Run both local processes:

```sh
cd integrations/naive
npm run crew:watch
npm run publish:watch
```

The private `data/naive/publishing-policy.json` explicitly enables public TikTok publication for newly completed authorized test-source workflows. Existing jobs are skipped unless specifically included. The initial policy includes the format-corrected acceptance job above. No YouTube publication is enabled.

Source scope is still the attributed indoor TrackID3x3 sample. This is not a general publisher for arbitrary footage or a VLM discovery system. Source credit is carried into posts. An unsupported source, uncertain media state, budget refusal or terminal provider failure yields a machine-readable result rather than an approval question or an endless retry loop.

Each prepared asset has a stable hash/profile and upload/post journal. The publisher reconciles existing deliveries before sending and reads live provider status, since the company post list may lag. A successful post retains its exact post ID, permalink and public-access verification. The autonomous queue skips terminal receipts, preventing another post on its next tick.

Root model/delegation tasks now request a **$0.95 ceiling**, leaving room under the approved $1/video allocation for the documented $0.013 non-X post fee. The existing $9/month dispatcher allocation leaves $1 for onboarding/diagnostics. Publishing reserves its fee transactionally and idempotently in the same local allocation. Failed/uncertain billing is treated conservatively; these reservations are not a claim about settled charges for earlier failed experiments. Naive server-side session caps remain in force.

Optional cancellation is checked before upload/post writes. The sidecar cancellation endpoint can flag an active publication. Once a post has been submitted externally, its state must be reconciled; stopping a local process does not prove it was cancelled on TikTok.

## Validation

TypeScript and **35 integration tests** pass, including actual low-FPS → 30 FPS encoding/full decode/cache verification; rejected FPS/dimensions/codecs; automatic queue handoff; no older-job backfill; terminal-failure suppression; source/clip provenance; duplicates/overlap; budget reservations; stable create retries; cancellation races; and publishing-target policies.

Private request journals, the source/derivative media and signed URLs remain in ignored `data/naive/`. No keys or signed URLs are committed. Developer benchmark evaluation and this acceptance test are separate from any human approval step in the product workflow.
