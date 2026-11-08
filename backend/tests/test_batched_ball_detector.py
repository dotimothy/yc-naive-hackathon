"""CPU-only tests for experimental cross-frame ball batching and its benchmark.

A fake predictor replaces RF-DETR, so these check batching bookkeeping and
equivalence with the production mapping code, not basketball recognition.
"""

import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from workers.video.basketball_detector import BasketballDetector
from workers.video.batched_ball_detector import BatchedBallDetector, production_views

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "batches", ROOT / "scripts/benchmark_ball_batches.py"
)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
W, H = 1280, 720
THRESHOLDS = dict(basketball=0.25, hoop=0.35, player=0.5, referee=0.5)


class ContentModel:
    """Deterministic fake: detections depend only on each crop's own pixels."""

    def __init__(self):
        self.batch_sizes = []

    def predict(self, images, threshold):
        self.batch_sizes.append(len(images))
        results = []
        for image in images:
            h, w = image.shape[:2]
            level = float(image.mean()) / 255  # frames differ by brightness
            results.append(
                SimpleNamespace(
                    xyxy=[
                        [w * 0.4, h * 0.4, w * 0.4 + 20, h * 0.4 + 20],  # ball
                        [w * 0.1, h * 0.2, w * 0.1 + 30, h * 0.2 + 30],  # hoop
                        [0, 0, 5, 5],  # below threshold
                    ],
                    class_id=[0, 1, 0],
                    confidence=[0.3 + level * 0.5, 0.36 + level * 0.1, 0.1],
                )
            )
        return results


def fake_detector():
    return SimpleNamespace(
        device="cpu", model=ContentModel(), thresholds=THRESHOLDS, tiled=True
    )


def frames(n):
    return [
        (np.full((H, W, 3), 20 * (i + 1), dtype=np.uint8), 67 * i) for i in range(n)
    ]


def strip(items):
    return [{k: v for k, v in d.items() if k != "view"} for d in items]


def test_production_views_match_detector_rule():
    assert production_views(W, H) == [(0, 0, W, H), (0, 0, 768, H), (512, 0, W, H)]
    assert production_views(720, 720) == [(0, 0, 720, 720)]


@pytest.mark.parametrize("k", [1, 2, 4])
def test_batched_output_equals_production_detect_per_frame(k):
    detector = fake_detector()
    batched = BatchedBallDetector(detector, k)
    items = frames(k)
    kept, raw = batched.detect_batch(items)
    assert detector.model.batch_sizes == [3 * k]
    for (frame, timestamp), frame_kept, frame_raw in zip(items, kept, raw):
        production = BasketballDetector.detect(detector, frame, timestamp)
        assert strip(frame_kept) == production
        assert all(d["timestamp_ms"] == timestamp for d in frame_raw)
    # Frames stay separate: different brightness -> different confidences per frame.
    assert len({kept[i][0]["confidence"] for i in range(k)}) == k


def test_short_final_batch_and_invalid_sizes():
    batched = BatchedBallDetector(fake_detector(), 4)
    kept, _ = batched.detect_batch(frames(3))
    assert len(kept) == 3 and batched.last["images"] == 9
    with pytest.raises(ValueError):
        batched.detect_batch(frames(5))
    with pytest.raises(ValueError):
        batched.detect_batch([])
    with pytest.raises(ValueError):
        BatchedBallDetector(fake_detector(), 0)


def test_result_count_mismatch_is_an_error():
    detector = fake_detector()
    detector.model.predict = lambda images, threshold: []
    with pytest.raises(RuntimeError):
        BatchedBallDetector(detector, 2).detect_batch(frames(2))


def record(kept, t=0):
    return dict(frame_index=t, pts_ms=t, frame_digest="d%d" % t, kept=kept, raw=kept)


def obs(kind, x, confidence, t=0):
    return dict(
        class_name=kind,
        confidence=confidence,
        bbox=[x, 0.5, 0.02, 0.02],
        timestamp_ms=t,
    )


def test_compare_outputs_applies_explicit_tolerances():
    ref = [record([obs("basketball", 0.5, 0.40)])]
    tiny = [record([obs("basketball", 0.5 + 5e-5, 0.40 + 5e-5)])]
    big = [record([obs("basketball", 0.5 + 5e-4, 0.40)])]
    gone = [record([])]
    ok = bench.compare_outputs(ref, tiny, 1e-4, 1e-4)
    assert ok["kept_matched_out_of_tolerance"] == 0 and ok["frames_differing"] == []
    assert ok["max_abs_bbox_difference"] == pytest.approx(5e-5)
    assert (
        bench.compare_outputs(ref, big, 1e-4, 1e-4)["kept_matched_out_of_tolerance"]
        == 1
    )
    missing = bench.compare_outputs(ref, gone, 1e-4, 1e-4)
    assert missing["kept_missing"] == 1 and missing["frames_differing"] == [0]
    with pytest.raises(RuntimeError):
        bench.compare_outputs(ref, [dict(record([]), frame_digest="other")], 1e-4, 1e-4)


def test_gate_requires_identical_trajectory_metrics():
    seq = [
        record(
            [obs("hoop", 0.47, 0.6, t * 67)]
            + ([obs("basketball", 0.5, 0.5, t * 67)] if t != 2 else []),
            t * 67,
        )
        for t in range(6)
    ]
    ref = bench.trajectory_summary(seq, 1000)
    assert ref["frames_with_ball"] == 5 and ref["no_ball_run_lengths"] == [1]
    same = bench.compare_outputs(seq, seq, 1e-4, 1e-4)
    assert bench.gate(ref, ref, same, 1e-4)["passed"]
    worse = [
        dict(r, kept=[d for d in r["kept"] if d["class_name"] == "hoop"])
        if r["pts_ms"] == 0
        else r
        for r in seq
    ]
    result = bench.gate(
        ref,
        bench.trajectory_summary(worse, 1000),
        bench.compare_outputs(seq, worse, 1e-4, 1e-4),
        1e-4,
    )
    assert not result["passed"]
    assert not result["checks"]["same_frames_with_ball"]
    assert not result["checks"]["no_kept_observation_missing_or_added"]


def test_guard_detects_active_job_and_fails_closed(tmp_path):
    database = tmp_path / "scoutfilm.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))"
        )
        db.execute(
            "INSERT INTO records VALUES('job','a',?)",
            (json.dumps(dict(status="cancelling")),),
        )
    assert bench.active_jobs(database)[0]["status"] == "cancelling"
    broken = tmp_path / "broken.sqlite3"
    broken.write_text("nope")
    assert "error" in bench.active_jobs(broken)[0]


def test_oom_detection_text():
    assert bench.is_oom(RuntimeError("MPS backend out of memory (MPS allocated: 1 GB)"))
    assert not bench.is_oom(RuntimeError("shape mismatch"))
