import numpy as np
import pytest
from workers.video.identity import propose_tracklet_merges
from workers.video.tracker import ContinuityTracker

RED = np.array([1.0, 0.0, 0.0, 0.0])
BLUE = np.array([0.0, 1.0, 0.0, 0.0])


def person(x, y=0, w=30, h=100):
    return [x, y, x + w, y + h]


def test_drop_in_compatible_with_baseline_reorder_and_expiry():
    tracker = ContinuityTracker(max_age=1)
    assert tracker.update([[0, 0, 10, 10], [20, 0, 30, 10]]) == [1, 2]
    assert tracker.update([[21, 0, 31, 10], [1, 0, 11, 10]]) == [2, 1]
    tracker.update([])
    tracker.update([])
    assert tracker.update([[1, 0, 11, 10]]) == [3]
    assert all(isinstance(i, int) for i in tracker.update([[1, 0, 11, 10]]))


def test_ids_follow_people_when_detection_order_is_permuted():
    paths = [
        [person(10 * t), person(300 - 8 * t, 20), person(150, 200 + 3 * t)]
        for t in range(12)
    ]
    plain, shuffled = ContinuityTracker(), ContinuityTracker()
    rng = np.random.default_rng(3)
    mapping = {}
    for t, boxes in enumerate(paths):
        order = rng.permutation(len(boxes))
        a = plain.update(boxes, timestamp_ms=t * 250)
        b = shuffled.update([boxes[i] for i in order], timestamp_ms=t * 250)
        # IDs are arbitrary labels; the same person must keep the same label.
        for i, j in zip(order, b):
            assert mapping.setdefault(a[i], j) == j
    assert len(set(mapping.values())) == 3 and sorted(plain.tracks) == [1, 2, 3]


def crossing(descriptors):
    """Two people walk through each other along the same image row."""
    tracker, owners = ContinuityTracker(), {}
    for t in range(14):
        left, right = person(20 * t), person(240 - 20 * t)  # exact overlap at t=6
        ids = tracker.update([left, right], descriptors, timestamp_ms=t * 250)
        owners.setdefault(ids[0], set()).add("left-to-right")
        owners.setdefault(ids[1], set()).add("right-to-left")
    return owners, tracker


@pytest.mark.parametrize("descriptors", [None, [RED, BLUE]])
def test_adversarial_crossing_never_moves_an_id_to_the_other_person(descriptors):
    owners, tracker = crossing(descriptors)
    # Exact overlap is a tie (and overlapping kits are ignored): fragments are
    # allowed, swaps are not.
    assert all(len(people) == 1 for people in owners.values())
    assert tracker.stats["ambiguous_rejections"] >= 1


def test_uniform_appearance_beats_wrong_motion_prediction_when_players_turn():
    tracker, owners = ContinuityTracker(), {}
    xs = [0, 30, 60, 90, 60, 30]  # approach to a 30px gap, then turn back
    for t, x in enumerate(xs):
        ids = tracker.update(
            [person(x), person(240 - x)], [RED, BLUE], timestamp_ms=t * 250
        )
        owners.setdefault(ids[0], set()).add("red")
        owners.setdefault(ids[1], set()).add("blue")
    assert len(owners) == 2 and all(len(people) == 1 for people in owners.values())


def test_short_occlusion_recovers_with_motion_prediction():
    tracker = ContinuityTracker()
    ids = [tracker.update([person(15 * t)], timestamp_ms=t * 250)[0] for t in range(4)]
    tracker.update([], timestamp_ms=1000)
    tracker.update([], timestamp_ms=1250)
    returned = tracker.update([person(15 * 6)], timestamp_ms=1500)
    assert returned == [ids[0]] and len(set(ids)) == 1
    assert tracker.last_details[0]["status"] == "recovered"
    assert tracker.last_details[0]["missed_updates"] == 2


