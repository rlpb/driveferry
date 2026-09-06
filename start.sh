#!/usr/bin/env bash
# First run creates a virtual environment next to this script; later runs reuse it.
set -euo pipefail
cd "$(dirname "$0")"

python=$(command -v python3 || command -v python || true)
if [ -z "$python" ]; then
  echo "Python 3.9 or newer is required. Install it and run this again." >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Setting up DriveFerry for the first time..."
  "$python" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet pywebview
fi

exec .venv/bin/python -m driveferry "$@"
