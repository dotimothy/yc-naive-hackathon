"""Source-time reviewer splits; original observations and identities remain immutable.

A split is a correction to a tracklet, not confirmation of who a person is. Every
resulting fragment starts with a distinct anonymous, unconfirmed identity. The
API owns persistence, correction revisions and invalidation of earlier reviews.
"""

from bisect import bisect_right
from copy import deepcopy

MAX_SPLITS_PER_TRACK = 10


def _boundaries(samples, splits):
    times = {}
    for sample in samples:
        times.setdefault(str(sample["track_id"]), set()).add(sample["timestamp_ms"])
    boundaries = {}
    for split in splits:
        if not isinstance(split, dict):
            raise ValueError("Each split must contain track_id and split_ms")
        track = split.get("track_id")
        boundary = split.get("split_ms")
        if not isinstance(track, str) or not track or track not in times:
            raise ValueError(
                "Split track does not exist in the current source observations"
            )
        if isinstance(boundary, bool) or not isinstance(boundary, int):
            raise ValueError(
                "Split time must be an integer source timestamp in milliseconds"
            )
        observed = times[track]
        if boundary not in observed or not min(observed) < boundary < max(observed):
            raise ValueError(
                "Split time must be an observed timestamp inside the track range"
            )
        values = boundaries.setdefault(track, [])
        if boundary in values:
            raise ValueError("Duplicate split boundary for the same track")
        values.append(boundary)
        if len(values) > MAX_SPLITS_PER_TRACK:
            raise ValueError("A track may have at most 10 split boundaries")
    generated = set()
    for track, values in boundaries.items():
        values.sort()
        names = [track + "~before", *(track + "~" + str(t) for t in values)]
        if any(name in times or name in generated for name in names):
            raise ValueError(
                "Split fragment ID conflicts with an existing source track"
            )
        generated.update(names)
    return boundaries


def validate_splits(samples, splits):
    """Raise ValueError for unknown tracks, invalid/duplicate times or ID collisions.

    Boundaries must coincide with actual observations strictly inside the source
    track range. The observation at a boundary belongs to the later fragment.
    Up to ten boundaries per original track are allowed, in any input order.
    """
    _boundaries(samples, splits)


def apply_track_splits(samples, splits):
    """Return independent copies with corrected fragments; never mutate source data.

    Every affected observation records source_track_id and identity_correction.
    No automatic association confidence, candidate identity or confirmation is
    carried into a corrected fragment. Unaffected observations are copied as-is.
    """
    boundaries = _boundaries(samples, splits)
    corrected = deepcopy(samples)
    for sample in corrected:
        source_track = str(sample["track_id"])
        values = boundaries.get(source_track)
        if not values:
            continue
        segment = bisect_right(values, sample["timestamp_ms"])
        track = source_track + (
            "~before" if segment == 0 else "~" + str(values[segment - 1])
        )
        sample.update(
            source_track_id=source_track,
            track_id=track,
            player_id="fragment-" + track,
            player_label="Fragment " + track,
            identity_source="track",
            identity_status="reviewer_split",
            identity_confirmed=False,
            association_confidence=None,
            identity_candidates=[],
            identity_correction=dict(
                method="reviewer-source-time-split-v1",
                source_track_id=source_track,
                split_ms=values.copy(),
                segment_index=segment,
                segment_start_ms=None if segment == 0 else values[segment - 1],
                segment_end_ms=values[segment] if segment < len(values) else None,
                requires_identity_review=True,
            ),
        )
    return corrected
