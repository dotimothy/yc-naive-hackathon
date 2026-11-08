# YOLO26n pose migration validation

The production adapter changes from Torchvision Keypoint R-CNN to `yolo26n-pose`.
This comparison keeps source frames, matching and association fixed. Configuration
was selected on development footage before the measured held-out regression run.

**The requested model runs on MPS and is faster to infer, but player-box coverage
is lower on the indoor regression footage.** It is not a quality-preserving
replacement at the measured operating point. Reviewer attribution remains required.

## Reproduce

```sh
PYTORCH_ENABLE_MPS_FALLBACK=0 .venv/bin/python scripts/validate_pose_migration.py --device mps
```

The script reads the frozen TrackID3x3 manifest and historical Keypoint R-CNN
caches, verifies video/annotation hashes, and runs YOLO on exactly the same
source-frame indices, including zero-detection frames. New ignored artifacts go
to `data/pose_migration/yolo26n_1280/`; historical caches/media are unchanged.

Frozen settings:

- Historical R-CNN confidence0.75 versus YOLO production confidence0.25. Confidence
  is not calibrated across detectors; this compares their production operating points.
- YOLO image size1280, rectangular letterbox, max30 detections, NMS-free end-to-end
  head and float32. Actual predictor settings are recorded per sequence.
- Hungarian box matching at IoU≥0.5; MOT frame=source index+1.
- ContinuityTracker and v1 PlayerIdentityTracker, appearance descriptors enabled,
  `reconnection="review"`; no automatic cross-track merging.
- Dev: S1T1/S2T6. Previously held-out regression: S3T4/S4T2/S6T2. These clips have
  already been observed in earlier reviews, so future tuning needs fresh sessions.

## Measured results

Five sequences supplied252 exact sampled frames. YOLO synchronized inference
finished in12.805 seconds (19.68 sampled frames/second), excluding video decoding,
identity evaluation and model load. CPU fallback was explicitly disabled. The old
caches were generated on CPU, so their timing is not a same-device speed baseline.
See the application benchmark for measured end-to-end savings.

| Split | Detector | Matched / GT | Box coverage | Mixed player IDs | Contaminated observations | Identity purity |
| --- | --- | --- | --- | --- | --- | --- |
| dev | cached R-CNN |460/528|87.12%|6|8|98.26%|
| dev | YOLO26n pose |247/528|46.78%|3|3|98.79%|
| held-out regression | cached R-CNN |828/984|84.15%|8|12|98.55%|
| held-out regression | YOLO26n pose |535/984|54.37%|5|9|98.32%|

Per-sequence box coverage:

| Sequence | Cached R-CNN | YOLO26n pose |
| --- | --- | --- |
| S1T1 (dev) |89.22%|52.45%|
| S2T6 (dev) |85.80%|43.21%|
| S3T4 (regression) |82.74%|52.98%|
| S4T2 (regression) |84.03%|44.79%|
| S6T2 (regression) |85.56%|63.33%|

Held-out player-ID switches fell108→89 and fragments102→87, but many observations
are missing: coverage dropped29.78 percentage points. Lower switch/mixed-ID counts
therefore do not establish an identity-quality improvement. Contamination remained
8 observations on S6T2 under both detectors, while detection coverage decreased.

## Limits and artifacts

Box annotations cannot validate pose keypoint accuracy. The custom association
metrics are not IDF1/HOTA. Unannotated referees, staff and spectators mean unmatched
predictions cannot all be called detector mistakes. Identity purity is conditional
on matched observations and must be read together with box coverage.

The results apply to five clips from one indoor venue, not general basketball or
other sports. The outdoor sample has no identity ground truth. Keep source-review
and correction controls active for stats and player-attributed clips.

Local ignored evidence:

- `data/pose_migration/yolo26n_1280/report.json`
- Per-sequence `*_detections.json` and `*_comparison.json` in that folder.
- `data/pose_migration-validation-1280.log`

An earlier process imported model configuration before the image-size update
completed. Its results under `data/pose_migration/yolo26n/` are unconfirmed and are
excluded from this comparison. The corrected run records actual predictor size1280
for all five sequences; no threshold or association change was made after seeing
held-out results.

## Application integration verification

The12.012-second outdoor source completed a unified run with both models:72 pose
frames,180 ball frames,381 person observations and6 source-verified review clips.
Pose inference took5.84 seconds (12.34 sampled frames/s), versus14.62 seconds in
the earlier R-CNN run on this source. Ball detection remained108/180 sampled
frames. Total wall time was93.16 seconds; another longer analysis was using the
same GPU concurrently, so this run cannot establish an end-to-end speedup.

A real CPU smoke on the authorized0.6-second excerpt also completed with both
models (6.96 seconds total,0.46 seconds pose inference). Browser checks verified
pose dots, ball/hoop overlay, playable source-linked clips and mobile layout.
The backend suite passes112 tests; Ruff and the frontend build pass. Dependency
pins were updated to NumPy1.26.4/OpenCV4.11 for this Python3.9/macOS Ultralytics
release, and both core and ball environments pass dependency checks.

Older API processes can finish current work before an idle reload. The per-job
worker normalizes unversioned requests to YOLO's0.25 default; versioned requests
preserve explicit thresholds. Historical reports/tracks remain unchanged. Future
model or operating-point tuning requires development footage followed by new,
previously unused validation sessions.
