#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python -m venv .venv-ball
scoutfilm_core_site=$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')
scoutfilm_ball_site=$(.venv-ball/bin/python -c 'import site; print(site.getsitepackages()[0])')
printf '%s\n' "$scoutfilm_core_site" > "$scoutfilm_ball_site/shared_torch.pth"
.venv-ball/bin/python -m pip install --upgrade pip
.venv-ball/bin/python -m pip install -r requirements-ball.txt -c requirements.txt
.venv/bin/python scripts/download_basketball_model.py
