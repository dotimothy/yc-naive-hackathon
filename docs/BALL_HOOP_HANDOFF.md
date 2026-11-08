# Ball/hoop detection and shot-candidate prototype

**Current app flow:** [unified analysis](UNIFIED_ANALYSIS.md) supersedes the separate-pass API/UI described below. Use Analyze Footage for both models; the separate POST now returns410.

## Scope and parallel ownership

This branch adds an isolated, runnable video-analysis stage. It does not edit the existing pose pipeline, identity draft, backend or frontend. Claude's separate session `fccb9796` (`scoutfilm-tracking`) owns tracking/team improvements in `/Users/tim/Documents/GitHub/yc-naive-tracking`. Another interactive Claude session was active in the main checkout, so this implementation was developed in `/Users/tim/Documents/GitHub/yc-naive-ball` on `work/ball-hoop`.

## Run

The normal core environment and FFmpeg/ffprobe must already be installed. Set up the optional detector environment once:

```sh
scripts/setup_ball_detector.sh
.venv-ball/bin/python scripts/analyze_basketball.py \
  data/trackid3x3/outdoor_stationary_12s.mp4 \
  --output data/ball_hoop/outdoor
```

Optional `--players path/to/tracks.json` attaches provisional possible-release attribution. A neighboring `report.json` must contain the exact video's `source_sha256`; mismatched evidence is rejected. Use `--device cpu|mps|cuda:N`, `--stride 1–5`, confidence thresholds, and `--limit-seconds` for diagnostics. Default stride is two. `--no-tiles` is faster but performed poorly on the small outdoor hoop. Optional `--hoop x y width height` is an explicitly reviewer-supplied normalized region, never labelled automatic.

Setup uses the same Python version and a read-only path to core packages from a separate `.venv-ball`; it installs detector dependencies there instead of upgrading the working app environment. A standalone deployed worker can instead install core and detector requirements together in its own environment. No hosted inference, API key, Naive SDK or sponsor integration is used.

Outputs: `objects.json` (source-timed model observations), `ball_tracks.json` (short continuous ball segments), `report.json` (source/model provenance, stable hoop regions, proposals, timings), and `observations.jpg` (real frames with model overlays). Everything lives under ignored `data/`; no weights, footage or generated images are committed.

## Model provenance and loader

Use the E-BARD RF-DETR Nano detector, not the YOLO checkpoint. Its architecture is Apache-2.0 RF-DETR; the model author declares weights CC BY 4.0. Credit Gabriele Giudici / E-BARD. Upstream documents NBA broadcast training and weaker generalization to outdoor/lower-tier footage; that is a limitation, not a guarantee about this source.

Pinned model revision `aae99b56aadd2f1c40f1a402fd465dd666fbc8da`, file `BODD_rf-detr-nano_0000/checkpoint_best_total.pth`, SHA-256 `759968ecf8f83663c85de3d881713e072f4a9dd0ea2f136b082878b1e4fe2400` (120,811,190 bytes). The downloader bounds size and verifies this digest. It loads the download with Torch's restricted weights-only loader (allowing only Namespace metadata), extracts tensors into a locally generated state-only checkpoint, and verifies the latter's manifest hash before inference.

The checkpoint has an empty class-name list, so RF-DETR's generic COCO names are incorrect for it. We explicitly use the author's evaluation mapping: **0 basketball, 1 hoop, 2 player, 3 referee**. Constructor/prediction APIs are checked against pinned `rfdetr==1.3.0`; input images are RGB and the threshold argument is `threshold`.

References: [model card](https://huggingface.co/GabrieleGiudici/E-BARD-detection-models), [author's class-ID evaluation output](https://github.com/GabrieleGiudic/E-BARD/blob/f8314e03179b6558fb6ef0d26a9135fdc3572b78/detection/eval/yolo_vs_detr.out), [RF-DETR source and license](https://github.com/roboflow/rf-detr).

## Implemented behavior

- Whole-image plus two overlapping horizontal crops, batched per source frame. Crop coordinates are mapped back to original normalized image coordinates; same-class overlap suppression removes tile duplicates.
- Distinct ball/hoop thresholds (0.25 / 0.35 by default), raw confidence retained. No model confidence is presented as a calibrated shot probability.
- Stationary hoop consensus requires observations at distinct timestamps, so tile duplicates cannot create fake support. Hoop regions remain unverified.
- Short ball segments use motion-gated one-to-one assignment, with a 200 ms maximum gap. Ambiguous competing balls start new segments; missing balls are not interpolated into evidence.
- A possible shot requires observed ascent, subsequent descent, enough continuous observations, and approach to the hoop region. Descending-only motion, missing hoops and large gaps cannot produce a proposal.
- A proposal includes source range, supporting timestamps, ball segment, hoop ID and review-required state. Possible-release attribution remains unknown if multiple players are equally plausible. Even a unique nearest track is provisional.
- **Made/missed counts stay null.** No confirmed outcomes, official statistics or player-performance updates are generated. A hoop/ball trajectory in 2D does not by itself prove a basket.

## Measured outdoor sample result

12.012-second TrackID3x3 stationary outdoor source, MPS, stride two, three model views per processed frame:

- 360 decoded frames; 180 frames processed.
- Hoop observations on all 180 sampled frames; one stable region, median detector confidence 0.571.
- 110 ball observations across 108 sampled frames (60% detection coverage); 13 short ball segments.
- Zero shot proposals. This does not prove there were no attempts: recall and outcomes are not annotated/evaluated yet.
- 53.55 seconds detector time, 57.29 seconds total including model loading; 3.36 source frames/second. This is batch analysis, not real time. CUDA code path exists but no NVIDIA GPU was available for testing.

Full-frame-only smoke inference missed the outdoor hoop at useful thresholds; overlapping crops found it around 0.45–0.52 confidence and an indoor hoop around 0.74. These are individual detections, not accuracy measurements. Annotated independent shot footage is needed before precision/recall claims or automatic outcome scoring.

Six new tests cover the proposal state machine, no-evidence cases, gap handling, ball reordering/jump rejection, duplicate hoop observations and ambiguous attribution. Model inference was exercised separately on actual video; synthetic trajectory tests do not establish basketball recognition accuracy.

## Integration handoff

The core app need not import RF-DETR into its process. A serialized background task can call `.venv-ball/bin/python scripts/analyze_basketball.py SOURCE --output JOB_DIR`, persist the returned report, and expose ball/hoop observations to the existing viewer. Use the same game source hash and an explicit parent pose job; never apply track IDs from a different analysis. Keep optional detector inference behind its own phase until runtime/latency is acceptable. The main API is still loopback-only; external GPU endpoints need authentication before Naive calls them.

Next: validate against labelled shot attempts, assess small-ball misses, expose the object overlays/proposal review in the UI, and connect only reviewer-confirmed outcomes to the existing highlight scores. These API/UI changes are deliberately separate from this first detector/trajectory module while concurrent identity work is active.

## PlayLattice integration update

The integration now exposes the optional worker through `backend/app/basketball_routes.py` and the Ball & Hoop Evidence panel. Observation overlays are timestamp bounded; proposals enter highlight review as pending, with made/missed outcomes unknown. Actor imports require the current pose job and identity correction revision. See `docs/API_CONTRACT.md` for the implemented routes; the earlier branch-isolation notes above describe the original detector handoff.
