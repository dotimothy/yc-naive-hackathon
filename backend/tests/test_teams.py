import numpy as np
from workers.video.teams import (
    TeamGrouper,
    flag_non_players,
    torso_box,
    torso_colour_feature,
)

RED, BLUE, BLACK, GREEN, WHITE = (
    (40, 40, 220),
    (200, 60, 30),
    (20, 20, 20),
    (40, 200, 40),
    (235, 235, 235),
)


def painted(shirt, head=(180, 200, 230), size=(100, 40)):
    """BGR person crop: head on top, shirt on torso, dark legs."""
    h, w = size
    frame = np.full((h, w, 3), (90, 140, 170), np.uint8)
    frame[:18] = head
    frame[18:55] = shirt
    frame[55:] = (60, 60, 60)
    return frame, [0, 0, w, h]


def feature(shirt, scale=1.0, head=(180, 200, 230)):
    frame, box = painted(shirt, head)
    frame = np.clip(frame.astype(float) * scale, 0, 255).astype(np.uint8)
    return torso_colour_feature(frame, box)


def grouper_for(kits, observations=5, scale=1.0):
    grouper = TeamGrouper()
    for t in range(observations):
        for key, shirt in kits.items():
            grouper.observe(
                key, feature(shirt, scale), timestamp_ms=t * 250 + int(key[-1])
            )
    return grouper


def partition(assignments):
    groups = {}
    for key, value in assignments.items():
        if value["team"] != "unknown":
            groups.setdefault(value["team"], set()).add(key)
    return sorted(map(frozenset, groups.values()), key=sorted)


def test_torso_region_excludes_head_and_uses_pose_when_available():
    x1, y1, x2, y2 = torso_box([0, 0, 40, 100])
    assert y1 >= 20 and y2 <= 55 and x1 >= 10 and x2 <= 30
    keypoints = np.zeros((17, 3))
    keypoints[:, 2] = 1
    keypoints[[5, 6, 11, 12], :2] = [[12, 25], [28, 25], [14, 55], [26, 55]]
    x1, y1, x2, y2 = torso_box([0, 0, 40, 100], keypoints)
    assert y1 > 25 and y2 == 55 and 12 < x1 < x2 < 28
    keypoints[5, 2] = 0  # an invisible shoulder falls back to the fixed band
    assert torso_box([0, 0, 40, 100], keypoints)[1] == 22


def test_head_and_face_pixels_do_not_change_colour_feature():
    assert np.allclose(feature(RED, head=(0, 0, 0)), feature(RED, head=(255, 255, 255)))
    assert torso_colour_feature(np.zeros((10, 10, 3), np.uint8), [0, 0, 2, 2]) is None


def test_two_kits_are_grouped_and_referee_stays_unknown():
    kits = {
        "p1": RED,
        "p2": RED,
        "p3": RED,
        "p4": BLUE,
        "p5": BLUE,
        "p6": BLUE,
        "r7": BLACK,
    }
    assignments, summary = grouper_for(kits).assign()
    assert summary["separable"] and not summary["identity_confirmed"]
    assert partition(assignments) == [{"p1", "p2", "p3"}, {"p4", "p5", "p6"}]
    assert assignments["p1"]["team"] == "Team A"  # first appearance, not colour
    assert assignments["r7"]["team"] == "unknown"


def test_team_labels_are_invariant_to_which_colour_each_team_wears():
    a, _ = grouper_for({"p1": RED, "p2": RED, "p3": BLUE, "p4": BLUE}).assign()
    b, _ = grouper_for({"p1": BLUE, "p2": BLUE, "p3": RED, "p4": RED}).assign()
    c, _ = grouper_for({"p1": WHITE, "p2": WHITE, "p3": BLACK, "p4": BLACK}).assign()
    assert {k: v["team"] for k, v in a.items()} == {k: v["team"] for k, v in b.items()}
    assert {k: v["team"] for k, v in a.items()} == {k: v["team"] for k, v in c.items()}


def test_partition_survives_global_brightness_change():
    kits = {"p1": RED, "p2": RED, "p3": BLUE, "p4": BLUE}
    bright, _ = grouper_for(kits).assign()
    dim, _ = grouper_for(kits, scale=0.75).assign()
    assert partition(bright) == partition(dim) == [{"p1", "p2"}, {"p3", "p4"}]


