# PlayLattice → Naive → TikTok workflow test

Tested 2026-11-08 UTC. **The analysis and crew handoff pass; final TikTok publication fails upstream.** No live TikTok URL is confirmed. The latest operator scope is TikTok only, public for testing. YouTube is excluded.

## Fresh authorized input through crew output

One attributed TrackID3x3 indoor video was uploaded with `rights_confirmed=true` and `auto_analyze=true`. No Generate click, clip review or approval was required between input and the captioned package. The existing local worker ran once; no inference was rerun to cut, caption or publish.

| Stage | Observed result |
| --- | --- |
| Input | 8.4-second indoor TrackID3x3 sample, SHA-256 `8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad` |
| Game / analysis | `4f65b414bb6f46149e0b10e17efea151` / `dd462eafa4bd40ada68a844eb9e28566` |
| Local analysis | Succeeded in **19.261 seconds**, 34 sampled pose frames, seven decoded QA-passed clips |
| Existing crew | CEO session `ses_0yxwxfwb4tyyqda0g4k849bfhs`; scout, clipper, caption-editor, channel-manager and analyst all ended with `end_turn` |
| Selection | One clip, 1003–8256 ms. All seven candidates overlap, so a nonoverlapping multi-clip set is impossible |
| Caption | “Basketball gameplay · 7.3 seconds. Player identities unverified.” |
| Agent cost | **28,865 micro-USD = $0.028865**, root plus all five members; excludes any separate social-post fees |
| Input-to-package | Approximately 4 minutes 30 seconds (07:06:47.780 → 07:11:17.349 UTC) |

This is metadata-grounded selection and packaging. No VLM inspected footage. Event recognition, verified identity, autonomous source discovery and semantic visual validation are not demonstrated.

## Media and publishing checks

The new selected clip is byte-identical to the previously uploaded verified asset, so its existing Naive file was reused. Its signed URL returned HTTP 200; the downloaded 1,026,022 bytes matched the expected clip hash. A separate upload through the social provider also succeeded and reported video media. The clip is H.264/AAC, 1280×720, approximately 7.257 seconds.

TikTok account `user9775537990834` was connected. A private request was rejected before posting because Naive does not accept TikTok per-post `visibility`. The operator then explicitly authorized public testing. Subsequent requests targeted **only TikTok**, retained source/license credit and grounded captions, and omitted that unsupported field.

| Public attempt | Provider post ID | Final live status |
| --- | --- | --- |
| Stored-file path | `110660eb-1123-4058-bb41-4b422015f094` | `failed`, `error: null` |
| Provider-uploaded media path, after confirming the first attempt failed | `49074585-71db-4356-9edb-01e4f5d56782` | `failed`, `error: null` |

Both were accepted and progressed through scheduled/processing before failing. This differs from the earlier 429 capacity issue. The raw Naive post response contains the same failed status and null error as the SDK; the SDK did not hide an upstream explanation.

The company post list can lag behind live provider status and still show scheduled. The publisher now reads `social.getPost` first, treats live failure as terminal, and requires the exact post ID when looking up a permalink. It does not blindly create another post when a result is uncertain. Further retries are stopped pending provider diagnosis.

Naive support can inspect the two provider post IDs above. One raw status-read request ID is `req_9dh9e5p7rck5rk2m1b0792csqe`.

## Validation and continuation

TypeScript and **28 integration tests** pass, including source/clip provenance, duplicate/overlap rejection, monthly reservations, stable create retries, cancellation races, concurrent performance-job source selection and explicit publishing-target policies. These software checks do not establish successful TikTok delivery.

Private evidence and request journals are in ignored `data/naive/`; no credentials or signed media URLs are committed. The analysis watcher remains active. The publication test command is explicit and currently limited to the attributed indoor sample:

```sh
cd integrations/naive
npm run publish:test -- --job dd462eafa4bd40ada68a844eb9e28566
```

Resolve or expose the provider's TikTok failure before starting a new submission. Do not change accounts, privacy or destinations to work around a rejected post without an operator instruction.