def test_long_gap_starts_a_new_track():
    tracker = ContinuityTracker(max_lost_ms=1500)
    first = tracker.update([person(0)], timestamp_ms=0)[0]
    tracker.update([person(0)], timestamp_ms=250)
    assert tracker.update([person(0)], timestamp_ms=2500) != [first]
    assert tracker.last_details[0]["status"] == "new"


def test_same_colour_ambiguous_return_is_rejected():
    tracker = ContinuityTracker()
    for t in range(3):
        a, b = tracker.update(
            [person(0), person(100)], [RED, RED], timestamp_ms=t * 250
        )
    tracker.update([], timestamp_ms=750)
    new = tracker.update([person(50)], [RED], timestamp_ms=1000)[0]
    assert new not in (a, b)
    detail = tracker.last_details[0]
    assert detail["status"] == "ambiguous" and set(detail["ambiguous_with"]) == {a, b}


def test_different_uniform_cannot_take_over_lost_track():
    tracker = ContinuityTracker()
    first = tracker.update([person(0)], [RED], timestamp_ms=0)[0]
    tracker.update([person(5)], [RED], timestamp_ms=250)
    assert tracker.update([person(10)], [BLUE], timestamp_ms=500)[0] != first


def test_overlapping_detections_do_not_update_appearance():
    tracker = ContinuityTracker()
    a, _ = tracker.update([person(0), person(200)], [RED, BLUE], timestamp_ms=0)
    tracker.update([person(5), person(15)], [BLUE, BLUE], timestamp_ms=250)
    assert np.allclose(tracker.tracks[a]["descriptor"], RED)


def test_time_and_input_validation():
    tracker = ContinuityTracker()
    tracker.update([person(0)], timestamp_ms=100)
    with pytest.raises(ValueError):
        tracker.update([person(0)], timestamp_ms=100)
    with pytest.raises(ValueError):
        tracker.update([person(0)])
    with pytest.raises(ValueError):
        tracker.update([person(0)], [RED, BLUE], timestamp_ms=200)


def samples(track, start, xs, y=0.5, step=250):
    return [
        dict(track_id=track, timestamp_ms=start + i * step, bbox=[x, y, 0.03, 0.2])
        for i, x in enumerate(xs)
    ]


def test_merge_proposal_carries_evidence_and_is_never_confirmed():
    data = samples("1", 0, [0.10, 0.11, 0.12]) + samples("2", 1000, [0.14, 0.15])
    (proposal,) = propose_tracklet_merges(data, descriptors={"1": RED, "2": RED})
    assert proposal["source_track_id"] == "1" and proposal["target_track_id"] == "2"
    assert proposal["status"] == "proposed" and proposal["requires_review"]
    assert proposal["identity_confirmed"] is False
    evidence = proposal["evidence"]
    assert evidence["gap_ms"] == 500 and evidence["appearance_distance"] == 0


def test_same_colour_merge_candidates_are_marked_ambiguous():
    data = (
        samples("1", 0, [0.10, 0.10, 0.10])
        + samples("2", 0, [0.20, 0.20, 0.20])
        + samples("3", 1000, [0.15])
    )
    proposals = propose_tracklet_merges(data, descriptors={k: RED for k in "123"})
    assert len(proposals) == 1 and proposals[0]["status"] == "ambiguous"
    assert proposals[0]["alternatives"]


def test_merges_rejected_for_overlap_team_conflict_colour_and_long_gap():
    base = samples("1", 0, [0.10, 0.11, 0.12])
    overlapping = samples("2", 250, [0.13, 0.14])
    assert propose_tracklet_merges(base + overlapping) == []
    later = samples("2", 1000, [0.14, 0.15])
    teams = {"1": {"team": "Team A"}, "2": {"team": "Team B"}}
    assert propose_tracklet_merges(base + later, teams=teams) == []
    assert (
        propose_tracklet_merges(base + later, descriptors={"1": RED, "2": BLUE}) == []
    )
    assert propose_tracklet_merges(base + samples("2", 9000, [0.12])) == []
