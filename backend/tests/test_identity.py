import numpy as np
import pytest
from workers.video.identity import PlayerIdentityTracker, uniform_descriptor
from workers.video.highlights import build_highlights, summarize

RED = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
BLUE = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)


def test_occlusion_reconnects_new_track_to_same_game_player():
    tracker = PlayerIdentityTracker("game")
    first = tracker.update([1], [[0, 0, 30, 100]], [RED], 0)[0]
    tracker.update([1], [[5, 0, 35, 100]], [RED], 250)
    tracker.update([1], [[10, 0, 40, 100]], [RED], 500)
    returned = tracker.update([8], [[20, 0, 50, 100]], [RED], 1250)[0]
    assert returned["player_id"] == first["player_id"]
    assert returned["track_id"] == "8" and returned["identity_status"] == "reidentified"
    assert not returned["identity_confirmed"]


def test_distinct_uniform_return_after_long_absence_and_separate_games():
    tracker = PlayerIdentityTracker("game")
    original = tracker.update([1], [[0, 0, 30, 100]], [RED], 0)[0]["player_id"]
    tracker.update([1], [[0, 0, 30, 100]], [RED], 250)
    tracker.update([1], [[0, 0, 30, 100]], [RED], 500)
    returned = tracker.update([80], [[400, 0, 430, 100]], [RED], 600000)[0]
    assert returned["player_id"] == original
    other = PlayerIdentityTracker("other").update([1], [[0, 0, 30, 100]], [RED], 0)[0]
    assert other["player_id"] != original


def test_simultaneous_people_never_share_id_and_ambiguous_return_is_held():
    tracker = PlayerIdentityTracker("game")
    for time in [0, 250, 500]:
        result = tracker.update(
            [1, 2], [[0, 0, 30, 100], [200, 0, 230, 100]], [RED, RED], time
        )
        assert len({s["player_id"] for s in result}) == 2
    previous = {s["player_id"] for s in result}
    returned = tracker.update([9], [[100, 0, 130, 100]], [RED], 10000)[0]
    assert returned["player_id"] not in previous
    assert returned["identity_status"] == "ambiguous"
    assert returned["association_confidence"] is None


def test_uniform_and_motion_preserve_ids_when_input_order_changes():
    tracker = PlayerIdentityTracker("game")
    first = tracker.update(
        [1, 2], [[0, 0, 30, 100], [200, 0, 230, 100]], [RED, BLUE], 0
    )
    second = tracker.update(
        [8, 9], [[180, 0, 210, 100], [20, 0, 50, 100]], [BLUE, RED], 250
    )
    assert [s["player_id"] for s in second] == [
        first[1]["player_id"],
        first[0]["player_id"],
    ]


def test_reused_raw_id_is_split_when_appearance_changes():
    tracker = PlayerIdentityTracker("game")
    first = tracker.update([1], [[0, 0, 30, 100]], [RED], 0)[0]
    second = tracker.update([1], [[0, 0, 30, 100]], [BLUE], 250)[0]
    assert first["player_id"] != second["player_id"]
    assert second["raw_track_id"] == "1" and second["track_id"] == "1~2"
    with pytest.raises(ValueError):
        tracker.update([1], [[0, 0, 30, 100]], [BLUE], 250)


def test_descriptor_excludes_head_and_distinguishes_uniforms():
    frame = np.zeros((100, 40, 3), dtype=np.uint8)
    frame[25:] = [255, 0, 0]
    a = uniform_descriptor(frame, [0, 0, 40, 100])
    frame[:25] = [0, 255, 0]
    assert np.array_equal(a, uniform_descriptor(frame, [0, 0, 40, 100]))
    frame[25:] = [0, 0, 255]
    b = uniform_descriptor(frame, [0, 0, 40, 100])
    assert float(np.dot(a, b)) < 0.95
    assert uniform_descriptor(frame, [0, 0, 1, 1]) is None


def test_scores_combine_fragments_and_manual_correction_overrides():
    samples = []
    for track, start in [("1", 0), ("8", 2500)]:
        for i in range(8):
            samples.append(
                dict(
                    track_id=track,
                    player_id="game-player-1",
                    player_label="Player 1",
                    identity_status="continued",
                    association_confidence=0.9,
                    timestamp_ms=start + i * 250,
                    bbox=[0.1 + i * 0.05, 0.1, 0.1, 0.3],
                    keypoints=[],
                    confidence=0.95,
                )
            )
    bundle = build_highlights(samples, 5000, 20, 5, "job")
    assert len(bundle["players"]) == 1
    player = bundle["players"][0]
    assert player["track_ids"] == ["1", "8"] and player["candidate_count"] == 2
    assert player["identity_source"] == "automatic" and not player["identity_confirmed"]
    assert all(c["player_id"] == "game-player-1" for c in bundle["candidates"])
    summarize(
        bundle,
        groups=[
            dict(
                player_id="reviewer-player",
                label="Corrected player",
                track_ids=["8"],
                confirmed=True,
            )
        ],
    )
    assert len(bundle["players"]) == 2
    assert (
        next(c for c in bundle["candidates"] if c["track_id"] == "8")["player_id"]
        == "reviewer-player"
    )
    assert next(p for p in bundle["players"] if p["player_id"] == "reviewer-player")[
        "identity_confirmed"
    ]
