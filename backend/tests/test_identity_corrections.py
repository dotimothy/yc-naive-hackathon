from copy import deepcopy

import pytest

from workers.video.identity_corrections import apply_track_splits, validate_splits


def samples(track="1", times=(0, 100, 200, 300)):
    return [
        dict(
            track_id=track,
            timestamp_ms=t,
            bbox=[0.1, 0.2, 0.1, 0.3],
            player_id="old-person",
            player_label="Old association",
            identity_status="reidentified",
            identity_source="automatic",
            identity_confirmed=True,
            association_confidence=0.99,
            identity_candidates=["other-old-person"],
        )
        for t in times
    ]


def test_split_retains_source_evidence_and_removes_old_identity():
    original = samples()
    before = deepcopy(original)
    result = apply_track_splits(original, [dict(track_id="1", split_ms=200)])
    assert original == before
    assert [s["track_id"] for s in result] == ["1~before", "1~before", "1~200", "1~200"]
    assert [s["timestamp_ms"] for s in result] == [0, 100, 200, 300]
    assert {s["player_id"] for s in result} == {"fragment-1~before", "fragment-1~200"}
    assert all(s["source_track_id"] == "1" for s in result)
    assert all(
        not s["identity_confirmed"] and s["association_confidence"] is None
        for s in result
    )
    assert all(
        s["identity_candidates"] == [] and s["identity_status"] == "reviewer_split"
        for s in result
    )
    assert result[1]["identity_correction"]["segment_end_ms"] == 200
    assert result[2]["identity_correction"]["segment_start_ms"] == 200
    assert all(s["identity_correction"]["requires_identity_review"] for s in result)
    result[0]["bbox"][0] = 0.9
    result[0]["identity_correction"]["split_ms"].append(999)
    assert original == before
    assert result[1]["identity_correction"]["split_ms"] == [200]


def test_multiple_boundaries_sorted_unaffected_tracks_deep_copied():
    original = samples() + samples("2")
    result = apply_track_splits(
        original, [dict(track_id="1", split_ms=200), dict(track_id="1", split_ms=100)]
    )
    assert [s["track_id"] for s in result[:4]] == [
        "1~before",
        "1~100",
        "1~200",
        "1~200",
    ]
    assert result[4:] == original[4:]
    assert result[4] is not original[4]
    assert apply_track_splits(original, []) == original


@pytest.mark.parametrize(
    "track,time",
    [
        ("missing", 100),
        ("1", 0),
        ("1", 300),
        ("1", 150),
        ("1", -1),
        ("1", True),
        ("1", 100.0),
    ],
)
def test_invalid_track_or_boundary(track, time):
    with pytest.raises(ValueError):
        validate_splits(samples(), [dict(track_id=track, split_ms=time)])


def test_duplicates_limit_and_name_collisions_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        validate_splits(samples(), [dict(track_id="1", split_ms=100)] * 2)
    many = samples(times=tuple(range(20)))
    with pytest.raises(ValueError, match="at most 10"):
        validate_splits(many, [dict(track_id="1", split_ms=i) for i in range(1, 12)])
    with pytest.raises(ValueError, match="conflicts"):
        validate_splits(
            samples() + samples("1~before"), [dict(track_id="1", split_ms=100)]
        )
