#!/usr/bin/env python3
"""Compare two inference runs; parity is not ground-truth accuracy."""

import argparse
import json
from pathlib import Path
import numpy as np


def compare(left_path, right_path):
    left = json.loads(Path(left_path).read_text())
    right = json.loads(Path(right_path).read_text())

    def key(sample):
        return sample["timestamp_ms"], sample["track_id"]

    a, b = {key(s): s for s in left}, {key(s): s for s in right}
    shared = a.keys() & b.keys()
    box_errors = []
    point_errors = []
    score_errors = []
    for k in shared:
        box_errors.append(float(np.max(np.abs(np.array(a[k]["bbox"]) - b[k]["bbox"]))))
        point_errors.append(
            float(
                np.max(
                    np.abs(
                        np.array([[p["x"], p["y"]] for p in a[k]["keypoints"]])
                        - [[p["x"], p["y"]] for p in b[k]["keypoints"]]
                    )
                )
            )
        )
        score_errors.append(abs(a[k]["confidence"] - b[k]["confidence"]))
    return dict(
        left_samples=len(left),
        right_samples=len(right),
        shared_timestamp_track_keys=len(shared),
        same_keys=a.keys() == b.keys(),
        max_normalized_box_difference=max(box_errors, default=None),
        max_normalized_pose_difference=max(point_errors, default=None),
        max_score_difference=max(score_errors, default=None),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left")
    parser.add_argument("right")
    args = parser.parse_args()
    print(json.dumps(compare(args.left, args.right), indent=2))
