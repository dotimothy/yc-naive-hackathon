import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from workers.render.quiz_music import synthesize_music  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--music-id", required=True)
p.add_argument("--output", required=True)
a = p.parse_args()
print(json.dumps(synthesize_music(a.output, a.music_id)))
