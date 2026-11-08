# YOLO26-family ball/hoop candidate: verification and limits

This experiment is archived in `develop` for reproducibility. Its code is not
wired into production analysis; RF-DETR and the existing three-view policy remain active.

This is an experimental adapter, not an accepted production replacement for the
basketball-trained RF-DETR model. It uses the official YOLOE-26 Nano prompt-free
segmentation model; it is not a basketball-fine-tuned YOLO26n detector.

## Pinned official candidate

- File: `yoloe-26n-seg-pf.pt`, Ultralytics assets release `v8.4.0`.
- Size:16,271,345 bytes.
- SHA-256:`ae805a9ac11c85214588b32da8248d68a516e1d071465e1e86a03cbfadb66ba7`.
- Actual checkpoint architecture: `YOLOESegModel`, head `YOLOESegment26`, YAML `yoloe-26n-seg.yaml`.
- Actual4585-class vocabulary includes source310=`basketball` and315=`basketball hoop`.
- Adapter outputs normalized class0=`basketball`,1=`hoop`, preserving original `source_class_id`.
- License: AGPL-3.0 / Ultralytics Enterprise.

The [official YOLOE documentation](https://docs.ultralytics.com/models/yoloe/)
describes prompt-free inference using the built-in RAM++ vocabulary, without a
text encoder. The checkpoint rejects `set_classes()`; the adapter filters its
existing exact labels rather than inventing a hoop label. The official
[RAM++ vocabulary](https://github.com/xinyu1205/recognize-anything/blob/main/ram/data/ram_tag_list.txt)
contains the two target names. Source download is the hash-pinned
[official asset](https://github.com/ultralytics/assets/releases/download/v8.4.0/yoloe-26n-seg-pf.pt).

The standard prediction path still computes segmentation masks, although the app
only consumes boxes. Native MPS output/device checks and disabled CPU fallback
are tested separately from detection quality. Decoder/preprocessing/serialization
remain CPU operations.

## Local interface

```sh
.venv/bin/python scripts/download_yolo_basketball_model.py
```

```python
from workers.video.yolo_basketball_detector import YoloBasketballDetector
model = YoloBasketballDetector('data/models', device='mps')
observations = model.detect(frame_bgr, source_timestamp_ms)
```

The constructor matches the existing detector contract. Full-frame1280 is the
default; `tiled=True` explicitly enables the full frame plus two60%-width crops.
Default thresholds are ball0.25/hoop0.35, float32, `nms=False`, `quantize=None`.
Checkpoint hash, source-label vocabulary and architecture are verified before
predictions are exposed. Offline mode and disabled telemetry are configured;
there is no image upload, hosted inference, text prompting, encoder download,
training, dependency installation or invented trajectory interpolation.

Each observation contains `class_name`, normalized `class_id`, original
`source_class_id`, detector confidence, normalized `bbox` in xywh, source
milliseconds, origin and `verified:false`. Made/missed outcomes remain unknown.
The detector exposes synchronized `last_inference_seconds` and separately
measured `last_transfer_seconds`; manifest records actual architecture/view mode.

## Local acceptance result: rejected

Native MPS inference completed with `PYTORCH_ENABLE_MPS_FALLBACK=0`, full-frame
1280 and frozen ball0.25/hoop0.35 thresholds. This confirms execution support,
but the detector failed the required basketball recognition check.

| Footage | Sampled frames | YOLOE ball frames | YOLOE hoop frames | Current RF-DETR ball/hoop frames | YOLOE inference / wall seconds |
| --- | ---: | ---: | ---: | --- | --- |
| Outdoor stationary12s | 180 | 0 | 0 | 108 / 180 | 19.87 / 20.33 |
| Indoor stationary8.4s | 84 | 0 | 0 | 56 / 84 | 8.28 / 8.36 |

Frame coverage is a diagnostic, not labeled precision or recall. No thresholds
were tuned on the indoor footage. Initial full-frame cold inference took2.47s;
warm first-frame calls took0.105–0.116s. Both returned no target observations.

Unfiltered first-frame predictions on both sources returned global vocabulary
IDs, including `person`2163, but no basketball, basketball backboard or basketball
hoop. This rules out normalized-ID remapping as the cause of the empty adapter
outputs on these frames. It does not establish that other inference settings
could never improve the model. Outdoor-only three-view probes at0/4/8s also
returned no targets, with13.32/44.87/49.78s inference latency. Cropping is therefore
not an accepted recovery path and its cost must not be inferred from full-frame
timings.

Ignored local evidence: `data/yolo_ball_smoke.json`,
`data/yolo_ball_validation/report.json`, per-source observation files and
`data/yolo_ball_validation/unfiltered_and_tiled_smoke.json`. Keep the current
basketball-trained detector as the production default. The adapter is an
explicit experimental option pending a suitable trained checkpoint and labeled
held-out validation.

## Other investigated candidates

[446f6e6e79/YOLO-basketball-fineTuned](https://huggingface.co/446f6e6e79/YOLO-basketball-fineTuned)
provides actual YOLO26m weights, not Nano. Pinned revision
`8d30eddfcc47728f73de9beda09291854f7970f2` has `best.pt` equal to
`v1-yolo26m_finetuned.pt`,44,124,057 bytes, SHA-256
`129fa18d0811ed2ed3d0529e73b59e405f900034fc14b5a2090e8c17cc427162`.
Static checkpoint inspection confirms `yolo26m.yaml` and classes `Ball`, named
jerseys and referees, with no hoop class. The model card declares MIT and notes
single-game training/generalization limits. It cannot replace both ball and hoop
detection. No untrusted checkpoint code was executed during that inspection.

[Comets Basketball/hoop-6mtlz](https://universe.roboflow.com/comets-basketball/hoop-6mtlz)
advertises a YOLO26 Fast/Nano model with person, basketball, backboard and hoop
classes. The public page declares CC BY4.0 for its dataset and offers API-key
hosted usage. I could not verify an anonymous downloadable weights URL, weight
hash/size, or a distinct weights license. Therefore it is not a verified offline
checkpoint for this integration; no signup or hosted prediction was attempted.

No locally downloadable, provenance-verified basketball-trained YOLO26n checkpoint
covering both ball and hoop was verified in this search. This is a search result,
not proof that no such checkpoint exists.
