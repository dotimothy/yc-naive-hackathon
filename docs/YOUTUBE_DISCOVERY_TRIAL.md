# YouTube Discovery and Ingestion Trial

This is one seeded source trial using the existing Scout, followed by direct YouTube retrieval, a bounded excerpt and local analysis. The separate visual redesign uses the existing validated TrackID3x3 sample. No new TikTok post was created and the publishing pause was preserved.

## Review links

- Source trail and three playable analysis clips: http://127.0.0.1:8011/discovery/youtube-demo
- Redesigned cover and interactive quizzes: http://127.0.0.1:8011/previews/dd462eafa4bd40ada68a844eb9e28566

Scout performed two web searches, twelve source reads and ten browser actions. Its first search run reached the iteration limit; a bounded report phase returned one candidate with camera verification explicitly false. Combined Scout spend was $0.057860, within the $0.15 discovery ceiling. Standalone discovery reservations are included in the existing monthly ledger. This remains a seeded pilot; a general recurring discovery-to-ingestion service is still a separate milestone.

Source: https://www.youtube.com/watch?v=7CQ9mAf_B5Y — ČBF 3x3 Tour22.6. Ústí nad Labem, Honza Nový. Historical CC BY3.0 evidence and reviewer confirmation are recorded at https://commons.wikimedia.org/wiki/File:%C4%8CBF_3x3_Tour_22.6._%C3%9Ast%C3%AD_nad_Labem.webm . The YouTube extractor did not expose a current license field; the provenance record preserves that independent evidence rather than inventing one.

Assistant frame inspection selected a nominal13.5–20.25-second interior window between observed camera edits. The derivative is6.773seconds,1280×720; original audio was absent from the selected video-only download. Registered game `bf5926fa6c4a4b91aa0e67ed903dd8e2`, analysis `a74dba10aac947f5953cbd7f04f6d298`, succeeded and yielded three source-bound QA-passed clips. Its selected highlighted-player fragments did not meet the portrait continuity gate, so this source received no prediction-quiz export. The gate was preserved. Private raw media, tool-event logs and hash/time provenance remain in ignored `data/naive/youtube-discovery/`.

The visual update adds tracked-source-frame JPEG covers, stronger crop/framing, larger typography, distinct answer cards, a countdown ring/bar and clear answer reveals. The browser preview uses the generated cover and highlights selected/correct answers. Cover receipts retain the source frame time and track; no people or plays are generated. Existing live TikTok posts are unchanged. The SDK social-post interface does not expose TikTok cover selection; the generated cover is currently used in local previews.

Validation: integration typecheck,51 TypeScript tests,10 Python renderer/music/cover tests, full video decode and real Chrome checks of both pages, including quiz selection/reveal/music and390px layout. The full-video shot heuristic missed some similar-court view edits; the bounded pilot used inspected frame samples. Robust general camera screening and automatic handoff still need implementation.
