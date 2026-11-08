"""CPU-only tests for the experimental adaptive-crop policy and benchmark helpers.

No model is loaded; a fake predictor stands in for RF-DETR. These tests check
view planning and bookkeeping, not basketball recognition.
"""

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from workers.video.adaptive_ball_detector import (
    AdaptiveBallDetector,
    AdaptiveCropPolicy,
    TileSelectionPolicy,
    predict_views,
    production_views,
)

W, H = 1280, 720
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "bench", ROOT / "scripts/benchmark_ball_views.py"
)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def obs(kind, x, y, t, confidence=0.6, size=0.02):
    return dict(
        class_name=kind,
        confidence=confidence,
        bbox=[x - size / 2, y - size / 2, size, size],
        timestamp_ms=t,
    )


def warmed(policy, frames=3, ball_x=None):
    """Feed bootstrap frames containing a stationary hoop (and optionally a ball)."""
    t = 0
    for _ in range(frames):
        policy.plan(t, W, H)
        seen = [obs("hoop", 0.47, 0.4, t)]
        if ball_x is not None:
            seen.append(obs("basketball", ball_x, 0.7, t))
        policy.update(seen, t)
        t += 67
    return t


def test_bootstrap_and_refresh_use_production_views():
    policy = AdaptiveCropPolicy(bootstrap_frames=2, refresh_every=5)
    views, reason = policy.plan(0, W, H)
    assert reason == "refresh" and views == production_views(W, H)
    t = warmed(policy, frames=5, ball_x=0.5)
    views, reason = policy.plan(t, W, H)  # frame number 5
    assert reason == "refresh" and len(views) == 3


def test_missing_hoop_falls_back():
    policy = AdaptiveCropPolicy(bootstrap_frames=1)
    policy.plan(0, W, H)
    policy.update([obs("basketball", 0.5, 0.7, 0)], 0)
    views, reason = policy.plan(67, W, H)
    assert reason == "no_hoop" and views == production_views(W, H)


def test_nearby_ball_and_hoop_share_one_tile_sized_crop():
    policy = AdaptiveCropPolicy()
    t = warmed(policy, ball_x=0.55)
    views, reason = policy.plan(t, W, H)
    assert reason == "roi"
    names = [n for n, _ in views]
    assert names == ["full", "roi0"]
    x1, y1, x2, y2 = views[1][1]
    assert (x2 - x1, y1, y2) == (round(W * 0.6), 0, H)
    for x in (0.47, 0.55):
        assert x1 + 0.15 * (x2 - x1) <= x * W <= x2 - 0.15 * (x2 - x1)


def test_distant_ball_gets_its_own_crop_and_crops_stay_in_frame():
    policy = AdaptiveCropPolicy(keep_full=False)
    t = warmed(policy, ball_x=0.99)
    views, _ = policy.plan(t, W, H)
    assert [n for n, _ in views] == ["roi0", "roi1"]
    for _, (x1, _, x2, _) in views:
        assert 0 <= x1 < x2 <= W


def test_stale_ball_triggers_reacquire_on_schedule():
    policy = AdaptiveCropPolicy(reacquire_every=2)
    t = warmed(policy, ball_x=0.5) + 400  # older than the 200 ms segment gap
    assert policy.plan(t, W, H)[1] == "reacquire"  # 0 frames without ball
    policy.update([obs("hoop", 0.47, 0.4, t)], t)
    views, reason = policy.plan(t + 67, W, H)  # 1 frame without ball: hoop crop only
    assert reason == "roi" and [n for n, _ in views] == ["full", "roi0"]
    policy.update([obs("hoop", 0.47, 0.4, t + 67)], t + 67)
    assert policy.plan(t + 134, W, H)[1] == "reacquire"


def test_square_frames_are_never_cropped():
    policy = AdaptiveCropPolicy()
    views, reason = policy.plan(0, 720, 720)
    assert reason == "not_wide" and len(views) == 1


def test_invalid_parameters_rejected():
    with pytest.raises(ValueError):
        AdaptiveCropPolicy(crop_fraction=0.05)
    with pytest.raises(ValueError):
        AdaptiveCropPolicy(refresh_every=0)


class FakeModel:
    def __init__(self):
        self.calls = []

    def predict(self, images, threshold):
        self.calls.append([img.shape for img in images])
        out = []
        for img in images:
            h, w = img.shape[:2]
            # One ball at the crop centre and one low-confidence hoop below threshold.
            out.append(
                SimpleNamespace(
                    xyxy=[
                        [w / 2 - 10, h / 2 - 10, w / 2 + 10, h / 2 + 10],
                        [0, 0, 20, 20],
                    ],
                    class_id=[0, 1],
                    confidence=[0.5, 0.2],
                )
            )
        return out


def fake_detector():
    return SimpleNamespace(
        device="cpu",
        model=FakeModel(),
        thresholds=dict(basketball=0.25, hoop=0.35, player=0.5, referee=0.5),
    )


def test_predict_views_maps_crop_coordinates_and_thresholds():
    detector = fake_detector()
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    raw, seconds = predict_views(detector, frame, 100, [("roi0", (400, 0, 1168, 720))])
    assert seconds >= 0 and len(raw) == 1  # hoop at 0.2 is below its 0.35 threshold
    ball = raw[0]
    assert ball["view"] == "roi0" and ball["timestamp_ms"] == 100
    x, y, w, h = ball["bbox"]
    assert x + w / 2 == pytest.approx((400 + 384) / W)
    assert y + h / 2 == pytest.approx(0.5)


