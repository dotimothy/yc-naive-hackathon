#!/usr/bin/env python3
"""Fetch pinned indoor ground truth and report annotation coverage (no inference)."""
import csv
import hashlib
import io
import json
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

COMMIT = '9822a3dde59fc80fbb5eaacd443e79e8b059d94e'
BASE = 'https://raw.githubusercontent.com/open-starlab/TrackID3x3/' + COMMIT + '/'
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/trackid3x3'


def fetch(path):
    target = OUT / path
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(urllib.request.urlopen(BASE + path, timeout=60).read())
    return target.read_bytes()


def summarize(raw):
    frames = Counter()
    tracks = defaultdict(list)
    seen = set()
    for line, row in enumerate(csv.reader(io.StringIO(raw.decode())), 1):
        if len(row) != 10:
            raise ValueError('Expected 10 MOT columns at line %d' % line)
        frame, track = int(row[0]), int(row[1])
        x, y, w, h = map(float, row[2:6])
        if frame < 1 or w <= 0 or h <= 0 or (frame, track) in seen:
            raise ValueError('Invalid or duplicate box at line %d' % line)
        seen.add((frame, track))
        frames[frame] += 1
        tracks[track].append((frame, x + w / 2, y + h))
    if not frames:
        raise ValueError('Empty annotations')
    return {
        'rows': sum(frames.values()), 'track_count': len(tracks),
        'first_frame': min(frames), 'last_frame': max(frames),
        'annotated_frames': len(frames),
        'missing_frames': max(frames) - min(frames) + 1 - len(frames),
        'players_per_frame': dict(sorted(Counter(frames.values()).items())),
        'track_gaps': {str(t): sum(b[0] - a[0] - 1 for a, b in zip(sorted(v), sorted(v)[1:])) for t, v in tracks.items()},
    }, tracks


def preview(tracks):
    colors = ['#2563eb', '#ef4444', '#16a34a', '#9333ea', '#ea580c', '#0891b2']
    points = [p for v in tracks.values() for p in v]
    xmax, ymax = max(p[1] for p in points), max(p[2] for p in points)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700" viewBox="0 0 1000 700">', '<rect width="1000" height="700" fill="white"/>', '<text x="20" y="30" font-size="20">TrackID3x3 basket_S1T1_pre — ground-truth footpoint paths</text>', '<text x="20" y="55" font-size="14">Image coordinates; not model predictions or calibrated court positions</text>']
    for i, (track, values) in enumerate(sorted(tracks.items())):
        coords = ' '.join('%.2f,%.2f' % (30 + p[1] / xmax * 930, 90 + p[2] / ymax * 550) for p in sorted(values))
        parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>' % (coords, colors[i % len(colors)]))
        parts.append('<text x="%d" y="680" fill="%s">Track %s</text>' % (25 + i * 150, colors[i % len(colors)], track))
    return '\n'.join(parts + ['</svg>'])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tree = json.load(urllib.request.urlopen('https://api.github.com/repos/open-starlab/TrackID3x3/git/trees/' + COMMIT + '?recursive=1', timeout=60))
    paths = sorted(t['path'] for t in tree['tree'] if t['path'].startswith('ground_truth/Indoor/MOT/') and t['path'].endswith('.txt'))
    records = []
    for path in paths:
        raw = fetch(path)
        stats, tracks = summarize(raw)
        records.append(dict(path=path, sha256=hashlib.sha256(raw).hexdigest(), **stats))
        if path.endswith('basket_S1T1_pre.txt'):
            (OUT / 'trajectory_preview.svg').write_text(preview(tracks))
    report = {'source_commit': COMMIT, 'mode': 'ground_truth_analysis_only', 'sequence_count': len(records), 'total_boxes': sum(r['rows'] for r in records), 'total_annotated_frames': sum(r['annotated_frames'] for r in records), 'sequences': records}
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'sequences'}, indent=2))
    print('First sequence:', json.dumps(records[0]))
    print('Artifacts:', OUT)


if __name__ == '__main__':
    main()
