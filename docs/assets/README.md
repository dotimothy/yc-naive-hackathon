# Dashboard recordings

The root README uses recordings from the running local app, captured on 2026-11-08. These are UI walkthroughs of existing results, not benchmarks or fresh recognition evaluations.

| File | What it shows | Capture |
| --- | --- | --- |
| `film-room.gif` | TrackID3x3 outdoor replay with actual model overlays, player table, candidate review, and clip tabs | 72 frames, 6 fps, 12-second loop |
| `naive-crew.gif` | Batch overview, AgentMail/outreach status, workflow stages, crew activity table, and batch video outcomes | 60 frames, 5 fps, 12-second loop |

Both were captured with headless Chrome via Playwright at a 1440×1000 viewport and scaled to 1120×778 using FFmpeg with a 112-color palette. Screenshots were sampled while navigating the UI, then evenly timed for GIF playback. Their playback speed is not the model inference rate or a measurement of job latency.

Only playback, scrolling, tab selection, and opening a workflow-stage disclosure were performed. No analysis jobs, review decisions, messages, or social posts were created or changed for these recordings. The Film Room uses the sample's existing persisted analysis; its job report may reflect historical model settings. The Naive recording shows the actual cancelled-batch/posting-off state, with completed agent sessions labeled as historical. AgentMail displays actual worker/request counters, not a simulated conversation.

The committed artifacts are the two GIFs. Temporary screenshots and capture scripts are excluded from Git. These recordings do not expose credentials, private email bodies, or local source paths.

## Footage and attribution

Film Room footage: Kazuhiro Yamada et al., **TrackID3x3: A Dataset for 3x3 Basketball Player Tracking and Identification**, [official repository](https://github.com/open-starlab/TrackID3x3), [paper](https://arxiv.org/abs/2503.18282). Dataset videos and annotations are [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) per the pinned upstream README.

The app sample excerpts the first 12 seconds of outdoor `IMG_0104.MOV`, resizes it to 1280×720, and removes audio. The recording adds the dashboard and prediction overlays, then scales and reduces colors for GIF display. Player labels are provisional; no author endorsement is implied. See [dataset provenance](../datasets/TRACKID3X3.md).

The Naive dashboard includes small source thumbnails from the existing supplied-video library and posted-video catalog. These are UI records, not additional recognition validation or claims about participant identities.

## Other website recordings

- The earlier 2026-11-07 dashboard tour remains in [`docs/html/assets/dashboard.gif`](../html/assets/dashboard.gif), used by the project website. It was captured at 1440×1100, 60 frames, 6 fps, scaled to 1120 pixels wide with a 128-color palette. It uses the same TrackID3x3 source and transformations above. The duplicate original formerly in this directory has been removed.
- The README's expandable portrait-quiz example reuses [`tiktok-quiz-preview.gif`](../html/assets/tiktok-quiz-preview.gif). It records an earlier published format rather than the latest shot-quiz design. Its source credit and original-post link appear alongside it in the README and in the [website asset notes](../html/README.md#tiktok-visual-fallback).
- The final product demo has its own [capture and source notes](../html/assets/DEMO.md).
