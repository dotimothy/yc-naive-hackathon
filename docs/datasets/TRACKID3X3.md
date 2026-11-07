# TrackID3x3: first local dataset

Source: https://github.com/open-starlab/TrackID3x3
Pinned revision: `9822a3dde59fc80fbb5eaacd443e79e8b059d94e` (inspected 2026-11-07).
Authors: Kazuhiro Yamada et al., “TrackID3x3: A Dataset for 3x3 Basketball Player Tracking and Identification”, https://arxiv.org/abs/2503.18282.

## Actual analysis

Run from the repository root (Python standard library only):

```sh
python3 scripts/analyze_trackid3x3.py
open data/trackid3x3/trajectory_preview.svg
```

The script downloads only the 42 indoor MOT annotation files from the pinned upstream revision. Downloads and generated artifacts stay in ignored `data/`. Each file is hashed; `report.json` includes frame coverage, box counts, tracks, and gaps. The trajectory preview uses annotated bounding-box bottom centers in image coordinates, not calibrated court coordinates or predictions.

Measured annotation inventory: **42 sequences, 7,534 annotated frames, 45,204 boxes**. First sample `basket_S1T1_pre.txt`: 168 frames, 1,008 boxes, six track IDs, no missing frames or gaps within those tracks. This is ground-truth coverage analysis, not tracking accuracy or proof that inferred identities are correct.

Observed MOT rows have ten comma-separated columns: frame, track ID, x, y, width, height and four auxiliary values. First sample frames start at 1; its auxiliary values are -1. Do not interpret those values as model confidence. Bounding boxes are pixel coordinates; video dimensions and FPS must be probed before normalizing or deriving source timestamps. Do not assume pose annotations share this format.

## Matching video

Official dataset folder: https://drive.google.com/drive/folders/1aWqMwQKr5xKMjqms7-raYluSlxPsGvwX
Indoor raw folder: https://drive.google.com/drive/folders/1q0L_Mmf5cwb-Yv51NTjgQcF12Is0fYCB
First video: https://drive.google.com/file/d/1ONVOjVBPmNdpQWQ_9WJCNYte__4NJ5Ol/view

Downloaded sample to `data/trackid3x3/basket_S1T1_pre.mp4`: 1,554,828 bytes; SHA-256 `8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad`. MP4 file signature checked; decoding/alignment checks remain pending.

Match `basket_S1T1_pre.mp4` to `ground_truth/Indoor/MOT/basket_S1T1_pre.txt`. Verify annotation/video frame alignment explicitly; do not assume frame counts or rates match from naming alone. FFmpeg/ffprobe was unavailable in the initial local environment (Python 3.9.6; Node 26.13.0).

## Next executable milestone

1. Install FFmpeg/ffprobe and probe the matching MP4 for dimensions, frame rate, duration and decoded frame count.
2. Overlay ground truth on the matching frames to verify alignment. Ground-truth playback must be visibly labeled.
3. Run a separately licensed pretrained person/pose tracker on the same video. Keep predictions separate from ground truth.
4. Compare boxes, identity continuity and throughput; use a documented matching/evaluation protocol before reporting accuracy. Split by source session before any model tuning to avoid leakage.
5. Feed actual predictions through the shared API contract and render two independently verified clips. A dataset track ID is not a consented athlete profile.

## Terms and scope

The upstream README explicitly lists dataset videos, annotations and intermediates as **CC BY 4.0 unless otherwise stated**. Retain attribution and note transformations. Original code is Apache 2.0; BoT-SORT is MIT; CAMELTrack is Apache 2.0; the bundled jersey-number-pipeline is **CC BY-NC 3.0**. We have not installed or copied those pipelines. Verify model-weight terms separately before selecting an inference model.

The dataset supports indoor/outdoor/drone 3x3 basketball; the README describes six-player boxes and ten pose keypoints on some frames. Our measured findings above cover indoor MOT annotations only. No recruiting identity, athlete-profile consent, event labels, inference FPS or model accuracy has been established by this analysis.
