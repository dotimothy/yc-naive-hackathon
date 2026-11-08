#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
models=1; sample=1; ball=1; check=0
for arg in "$@"; do
  case "$arg" in
    --check) check=1 ;;
    --skip-models) models=0 ;;
    --skip-sample) sample=0 ;;
    --no-ball) ball=0 ;;
    -h|--help) echo 'Usage: ./setup.sh [--check] [--skip-models] [--skip-sample] [--no-ball]'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
for tool in node npm ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Install Node.js/npm and FFmpeg first." >&2; exit 1; }
done
node -e 'if(Number(process.versions.node.split(".")[0])<22){console.error("Node.js 22.13+ required for Naive SQLite support");process.exit(1)};require("node:sqlite")' 2>/dev/null || { echo 'Node.js with node:sqlite support required (22.13+).' >&2; exit 1; }
python_bin="${PLAYLATTICE_PYTHON:-}"
if [[ -z "$python_bin" && -x .venv/bin/python ]]; then python_bin="$ROOT/.venv/bin/python"; fi
if [[ -z "$python_bin" ]]; then
  for candidate in python3.11 python3.12 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys;sys.exit(not ((3,9)<=sys.version_info[:2]<(3,13)))'; then python_bin="$(command -v "$candidate")"; break; fi
  done
fi
[[ -n "$python_bin" ]] || { echo 'Python 3.9–3.12 is required by the pinned dependencies.' >&2; exit 1; }
"$python_bin" -c 'import sys;sys.exit(not ((3,9)<=sys.version_info[:2]<(3,13)))' || { echo 'Use Python 3.9–3.12 with PLAYLATTICE_PYTHON.' >&2; exit 1; }
if (( check )); then
  [[ -x .venv/bin/python && -d frontend/node_modules && -d integrations/naive/node_modules ]] || { echo 'Dependencies missing; run ./setup.sh.' >&2; exit 1; }
  .venv/bin/python -c 'import fastapi, uvicorn, torch, ultralytics'
  echo 'Local prerequisites and installed dependencies are available.'
  exit 0
fi
if [[ ! -x .venv/bin/python ]]; then "$python_bin" -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix integrations/naive ci --ignore-scripts
if (( models )); then
  .venv/bin/python scripts/download_pose_model.py
  if (( ball )); then bash scripts/setup_ball_detector.sh; fi
fi
if (( sample )); then .venv/bin/python scripts/download_sample.py --outdoor; fi
npm --prefix frontend run build
npm --prefix integrations/naive run typecheck
printf '\nSetup complete. Start PlayLattice with ./run.sh\n'
