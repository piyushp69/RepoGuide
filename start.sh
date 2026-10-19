#!/usr/bin/env sh
# Start RepoGuide on macOS / Linux (backend + frontend). Usage: ./start.sh   or   ./start.sh --prod
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "No .venv found. Run ./setup.sh first." >&2
  exit 1
fi
exec "$ROOT/.venv/bin/python" "$ROOT/scripts/dev.py" "$@"
