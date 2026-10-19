#!/usr/bin/env sh
# One-time setup on macOS / Linux: finds Python 3.11+ and runs scripts/setup.py. Usage: ./setup.sh
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    exec "$candidate" "$ROOT/scripts/setup.py" "$@"
  fi
done
echo "Python 3.11 or newer was not found." >&2
exit 1