def test_adaptive_detector_suppresses_overlaps_and_updates_policy():
    detector = AdaptiveBallDetector(fake_detector())
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    kept = detector.detect(frame, 0)
    # Three views find three distinct centred balls (full, left, right crops): none overlap.
    assert len(kept) == 3 and detector.last_reason == "refresh"
    assert detector.policy.frame_number == 1 and detector.policy.ball is not None


def frames(items):
    return [dict(pts_ms=t, frame_index=i, kept=k) for i, (t, k) in enumerate(items)]


def test_agreement_counts_lost_and_extra_balls_without_calling_them_errors():
    ref = frames(
        [(0, [obs("basketball", 0.5, 0.5, 0)]), (67, [obs("basketball", 0.5, 0.5, 67)])]
    )
    cand = frames(
        [
            (0, [obs("basketball", 0.505, 0.5, 0)]),
            (67, [obs("basketball", 0.9, 0.5, 67)]),
        ]
    )
    a = bench.agreement(ref, cand)
    assert a["reference_ball_observations_matched"] == 1
    assert a["reference_only"] == 1 and a["candidate_only"] == 1
    assert a["frames_with_lost_reference_ball"] == [67]
    assert "not accuracy" in a["note"]


def test_metrics_and_verdict_flag_coverage_regression():
    seq = [
        (
            t * 67,
            [obs("hoop", 0.47, 0.4, t * 67)]
            + ([obs("basketball", 0.5, 0.6 - t * 0.001, t * 67)] if t != 3 else []),
        )
        for t in range(8)
    ]
    ref = bench.summarize(frames(seq), 1000)
    assert (
        ref["ball"]["frames_with_ball"] == 7
        and ref["ball"]["longest_no_ball_run_frames"] == 1
    )
    worse_seq = [
        (t, [k for k in kept if k["class_name"] == "hoop" or t != 134])
        for t, kept in seq
    ]
    worse = bench.summarize(frames(worse_seq), 1000)
    worse["agreement"] = bench.agreement(frames(seq), frames(worse_seq))
    v = bench.verdict(
        ref, worse, dict(detector_seconds=1, reference_detector_seconds=2)
    )
    assert not v["passed"]
    assert not v["checks"]["frames_with_ball_preserved"] and v["checks"]["cheaper"]


def test_guard_reports_active_jobs_and_fails_closed(tmp_path):
    database = tmp_path / "scoutfilm.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))"
        )
        db.execute(
            "INSERT INTO records VALUES('job','a',?)",
            (json.dumps(dict(status="succeeded")),),
        )
        db.execute(
            "INSERT INTO records VALUES('basketball_job','b',?)",
            (json.dumps(dict(status="running")),),
        )
    found = bench.active_jobs(database)
    assert [(j["kind"], j["status"]) for j in found] == [("basketball_job", "running")]
    broken = tmp_path / "broken.sqlite3"
    broken.write_text("not a database")
    assert "error" in bench.active_jobs(broken)[0]


def test_tile_selection_only_uses_production_views():
    policy = TileSelectionPolicy()
    t = warmed(policy, ball_x=0.2)
    views, reason = policy.plan(t, W, H)
    assert reason == "roi" and views == [
        production_views(W, H)[0],
        production_views(W, H)[1],
    ]
    allowed = set(production_views(W, H))
    for ball_x in (0.05, 0.5, 0.95):
        p = TileSelectionPolicy()
        p.plan(0, W, H)
        views, _ = p.plan(warmed(p, ball_x=ball_x), W, H)
        assert set(views) <= allowed


def test_tile_selection_picks_one_tile_covering_hoop_and_ball():
    policy = TileSelectionPolicy(margin=0.05)
    assert [n for n, _ in policy.crops([0.474, 0.9], W, H)] == ["right"]
    assert [n for n, _ in policy.crops([0.474, 0.2], W, H)] == ["left"]
    assert [n for n, _ in policy.crops([0.2, 0.9], W, H)] == ["left", "right"]


def raw_hoop(view, t):
    return dict(obs("hoop", 0.47, 0.4, t), view=view)


def test_learned_hoop_tiles_add_the_reliable_tile():
    policy = TileSelectionPolicy(learn_hoop_tiles=True)
    t = 0
    for _ in range(
        3
    ):  # bootstrap frames run both tiles; only the right one finds the hoop
        policy.plan(t, W, H)
        raw = [raw_hoop("right", t), dict(obs("basketball", 0.2, 0.7, t), view="left")]
        policy.update(suppress_like(raw), t, raw)
        t += 67
    assert policy.reliable_hoop_tiles() == {"right"}
    views, _ = policy.plan(t, W, H)
    assert [n for n, _ in views] == [
        "full",
        "left",
        "right",
    ]  # ball needs left, hoop needs right
    plain = TileSelectionPolicy()
    t = 0
    for _ in range(3):
        plain.plan(t, W, H)
        raw = [raw_hoop("right", t), dict(obs("basketball", 0.2, 0.7, t), view="left")]
        plain.update(suppress_like(raw), t, raw)
        t += 67
    assert [n for n, _ in plain.plan(t, W, H)[0]] == ["full", "left"]


def suppress_like(raw):
    from workers.video.shot_candidates import suppress

    return suppress(raw)
