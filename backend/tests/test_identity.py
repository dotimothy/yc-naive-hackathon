import numpy as np
import pytest
from workers.video.identity import PlayerIdentityTracker, uniform_descriptor
from workers.video.highlights import build_highlights, summarize

RED = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
BLUE = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
# Pre-review behaviour, for the evaluator comparison only.
LEGACY = dict(reconnection="automatic")


def test_legacy_occlusion_reconnects_new_track_to_same_game_player():
    tracker = PlayerIdentityTracker("game", **LEGACY)
    first = tracker.update([1], [[0, 0, 30, 100]], [RED], 0)[0]
    tracker.update([1], [[5, 0, 35, 100]], [RED], 250)
    tracker.update([1], [[10, 0, 40, 100]], [RED], 500)
    returned = tracker.update([8], [[20, 0, 50, 100]], [RED], 1250)[0]
    assert returned["player_id"] == first["player_id"]
    assert returned["track_id"] == "8" and returned["identity_status"] == "reidentified"
    assert not returned["identity_confirmed"]


def test_legacy_distinct_uniform_return_after_long_absence_and_separate_games():
    tracker = PlayerIdentityTracker("game", **LEGACY)
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


def test_legacy_uniform_and_motion_preserve_ids_when_input_order_changes():
    tracker = PlayerIdentityTracker("game", **LEGACY)
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


# --- Default reconnection policy: cross-track links are withheld for review ---


def person(x, top=0):
    return [x, top, x + 30, top + 100]


def walk(tracker, track, xs, colour, start=0, step=250):
    return [
        tracker.update([track], [person(x)], [colour], start + i * step)[0]
        for i, x in enumerate(xs)
    ]


def test_default_withholds_occlusion_reconnection_and_suggests_it():
    tracker = PlayerIdentityTracker("game")
    first = walk(tracker, 1, [0, 5, 10], RED)[0]
    fragment = tracker.update([8], [person(20)], [RED], 1250)[0]
    assert fragment["player_id"] != first["player_id"]
    assert fragment["identity_status"] == "ambiguous"
    assert fragment["identity_candidates"] == [first["player_id"]]
    assert fragment["association_confidence"] is None
    assert not fragment["identity_confirmed"]
    [suggestion] = tracker.reconnection_suggestions()
    assert suggestion["track_id"] == "8" and suggestion["requires_review"]
    assert [c["player_id"] for c in suggestion["candidates"]] == [first["player_id"]]
    assert suggestion["candidates"][0]["gap_ms"] == 750
    assert suggestion["candidates"][0]["track_ids"] == ["1"]
    summary = tracker.summary()
    assert summary["reconnection_policy"] == "review"
    assert summary["reconnections"] == 0 and summary["withheld_reconnections"] == 1


def test_default_never_reconnects_after_long_absence():
    tracker = PlayerIdentityTracker("game")
    original = walk(tracker, 1, [0, 0, 0], RED)[0]["player_id"]
    returned = tracker.update([80], [person(400)], [RED], 600000)[0]
    assert returned["player_id"] != original
    assert tracker.reconnections == 0


@pytest.mark.parametrize("continuity", [False, True])
def test_same_kit_fragments_after_crossing_are_never_merged(continuity):
    # Two same-kit players cross; the raw tracker loses both and starts new IDs.
    # Positions alone would swap them; no fragment may inherit either identity.
    tracker = PlayerIdentityTracker("game", continuity=continuity)
    for t in (0, 250, 500):
        before = tracker.update(
            [1, 2], [person(40 + t // 25), person(100 - t // 25)], [RED, RED], t
        )
    old = {s["player_id"] for s in before}
    after = tracker.update([7, 8], [person(75), person(65)], [RED, RED], 750)
    later = tracker.update([7, 8], [person(80), person(60)], [RED, RED], 1000)
    for frame in (after, later):
        ids = [s["player_id"] for s in frame]
        assert len(set(ids)) == 2 and not set(ids) & old
        assert all(s["identity_status"] != "reidentified" for s in frame)
    assert tracker.reconnections == 0


def test_single_same_kit_fragment_beside_a_teammate_is_not_merged():
    tracker = PlayerIdentityTracker("game")
    for t in (0, 250, 500):
        frame = tracker.update([1, 2], [person(0), person(300)], [RED, RED], t)
    lost = frame[0]["player_id"]
    result = tracker.update([2, 9], [person(300), person(15)], [RED, RED], 750)
    assert result[0]["player_id"] == frame[1]["player_id"]  # same raw track continues
    assert result[1]["player_id"] not in {lost, frame[1]["player_id"]}
    assert lost in result[1]["identity_candidates"]


def test_reused_raw_id_after_long_gap_does_not_mix_players():
    # A restarted or buggy raw tracker can hand an old ID to someone else.
    tracker = PlayerIdentityTracker("game")
    first = walk(tracker, 1, [0, 0, 0], RED)[0]
    reused = tracker.update([1], [person(500)], [RED], 30000)[0]
    assert reused["player_id"] != first["player_id"]
    assert reused["raw_track_id"] == "1" and reused["track_id"] == "1~2"
    legacy = PlayerIdentityTracker("game", **LEGACY)
    walk(legacy, 1, [0, 0, 0], RED)
    # Legacy mode merges the reused ID by colour alone: the contamination prevented.
    assert legacy.update([1], [person(500)], [RED], 30000)[0]["identity_status"] == (
        "reidentified"
    )


def test_reused_raw_id_that_teleports_starts_a_new_player():
    tracker = PlayerIdentityTracker("game")
    first = walk(tracker, 1, [0, 5], RED)[0]
    jumped = tracker.update([1], [person(900)], [RED], 750)[0]
    assert jumped["player_id"] != first["player_id"] and jumped["track_id"] == "1~2"


def test_duplicate_raw_id_in_one_frame_never_shares_a_player():
    tracker = PlayerIdentityTracker("game")
    walk(tracker, 1, [0, 5], RED)
    both = tracker.update([1, 1], [person(10), person(14)], [RED, RED], 750)
    assert both[0]["player_id"] != both[1]["player_id"]


def test_same_raw_track_continuation_is_unaffected_by_review_policy():
    for policy in ("review", "automatic"):
        tracker = PlayerIdentityTracker("game", reconnection=policy)
        results = walk(tracker, 3, [0, 10, 20, 30, 40], RED)
        assert len({r["player_id"] for r in results}) == 1
        assert [r["identity_status"] for r in results[1:]] == ["continued"] * 4


def test_suggestion_drops_candidates_seen_at_the_same_time_as_the_fragment():
    tracker = PlayerIdentityTracker("game")
    walk(tracker, 1, [0, 5, 10], RED)
    tracker.update([8], [person(100)], [RED], 1000)  # suggestion: raw 8 may be raw 1
    # The raw tracker then recovers raw 1 while raw 8 is still visible: different people.
    both = tracker.update([1, 8], [person(10), person(110)], [RED, RED], 1250)
    assert both[0]["identity_status"] == "continued"
    assert tracker.reconnection_suggestions() == []


def test_suggestions_are_copies_and_never_applied():
    tracker = PlayerIdentityTracker("game")
    walk(tracker, 1, [0, 5, 10], RED)
    tracker.update([8], [person(20)], [RED], 1250)
    tracker.reconnection_suggestions()[0]["candidates"].clear()
    assert tracker.reconnection_suggestions()[0]["candidates"]


def test_unknown_reconnection_policy_is_rejected():
    with pytest.raises(ValueError):
        PlayerIdentityTracker("game", reconnection="aggressive")
