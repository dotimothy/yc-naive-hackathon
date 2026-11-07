# Accelerated video worker

## Device policy

The default is `auto`: CUDA device 0, then Apple MPS, then CPU. Override globally with `SCOUTFILM_DEVICE=cpu|mps|cuda|cuda:N`, or per analysis with JSON `{"device":"mps"}`. Explicit unavailable devices are rejected with HTTP 422 rather than silently running on CPU. `/api/health` lists hardware availability; it is not a model warmup or a CUDA-operator certification.

The same model weights, FP32 precision, input size, confidence threshold and frame stride are retained across devices. No faster benchmark is achieved by skipping extra frames or disabling pose.

MPS runs a deliberate **hybrid** execution mode: convolutional backbone and keypoint head/predictor on Apple GPU, dynamic region processing/NMS/ROIAlign and final coordinates on CPU. Direct whole-model MPS was measured at ~88 seconds for the first frame locally; the hybrid smoke test took ~0.78 seconds cold and ~0.25–0.28 seconds on repeated frames. CUDA runs the whole model and inputs on GPU; CPU runs everything on CPU. Full-clip measurements are the authoritative comparison.

Low-score detections are now filtered before pose inference, using the same requested confidence threshold, instead of computing poses that would be discarded afterward. One resident model is cached per worker process (one device/configuration at a time), avoiding repeated weight loading. Skipped source frames are grabbed without unnecessary color-frame retrieval. GPU timings explicitly synchronize before and after inference; model load, output transfer and total wall time are recorded separately. Reports expose `requested_device`, actual `device`, `execution_mode`, precision and model cache status. There is no silent operator fallback enabled. Video decode, FFmpeg rendering/QA, tracking association and SQLite remain CPU work; the reported wall times include them.

## Local benchmark

```sh
.venv/bin/python scripts/benchmark_devices.py \
  data/trackid3x3/outdoor_stationary_12s.mp4 \
  --outdoor-sample --devices cpu mps
```

Outputs, tracks, QA and comparison JSON are saved under ignored `data/benchmarks/`. For NVIDIA use `--devices cpu cuda:0`. Run devices serially without another inference job competing for resources. The first frame includes device warmup; reports separate its timing. Cached models make subsequent jobs faster to start, but each benchmark still performs actual inference and renders/verifies clips.

## Measured results (2026-11-07)

Same 12.012-second, 360-frame outdoor source, model weights, FP32, stride 5 and confidence 0.75; 72 frames inferred, two clips rendered and independently verified in each run:

| Path | Inference FPS | End-to-end seconds |
| --- | ---: | ---: |
| Original CPU, pose before score filter | 1.45 | 52.08 |
| Optimized CPU | 3.14 | 25.24 |
| Optimized MPS hybrid | 4.92 | 16.57 |

Final MPS wall time is **3.14× faster than the original CPU path**, and **1.52× faster than the optimized CPU path**. These are individual full-clip measurements on this Mac, not a cross-hardware performance guarantee. Full-model MPS was rejected after a slow first-frame experiment. The first successful hybrid comparison before the pose-filter optimization measured CPU 52.08s vs MPS 20.01s.

All final runs retain 545 track samples and 29 temporary IDs. Early pose filtering gives exactly identical CPU boxes/keypoints/confidence on this sample. Final CPU vs MPS has identical timestamp/track keys; maximum normalized box difference 1.91e-7, pose-coordinate difference 1.70e-7, confidence difference 1.37e-6. A subsequent live API run selected `mps-hybrid` automatically and completed successfully in 20.52 seconds (4.09 model inference FPS); its actual device is visible in the browser. Eight tests pass, covering device selection, environment overrides, synchronization order and pre-queue rejection alongside the existing pipeline boundaries. This checks implementation parity, not basketball accuracy. Clip QA passes in both modes. Use `scripts/compare_device_tracks.py LEFT_TRACKS.json RIGHT_TRACKS.json` to reproduce parity checks.

## NVIDIA worker preparation

Use a Linux NVIDIA GPU worker with a compatible driver and a matched CUDA-enabled Torch/Torchvision installation. For this repo's pinned pair, the official PyTorch 2.8 instructions include CUDA 12.8 wheels:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install torch==2.8.0 torchvision==0.23.0 \
  --index-url https://download.pytorch.org/whl/cu128
.venv/bin/python -m pip install -r requirements.txt
# Install ffmpeg/ffprobe through the worker OS package manager.
SCOUTFILM_DEVICE=cuda:0 scripts/run_local.sh
```

Do not install the macOS-specific lockfile on Linux. Before calling it GPU-ready, run the benchmark on the worker and verify CUDA is actually selected and both clips pass QA. This Mac has no CUDA hardware; CUDA model execution/performance has not been verified here. Precision remains FP32; CUDA batching/FP16 are future optimizations requiring separate correctness/quality checks.

## Naive boundary

Naive will orchestrate this deterministic worker after local acceptance. A CUDA-enabled Torch build alone does not provide a GPU: the chosen runtime must expose an NVIDIA device and compatible driver. No claim is made that Naive's default runtime supplies one. If it does not, place this worker on separate GPU compute and let Naive call authenticated tools there. The current loopback API is not ready to expose externally; authentication and scoped/idempotent worker tools remain a later integration task. No Naive SDK or sponsor service is introduced by this change.

References: [PyTorch installation versions](https://pytorch.org/get-started/previous-versions/), [CUDA timing/synchronization](https://docs.pytorch.org/docs/stable/notes/cuda.html), [MPS synchronization](https://docs.pytorch.org/docs/stable/generated/torch.mps.synchronize.html).
