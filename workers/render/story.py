"""Source-grounded caption stories from continuity-gated replay plans; no inference."""

from copy import deepcopy

from workers.render.portrait import movement

PROFILE = "player-portrait-story-v1"
STYLE = "broadcast-story-v1"


def build_story_plan(plan):
    result = deepcopy(plan)
    result.update(profile=PROFILE, presentation_style=STYLE, quiz_candidates=[])
    for segment in result["segments"]:
        start, end = segment["source_start_ms"], segment["source_end_ms"]
        samples = sorted(
            (s for s in segment["samples"] if start <= s["timestamp_ms"] <= end),
            key=lambda s: s["timestamp_ms"],
        )
        if len(samples) < 5 or end - start < 1800:
            raise ValueError("Story requires continuous tracked evidence")
        if any(
            b["timestamp_ms"] - a["timestamp_ms"] > 800
            for a, b in zip(samples, samples[1:])
        ):
            raise ValueError("Story cannot bridge uncertain tracking gaps")
        if any(s.get("shot_id") != segment["shot_id"] for s in samples):
            raise ValueError("Story cannot bridge camera cuts")
        dx = movement(samples[0], samples[-1])
        direction = "right" if dx >= 0.025 else "left" if dx <= -0.025 else None
        label = segment.get("display_label", "Highlighted player")
        action = (
            f"Moves {direction} on screen"
            if direction
            else "Little net sideways movement"
        )
        detail = f"Tracked center changes {abs(dx) * 100:.1f}% of image width in {(samples[-1]['timestamp_ms'] - samples[0]['timestamp_ms']) / 1000:.1f}s."
        third = (end - start) / 3
        segment["story_beats"] = [
            dict(
                start_ms=start,
                end_ms=round(start + third),
                title="SETUP",
                headline=f"Follow {label}",
                caption="The highlighted box follows one provisional track in this camera shot.",
            ),
            dict(
                start_ms=round(start + third),
                end_ms=round(start + third * 2),
                title="THE MOVEMENT",
                headline=action,
                caption=detail,
            ),
            dict(
                start_ms=round(start + third * 2),
                end_ms=end,
                title="THE TAKEAWAY",
                headline="Compare the start and finish",
                caption=(
                    f"The track finishes further {direction} on screen. Compare its starting and ending positions in the source inset."
                    if direction
                    else "The track finishes near its starting horizontal position. Compare the source inset to see the movement around it."
                ),
            ),
        ]
        segment["story_evidence"] = dict(
            track_id=segment["track_id"],
            shot_id=segment["shot_id"],
            start_ms=samples[0]["timestamp_ms"],
            end_ms=samples[-1]["timestamp_ms"],
            horizontal_shift=dx,
            sample_count=len(samples),
        )
    return result


def story_beat(segment, timestamp):
    beats = segment.get("story_beats", [])
    return next(
        (b for b in beats if b["start_ms"] <= timestamp < b["end_ms"]),
        beats[-1] if beats else None,
    )
