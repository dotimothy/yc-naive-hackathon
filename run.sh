#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
check=0; crew=1
for arg in "$@"; do
  case "$arg" in
    --check) check=1 ;;
    --no-crew) crew=0 ;;
    -h|--help) echo 'Usage: ./run.sh [--check] [--no-crew]'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
command -v curl >/dev/null || { echo 'curl is required.' >&2; exit 1; }
healthy() { curl -fsS --max-time 3 "$1" >/dev/null 2>&1; }
if (( check )); then
  failed=0
  for entry in 'API|http://127.0.0.1:8000/api/health' 'Dashboard|http://127.0.0.1:5173' 'Naive Crew|http://127.0.0.1:8011/health'; do
    name="${entry%%|*}"; url="${entry#*|}"
    if healthy "$url"; then echo "$name: running ($url)"; else echo "$name: unavailable"; failed=1; fi
  done
  exit "$failed"
fi
[[ -x .venv/bin/python && -d frontend/node_modules ]] || { echo 'Run ./setup.sh first.' >&2; exit 1; }
mkdir -p data/logs
pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]:-}"; do [[ -z "$pid" ]] || kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]:-}"; do [[ -z "$pid" ]] || wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start() {
  local name="$1" url="$2" log="$3"; shift 3
  if healthy "$url"; then echo "$name already running."; return; fi
  "$@" >"$log" 2>&1 &
  local pid=$!; pids+=("$pid")
  for (( attempt=0; attempt<90; attempt++ )); do
    if ! kill -0 "$pid" 2>/dev/null; then echo "$name exited; see $log" >&2; tail -n 12 "$log" >&2; exit 1; fi
    if healthy "$url"; then echo "$name ready."; return; fi
    sleep 1
  done
  echo "$name startup timed out; see $log" >&2; exit 1
}
start API http://127.0.0.1:8000/api/health data/logs/api.log .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
start Dashboard http://127.0.0.1:5173 data/logs/dashboard.log node frontend/node_modules/vite/bin/vite.js frontend --host 127.0.0.1 --port 5173 --strictPort
if (( crew )); then
  if [[ -f data/naive/crew-versions.json && -d integrations/naive/node_modules ]]; then
    start 'Naive Crew' http://127.0.0.1:8011/health data/logs/crew.log node integrations/naive/node_modules/tsx/dist/cli.mjs integrations/naive/src/analysis-dispatcher.ts
  else
    echo 'Naive Crew not configured locally; Film Room is available. See integrations/naive/README.md.'
  fi
fi
printf '\nPlayLattice: http://127.0.0.1:5173\nLogs: %s/data/logs\n' "$ROOT"
if [[ ${#pids[@]} -eq 0 ]]; then exit 0; fi
echo 'Press Ctrl-C to stop services started by this script.'
while :; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then echo 'A service stopped. Check data/logs.' >&2; exit 1; fi
  done
  sleep 2
done
