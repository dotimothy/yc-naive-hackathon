"""Small IoU/Hungarian baseline. Track IDs are not athlete identities."""

import numpy as np
from scipy.optimize import linear_sum_assignment


def iou(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


class Tracker:
    def __init__(self, threshold=0.25, max_age=3):
        self.tracks = {}
        self.next_id = 1
        self.threshold, self.max_age = threshold, max_age

    def update(self, boxes):
        keys = list(self.tracks)
        assigned, result = set(), {}
        for key in keys:
            self.tracks[key]["age"] += 1
        if keys and len(boxes):
            scores = np.array(
                [[iou(self.tracks[k]["box"], b) for b in boxes] for k in keys]
            )
            # Gate before assignment so invalid pairings cannot displace valid matches.
            rows, cols = linear_sum_assignment(
                np.where(scores >= self.threshold, 1 - scores, 100)
            )
            for row, col in zip(rows, cols):
                if scores[row, col] >= self.threshold:
                    result[int(col)] = keys[row]
                    assigned.add(int(col))
        for index, box in enumerate(boxes):
            if index not in assigned:
                result[index] = self.next_id
                self.next_id += 1
            self.tracks[result[index]] = {"box": list(map(float, box)), "age": 0}
        self.tracks = {k: v for k, v in self.tracks.items() if v["age"] <= self.max_age}
        return [result[i] for i in range(len(boxes))]