def test_uniform_colour_everywhere_is_not_split_into_teams():
    assignments, summary = grouper_for({f"p{i}": RED for i in range(1, 7)}).assign()
    assert not summary["separable"]
    assert all(v["team"] == "unknown" for v in assignments.values())


def test_mixed_colour_side_stays_unknown_while_cohesive_side_is_labelled():
    kits = {"p1": RED, "p2": RED, "p3": RED, "p4": BLUE, "p5": GREEN, "p6": BLACK}
    assignments, summary = grouper_for(kits).assign()
    assert summary["reason"] == "one_dominant_colour_group_only"
    assert partition(assignments) == [{"p1", "p2", "p3"}]
    assert {assignments[k]["team"] for k in ("p4", "p5", "p6")} == {"unknown"}


def test_sparse_and_excluded_keys_stay_unknown():
    grouper = grouper_for({"p1": RED, "p2": RED, "p3": BLUE, "p4": BLUE})
    grouper.observe("p9", feature(RED), timestamp_ms=0)
    assignments, _ = grouper.assign(exclude=["p4"])
    assert assignments["p9"]["reason"] == "insufficient_observations"
    assert (
        assignments["p4"]["team"] == "unknown"
        and assignments["p4"]["reason"] == "excluded"
    )


def track(key, positions, size=(0.04, 0.2), step=250):
    return [
        dict(track_id=key, timestamp_ms=i * step, bbox=[x, y, size[0], size[1]])
        for i, (x, y) in enumerate(positions)
    ]


def test_non_player_flags_require_multiple_positional_signals():
    still = [(0.5, 0.5)] * 20
    seated_edge = track("bench", [(0.0, 0.8)] * 20, size=(0.09, 0.2))
    standing_still = track("still", still)
    moving = track(
        "player", [(0.1 + 0.03 * i, 0.5 + 0.01 * (i % 3)) for i in range(20)]
    )
    short = track("short", [(0.0, 0.8)] * 4, size=(0.09, 0.2))
    flags = flag_non_players(seated_edge + standing_still + moving + short)
    assert flags["bench"]["role_status"] == "likely_non_player"
    assert set(flags["bench"]["signals"]) >= {
        "stationary",
        "seated_posture",
        "frame_edge",
    }
    assert flags["still"]["role_status"] == "review_required"
    assert (
        flags["player"]["role_status"] == "unknown" and flags["player"]["signals"] == []
    )
    assert flags["short"]["reason"] == "insufficient_observations"


def test_colour_outlier_alone_never_flags_and_court_polygon_is_optional_evidence():
    moving = track("player", [(0.1 + 0.03 * i, 0.5) for i in range(20)])
    teams = {"player": {"team": "unknown", "reason": "colour_outlier"}}
    assert (
        flag_non_players(moving, team_assignments=teams)["player"]["role_status"]
        == "unknown"
    )
    court = [[0.2, 0.3], [0.9, 0.3], [0.9, 0.95], [0.2, 0.95]]
    courtside = track("courtside", [(0.05, 0.6)] * 20)
    flags = flag_non_players(courtside, court_polygon=court)
    assert flags["courtside"]["role_status"] == "likely_non_player"
    assert "outside_court" in flags["courtside"]["signals"]


def test_stationary_on_court_player_is_only_review_required_never_excluded():
    court = [[0.1, 0.3], [0.9, 0.3], [0.9, 0.95], [0.1, 0.95]]
    standing = track(
        "defender", [(0.5, 0.5)] * 24
    )  # waiting at the elbow, inside court
    snapshot = [dict(s, bbox=list(s["bbox"])) for s in standing]
    flags = flag_non_players(standing, court_polygon=court)
    assert flags["defender"]["role_status"] == "review_required"
    assert flags["defender"]["signals"] == ["stationary"]
    assert standing == snapshot  # flags never remove or edit samples


def test_active_player_at_frame_edge_is_not_flagged_as_non_player():
    # Running along the near sideline with the box clipped by the image border.
    edge = track("wing", [(0.0, 0.3 + 0.02 * i) for i in range(20)])
    flags = flag_non_players(edge)
    assert flags["wing"]["role_status"] == "review_required"
    assert flags["wing"]["signals"] == ["frame_edge"]
    crouched = track(
        "guard", [(0.3 + 0.02 * i, 0.5) for i in range(20)], size=(0.09, 0.2)
    )
    assert flag_non_players(crouched)["guard"]["role_status"] == "review_required"
