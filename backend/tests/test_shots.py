"""Camera-cut bookkeeping fixtures; these do not establish recognition accuracy."""

from copy import deepcopy

import cv2
import numpy as np
import pytest

from workers.video.shots import ShotDetector, classify_shots


def image(seed=1):
    rng = np.random.default_rng(seed)
    return rng.integers(25, 210, size=(180, 320, 3), dtype=np.uint8)


def test_hard_cut_starts_new_half_open_range_at_current_frame():
    detector = ShotDetector()
    first, second = image(1), image(55)
    assert detector.update(first, 0) is None
    assert detector.update(first, 500) is None
    cut = detector.update(second, 600)
    assert cut and cut["timestamp_ms"] == 600 and not cut["verified"]
    assert detector.shot_id == 1
    assert detector.update(second, 700) is None
    shots = detector.finalize(800)
    assert [(s["shot_id"], s["start_ms"], s["end_ms"]) for s in shots] == [
        (0, 0, 600),
        (1, 600, 800),
    ]
    assert shots[0]["visual_summary"]["decoded_frames"] == 2
    assert shots[1]["visual_summary"]["decoded_frames"] == 2
    shots[0]["start_ms"] = 100
    assert detector.finalize(800)[0]["start_ms"] == 0


def test_static_camera_and_person_motion_do_not_split():
    detector = ShotDetector()
    base = image(1)
    for t in range(0, 2000, 50):
        current = base.copy()
        x = (t // 50) * 4
        cv2.rectangle(current, (x, 60), (x + 15, 100), (220, 20, 30), -1)
        assert detector.update(current, t) is None
    assert len(detector.finalize(2000)) == 1


def test_camera_pan_is_not_a_cut():
    detector = ShotDetector()
    base = image(2)
    for i, t in enumerate(range(0, 1500, 100)):
        shifted = cv2.warpAffine(
            base,
            np.float32([[1, 0, i * 3], [0, 1, 0]]),
            (320, 180),
            borderMode=cv2.BORDER_REFLECT,
        )
        assert detector.update(shifted, t) is None


def test_gradual_camera_zoom_is_not_a_cut():
    detector = ShotDetector()
    base = image(3)
    for i, t in enumerate(range(0, 1500, 100)):
        matrix = cv2.getRotationMatrix2D((160, 90), 0, 1 + i * 0.006)
        zoomed = cv2.warpAffine(base, matrix, (320, 180), borderMode=cv2.BORDER_REFLECT)
        assert detector.update(zoomed, t) is None


def test_brightness_change_and_single_saturated_flash_are_not_cuts():
    detector = ShotDetector()
    base = image(4)
    for t, frame in [
        (0, base),
        (500, base),
        (600, np.clip(base.astype(float) * 1.1 + 10, 0, 255).astype(np.uint8)),
        (700, np.full_like(base, 255)),
        (800, base),
    ]:
        assert detector.update(frame, t) is None
    assert len(detector.finalize(900)) == 1


def test_new_camera_after_flash_compares_with_informative_anchor():
    detector = ShotDetector()
    detector.update(image(1), 0)
    detector.update(image(1), 500)
    assert detector.update(np.full_like(image(1), 255), 600) is None
    assert detector.update(image(52), 700)


def test_cut_cooldown_and_timestamp_validation():
    detector = ShotDetector()
    detector.update(image(1), 0)
    assert detector.update(image(10), 600)
    assert detector.update(image(60), 700) is None
    with pytest.raises(ValueError, match="strictly increase"):
        detector.update(image(1), 700)
    with pytest.raises(ValueError, match="duration"):
        detector.finalize(700)
    with pytest.raises(ValueError, match="timestamp"):
        ShotDetector().update(image(1), -1)
    with pytest.raises(ValueError, match="BGR"):
        ShotDetector().update(np.zeros((2, 2), dtype=np.uint8), 0)
    assert ShotDetector().finalize(0) == []


def shot(id=0, start=0, end=2000, **extra):
    return dict(shot_id=id, start_ms=start, end_ms=end, **extra)


def records(boxes, shot_id=0):
    return [
        dict(shot_id=shot_id, timestamp_ms=t, bbox=box, track_id=str(i))
        for t in [0, 500, 1000, 1500]
        for i, box in enumerate(boxes)
    ]


def test_wide_multiplayer_view_is_only_a_gameplay_candidate():
    shots = [shot()]
    samples = records(
        [[0.1, 0.2, 0.1, 0.35], [0.45, 0.25, 0.1, 0.3], [0.8, 0.2, 0.1, 0.35]]
    )
    before = deepcopy(shots)
    result = classify_shots(shots, samples)
    assert result[0]["category"] == "gameplay_candidate"
    assert result[0]["requires_review"] and not result[0]["category_verified"]
    assert shots == before


def test_dominant_closeup_is_reviewable_non_gameplay_candidate():
    result = classify_shots([shot()], records([[0.2, 0.1, 0.55, 0.85]]))
    assert result[0]["category"] == "non_gameplay_candidate"
    assert result[0]["category_evidence"]["closeup_frames"] == 4


def test_no_person_detections_alone_never_exclude_a_view():
    result = classify_shots([shot()], [])
    assert result[0]["category"] == "uncertain"
    result = classify_shots(
        [
            shot(
                visual_summary=dict(
                    decoded_frames=50, flat_fraction=0.2, mean_edge_density=0.03
                )
            )
        ],
        [],
    )
    assert result[0]["category"] == "uncertain"


def test_uniform_card_is_unverified_non_gameplay_candidate():
    detector = ShotDetector()
    for t in range(0, 2000, 100):
        detector.update(np.full((100, 100, 3), 255, dtype=np.uint8), t)
    result = classify_shots(detector.finalize(2000), [])
    assert result[0]["category"] == "non_gameplay_candidate"
    assert not result[0]["category_verified"]


@pytest.mark.parametrize("string_ids", [False, True])
def test_same_timestamp_observations_from_other_shot_are_excluded(string_ids):
    samples = records(
        [[0.1, 0.2, 0.1, 0.35], [0.45, 0.25, 0.1, 0.3], [0.8, 0.2, 0.1, 0.35]],
        shot_id=1,
    )
    result = classify_shots(
        [shot(id="0" if string_ids else 0), shot(id="1" if string_ids else 1)], samples
    )
    assert result[0]["category"] == "uncertain"
    assert result[1]["category"] == "gameplay_candidate"


def test_short_or_weak_views_stay_uncertain():
    samples = records([[0.2, 0.2, 0.1, 0.3], [0.6, 0.2, 0.1, 0.3]])
    assert classify_shots([shot()], samples)[0]["category"] == "uncertain"
    assert (
        classify_shots([shot(end=500)], records([[0.2, 0.1, 0.55, 0.85]]))[0][
            "category"
        ]
        == "uncertain"
    )
