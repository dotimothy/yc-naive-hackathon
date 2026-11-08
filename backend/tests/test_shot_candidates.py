from workers.video.shot_candidates import (
    BallTrajectories,
    stationary_hoops,
    propose_shots,
    release_actor,
    suppress,
)


def ball(x, y, t, track="ball-1", confidence=0.8):
    return dict(
        bbox=[x - 0.008, y - 0.008, 0.016, 0.016],
        confidence=confidence,
        timestamp_ms=t,
        class_name="basketball",
        ball_track_id=track,
    )


def test_shot_trajectory_is_only_a_review_candidate():
    observations = [
        ball(x, y, t)
        for x, y, t in [
            (0.2, 0.5, 0),
            (0.3, 0.4, 50),
            (0.4, 0.25, 100),
            (0.47, 0.2, 150),
            (0.49, 0.25, 200),
            (0.5, 0.31, 250),
        ]
    ]
    hoop = dict(hoop_id="h1", bbox=[0.45, 0.3, 0.1, 0.02], confidence=0.9)
    candidates = propose_shots(observations, [hoop], 1000)
    assert len(candidates) == 1
    c = candidates[0]
    assert c["outcome"] == "unknown" and c["review_required"] and not c["verified"]
    assert (
        c["event_type"] == "shot_attempt_candidate"
        and c["start_ms"] == 0
        and c["end_ms"] == 1000
    )
    assert c["actor"]["track_id"] is None
    assert len(c["evidence_timestamps"]) >= 4


def test_descending_ball_or_missing_hoop_cannot_claim_shot():
    observations = [ball(0.5, y, i * 50) for i, y in enumerate([0.2, 0.23, 0.28, 0.31])]
    hoop = dict(hoop_id="h1", bbox=[0.45, 0.3, 0.1, 0.02])
    assert not propose_shots(observations, [hoop], 2000)
    assert not propose_shots(observations, [], 2000)


def test_gaps_are_not_interpolated_into_shots():
    observations = [
        ball(0.5, y, t)
        for y, t in [
            (0.5, 0),
            (0.4, 50),
            (0.25, 100),
            (0.2, 150),
            (0.25, 500),
            (0.31, 550),
        ]
    ]
    assert not propose_shots(
        observations, [dict(hoop_id="h1", bbox=[0.45, 0.3, 0.1, 0.02])], 2000
    )
    linker = BallTrajectories(max_gap_ms=200)
    first = linker.update([ball(0.5, 0.5, 0)], 0)[0]
    returned = linker.update([ball(0.5, 0.5, 500)], 500)[0]
    assert first["ball_track_id"] != returned["ball_track_id"]


def test_ball_linking_survives_reordering_but_rejects_implausible_jump():
    linker = BallTrajectories()
    first = linker.update([ball(0.1, 0.1, 0), ball(0.8, 0.8, 0)], 0)
    second = linker.update([ball(0.81, 0.8, 50), ball(0.11, 0.1, 50)], 50)
    assert [x["ball_track_id"] for x in second] == [
        first[1]["ball_track_id"],
        first[0]["ball_track_id"],
    ]
    jumped = linker.update([ball(0.5, 0.5, 100)], 100)[0]
    assert jumped["ball_track_id"] not in {b["ball_track_id"] for b in first}


def test_tile_duplicates_do_not_create_fake_hoop_consensus():
    detection = dict(
        class_name="hoop", bbox=[0.45, 0.3, 0.1, 0.03], confidence=0.8, timestamp_ms=0
    )
    assert not stationary_hoops([detection, detection, detection])
    observations = [dict(detection, timestamp_ms=t) for t in [0, 100, 200]]
    hoops = stationary_hoops(observations)
    assert (
        len(hoops) == 1
        and not hoops[0]["verified"]
        and hoops[0]["observed_frames"] == 3
    )
    assert len(suppress([detection, dict(detection, confidence=0.7)])) == 1


def test_nearby_players_require_unique_release_evidence():
    item = ball(0.5, 0.5, 0)
    player = dict(
        track_id="1", timestamp_ms=0, bbox=[0.45, 0.3, 0.1, 0.3], keypoints=[]
    )
    assert release_actor(item, [player, dict(player, track_id="2")])["track_id"] is None
    unique = release_actor(item, [player])
    assert unique["track_id"] == "1" and not unique["identity_confirmed"]
    assert release_actor(item, [dict(player, timestamp_ms=1000)])["track_id"] is None
