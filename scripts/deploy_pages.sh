#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v npx >/dev/null || { echo 'Node.js/npm required.' >&2; exit 1; }
exec npx --yes wrangler@4.158.0 pages deploy --branch "${PLAYLATTICE_PAGES_BRANCH:-develop}" "$@"
