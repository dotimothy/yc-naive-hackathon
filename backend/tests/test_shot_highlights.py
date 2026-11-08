"""Camera boundaries are source-time invariants, independent of tracker quality."""

from copy import deepcopy

import pytest

from workers.video.highlights import (
    add_spotlights,
    build_highlights,
    render_highlights,
    shot_is_eligible,
    split_ranges,
    summarize,
)


def shot(id, start, end, category="gameplay_candidate", review_status="pending"):
    return dict(
        shot_id=str(id),
        start_ms=start,
        end_ms=end,
        category=category,
        review_status=review_status,
    )


def observations(start=0, end=8000, track="1"):
    return [
        dict(
            track_id=track,
            timestamp_ms=t,
            bbox=[0.1 + 0.05 * (i % 7), 0.2, 0.1, 0.3],
            confidence=0.9,
            keypoints=[],
        )
        for i, t in enumerate(range(start, end, 250))
    ]


def test_context_and_evidence_split_even_when_tracker_id_is_reused():
    shots = [shot(0, 0, 4000), shot(1, 4000, 8000)]
    bundle = build_highlights(observations(), 8000, 20, 5, "job", shots=shots)
    assert bundle["shots"] == shots
    assert {c["shot_id"] for c in bundle["candidates"]} == {"0", "1"}
    for candidate in bundle["candidates"]:
        boundary = shots[int(candidate["shot_id"])]
        assert (
            boundary["start_ms"]
            <= candidate["start_ms"]
            <= candidate["action_start_ms"]
            <= candidate["action_end_ms"]
            < candidate["end_ms"]
            <= boundary["end_ms"]
        )
        assert all(
            candidate["start_ms"]
            <= p["timestamp_ms"]
            < p["end_ms"]
            <= candidate["end_ms"]
            for p in candidate["spotlight"]
        )
    # No sample at exactly4000 belongs to the first shot.
    assert all(
        c["action_end_ms"] < 4000 for c in bundle["candidates"] if c["shot_id"] == "0"
    )


def test_unknown_or_non_gameplay_needs_reviewer_gameplay_confirmation():
    for category in ["non_gameplay_candidate", "uncertain"]:
        shots = [shot(0, 0, 8000, category)]
        assert (
            build_highlights(observations(), 8000, 20, 5, "job", shots=shots)[
                "candidates"
            ]
            == []
        )
        shots[0]["review_status"] = "confirmed_gameplay"
        assert build_highlights(observations(), 8000, 20, 5, "job", shots=shots)[
            "candidates"
        ]
    assert not shot_is_eligible(
        shot(0, 0, 1000, review_status="confirmed_non_gameplay")
    )


def test_short_shots_do_not_borrow_evidence_from_other_camera_angles():
    shots = [shot(i, i * 500, (i + 1) * 500) for i in range(16)]
    bundle = build_highlights(observations(), 8000, 20, 5, "job", shots=shots)
    assert bundle["candidates"] == []


def test_manifest_bounds_clamped_without_mutating_source():
    shots = [shot(0, -1000, 4000), shot(1, 4000, 10000)]
    original = deepcopy(shots)
    bundle = build_highlights(observations(), 8000, 20, 5, "job", shots=shots)
    assert shots == original
    assert bundle["shots"][0]["start_ms"] == 0 and bundle["shots"][1]["end_ms"] == 8000
    assert all(0 <= c["start_ms"] < c["end_ms"] <= 8000 for c in bundle["candidates"])


def test_legacy_no_manifest_is_unchanged_and_explicit_empty_manifest_is_not_legacy():
    samples = observations()
    assert build_highlights(samples, 8000, 20, 5, "job") == build_highlights(
        samples, 8000, 20, 5, "job", shots=None
    )
    assert build_highlights(samples, 8000, 20, 5, "job")["candidates"]
    assert build_highlights(samples, 8000, 20, 5, "job", shots=[])["candidates"] == []


def test_source_ranges_split_and_preserve_coach_metadata():
    ranges = [dict(start_ms=1000, end_ms=7000, event_type="coach_review")]
    shots = [
        shot(0, 0, 3000),
        shot(1, 3000, 5000, "non_gameplay_candidate"),
        shot(2, 5000, 8000),
    ]
    assert split_ranges(ranges, shots) == [
        dict(start_ms=1000, end_ms=3000, event_type="coach_review", shot_id="0"),
        dict(start_ms=5000, end_ms=7000, event_type="coach_review", shot_id="2"),
    ]
    assert len(split_ranges(ranges, shots, eligible_only=False)) == 3
    assert split_ranges(ranges, None) == ranges
    assert split_ranges(ranges, []) == []


@pytest.mark.parametrize(
    "shots",
    [
        [shot(0, 0, 4000), shot(1, 3000, 8000)],
        [shot(0, 0, 4000), shot(0, 4000, 8000)],
        [shot(0, 3000, 3000)],
        [shot(0, 0, 4.5)],
    ],
)
def test_invalid_manifest_cannot_produce_ambiguous_clips(shots):
    with pytest.raises(ValueError):
        build_highlights(observations(), 8000, 20, 5, "job", shots=shots)


def test_regenerated_spotlights_stop_at_source_cut():
    samples = observations()
    bundle = build_highlights(
        samples, 8000, 20, 5, "job", shots=[shot(0, 0, 4000), shot(1, 4000, 8000)]
    )
    add_spotlights(bundle, samples)
    for c in bundle["candidates"]:
        assert c["spotlight"]
        assert all(
            c["start_ms"] <= p["timestamp_ms"] < p["end_ms"] <= c["end_ms"]
            for p in c["spotlight"]
        )


def test_render_rejects_imported_candidate_crossing_a_cut(tmp_path):
    bundle = build_highlights(
        observations(),
        8000,
        20,
        5,
        "job",
        shots=[shot(0, 0, 4000), shot(1, 4000, 8000)],
    )
    candidate = bundle["candidates"][0]
    candidate.update(start_ms=3000, end_ms=5000, score=1000, automatic_score=1000)
    summarize(bundle)
    assert candidate["selected"]
    with pytest.raises(ValueError, match="one eligible camera shot"):
        render_highlights("unused.mp4", tmp_path, bundle, "game", lambda *a: None)
