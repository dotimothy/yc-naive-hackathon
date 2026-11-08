# Dashboard capture

`dashboard.gif` records the running PlayLattice dashboard on 2026-11-07, after the
tabbed film-room and player-table update. It shows outdoor sample replay
with actual sampled tracking/pose and ball/hoop overlays, followed by the player
table and candidate review tabs. No stats or identities were fabricated and
no review decisions were changed for the recording. The outdoor game uses its
existing persisted analysis, so the job panel shows that historical run's model
and timings; it is not a fresh benchmark of the currently configured YOLO26 pose model.

Capture: headless Chrome via Playwright, 1440×1100 viewport, 60 PNG screenshots;
converted with FFmpeg to a looping GIF at 6 fps, scaled to 1120 pixels wide with a
shared 128-colour palette. The GIF is a dashboard tour, not an inference-speed or
frame-rate benchmark. Only the GIF is committed; temporary screenshots and raw
videos remain in ignored `data/`.

Footage: Kazuhiro Yamada et al., **TrackID3x3: A Dataset for 3x3 Basketball Player
Tracking and Identification**, [official repository](https://github.com/open-starlab/TrackID3x3),
[paper](https://arxiv.org/abs/2503.18282). Dataset videos and annotations are
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) per the pinned upstream
README. The app sample excerpts the first 12 seconds of outdoor `IMG_0104.MOV`,
resizes to 1280×720 and removes audio. This recording adds the dashboard and model
prediction overlays, then scales and reduces colours for GIF display. No author
endorsement is implied. See [dataset provenance](../datasets/TRACKID3X3.md).
