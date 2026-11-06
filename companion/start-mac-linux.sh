#!/usr/bin/env bash
# Starts the iluvmusic companion. The first run sets up a private Python
# environment next to this file.
set -euo pipefail
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "iluvmusic needs Python 3.9 or newer. Install it (macOS: brew install python) and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting things up. This takes a minute."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --quiet --disable-pip-version-check --upgrade pip
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  printf "Start iluvmusic automatically when you sign in? [y/N] "
  read -r answer || answer=""
  case "$answer" in
    [Yy]*) .venv/bin/python iluvmusic_companion.py --install-autostart ;;
  esac
fi

exec .venv/bin/python iluvmusic_companion.py "$@"
