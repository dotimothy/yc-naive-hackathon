# Portrait Replays and Clipper Quizzes

The existing Naive clipper selects quiz moments and original countdown music. It does not require a new agent or human approval. The operator chose to keep the earlier public TikTok post; preparing this upgraded sample did not create another post.

## Validated sample

- Job: `dd462eafa4bd40ada68a844eb9e28566`.
- Preview: http://127.0.0.1:8011/previews/dd462eafa4bd40ada68a844eb9e28566
- Format: 720×1280, 30 FPS, H.264 and AAC; full decode checked.
- Four focused track replays, two prediction questions, clipper-selected **Suspense Pulse** music.
- Quiz session: `ses_g23s06sd8t2arbdnv52db61a05`, existing clipper version 9, cost $0.002086.
- Captions, full-court inset, provisional labels, tracked-player crop and five-second choice countdown are burned into the export.
- Interactive preview pauses until a choice, then resumes the action and reveals the answer. Exported TikTok video uses timed choices.

## Grounding and limits

Questions are validated against later observations of the same track in the same camera shot. Ambiguous movement, gaps and uncertain evidence are skipped. Labels identify provisional tracks, not verified unique athletes. Captions describe measured movement and poses; they do not assert possession, made shots or official statistics. Low-resolution source crops retain the source's quality limits.

Music is an original procedural composition selected from three cues. It is mixed only during quiz holds and fades before gameplay resumes. The requested television theme is not included; the latest operator instruction permits clipper-selected music.

## Implementation and operations

`portrait-export.ts` freezes source-bound analysis, calls `clipper-quizzes.ts`, generates the selected cue and runs `scripts/portrait_export.py`. `workers/render/portrait.py` renders replays and quizzes; `workers/render/quiz_music.py` supplies original cues. Source hashes, selected candidate IDs, music hashes and renderer hashes bind cached assets. Private state and rendered media remain under ignored `data/naive/`.

The existing local publisher alone delivers social posts after automatic checks. Account and public-post authorization are unchanged. Unknown source attribution still blocks delivery; current automatic source scope remains the known attributed TrackID3x3 indoor sample. Quiz spend is reserved and settled within existing per-video and monthly limits. `--prepare-only` may call the clipper but never uploads or posts.

Validation: 38 TypeScript tests, 9 Python renderer/music tests, typecheck and Ruff. Real source render and headless browser checks passed, including music playback during choices and silence after the fade before reveal. Existing live TikTok post remains in place.
