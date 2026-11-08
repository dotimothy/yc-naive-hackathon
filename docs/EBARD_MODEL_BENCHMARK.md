# E-BARD detector comparison — 2026-11-07

Decision: keep the existing RF-DETR Nano ball/hoop detector. The basketball-trained
E-BARD YOLOv8n checkpoint is substantially faster locally but does not preserve
usable ball/hoop detection on our two TrackID3x3 samples. No production model or
confidence threshold was changed.

## Paired local result

| Sample | Processed frames | RF-DETR detector seconds | YOLOv8n detector seconds | RF ball frames | YOLO ball frames | RF hoop frames | YOLO hoop frames |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Outdoor, 12.012 s | 180 | 42.543 | 6.119 | 108 | 5 | 180 | 0 |
| Indoor, 8.408 s | 84 | 19.967 | 3.085 | 56 | 34 | 84 | 0 |
| Total | 264 | 62.510 | 9.203 | 164 | 39 | 264 | 0 |

YOLOv8n was 6.79 times faster for the measured detector calls (85.3% less time).
These are detector timings, not complete analysis job times. Frame coverage is
not accuracy, recall or ground truth. RF-DETR also has false positives; neither
model produces reliable official statistics from these measurements.

The two models agreed on 34 ball observations within a normalized center distance
of 0.015. RF-DETR had 139 unmatched ball observations and YOLO had eight. This is
reference agreement, not an accuracy test. Outdoor ball trajectory segments were
13 versus three; indoor 18 versus 14. Fewer segments here cannot establish better
continuity because YOLO missed so many observations.

Visual inspection of outdoor sheets 0 and 2 and indoor sheet 0 confirmed visible
balls and the active hoop missed by YOLO. The indoor sheet also showed a YOLO ball
false positive on a player's orange shirt. These are qualitative spot checks, not
a fully annotated evaluation. Both samples use stationary cameras, so this does
not establish performance on broadcast footage or unseen camera styles.

## Method and provenance

- Same decoded BGR frame is supplied to both detectors, every second source frame.
- Three views each: full frame and two overlapping crops each 60% of source width.
- Both use 704 input size. YOLO uses square letterboxing (`rect=False`). RF-DETR
  retains its production preprocessing. Identical view regions and target input
  size do not imply identical model-specific image transforms.
- Ball threshold 0.25, hoop 0.35; shared cross-view suppression at IoU 0.5.
  YOLO additionally uses its standard internal NMS at IoU 0.5.
- Three untimed warmup calls per model per source. Execution order alternates by
  frame. Synchronized full detector calls include preprocessing, inference,
  transfer and postprocessing; exclude decode and model loading.
- MPS, float32, automatic MPS operator fallback disabled. Normal CPU preprocessing
  and result serialization remain part of the measured detector call.
- Python 3.9.6, PyTorch 2.8.0, Ultralytics 8.4.207, RF-DETR 1.3.0, macOS arm64.
- RF-DETR uses the existing pinned sanitized checkpoint and manifest validation.
- YOLO source: [E-BARD model repository](https://huggingface.co/GabrieleGiudici/E-BARD-detection-models),
  revision `aae99b56aadd2f1c40f1a402fd465dd666fbc8da`, file `BODD_yolov8n_0001.pt`.
  SHA256 `dfe3534d51bb21024d1a400c37f0c1fbf0c8b96ea9a56a5f3cb5454813bfd641`,
  6,151,282 bytes. Class mapping checked: basketball, hoop, player, referee.
  Repository labels model weights CC BY 4.0; runtime licensing is separate.
- Outdoor source SHA256 `144d758db53298cadb887bcee996284e569a03e45ed93c43265586933dc08b95`.
- Indoor source SHA256 `8af3f89ccd3549f885f24223d6265044965c7a5f5a4d16b773d7b0ae8a85eaad`.

A diagnostic at confidence 0.05 on each clip's first frame produced essentially
identical CPU/MPS confidences (differences below 0.000001). Hoops reached only
0.11845 outdoor and 0.09877 indoor. `rect=True` on the same mixed-view batch made no
difference. This rules out a clear first-frame device discrepancy; it does not
validate every frame or establish that changing thresholds would recover quality.

The author's published NBA test-split results did not transfer to these stationary
3x3 clips. This is evidence against this checkpoint for our current use, not
against YOLO architectures generally.

## Reproduction and artifacts

```sh
.venv-ball/bin/python scripts/benchmark_basketball_models.py \
  data/local/f4adfa16088142bbaa5d2cb4fbc9f9cc.mp4 \
  data/local/f6812246a24d4c09b3d0962357199b82.mp4
```

Run without other GPU workloads. The script downloads the pinned YOLO checkpoint
if absent and verifies its fixed SHA256 before loading. RF-DETR must already be
installed with its existing model manifest. This is an experimental benchmark,
not an application model selector.

Ignored artifacts are in `data/benchmarks/ebard-models/`: aggregate `summary.json`,
per-source `frames.json` (timestamps, frame hashes, observations and timings),
`summary.json`, side-by-side `review-*.jpg` and `sheet-*.jpg`, and diagnostic
`sanity.json`. Raw footage and checkpoints remain outside Git.
