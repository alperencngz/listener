#!/usr/bin/env bash
# Build Listener.app (py2app alias mode) into ./dist/Listener.app
#
# Alias mode references this repo's .venv in place, so the app must stay on
# this machine with the venv where it is. Rebuilds take seconds.
#
# Built in a clean staging dir (no pyproject.toml) with the venv's python
# directly, not `uv run`: uv would re-sync the venv and undo the pinned
# setuptools, and setuptools would otherwise read [project].dependencies as
# install_requires, which py2app refuses.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
STAGE="$REPO/build/_appstage"

if [ ! -x "$PY" ]; then
  echo "[build] no .venv found; run:  uv venv && uv sync --extra desktop" >&2
  exit 1
fi

echo "[build] pinning build tools in the venv ..."
uv pip install --quiet --python "$PY" 'py2app>=0.28' 'setuptools<71'

if [ ! -f "$REPO/mac/Listener.icns" ]; then
  echo "[build] generating the app icon ..."
  "$PY" "$REPO/mac/make_icon.py"
fi

echo "[build] cleaning previous build ..."
rm -rf "$REPO/build" "$REPO/dist"
mkdir -p "$STAGE"

echo "[build] building Listener.app (alias mode) ..."
( cd "$STAGE" && "$PY" "$REPO/setup_app.py" py2app -A )

echo "[build] placing app in ./dist ..."
mkdir -p "$REPO/dist"
rm -rf "$REPO/dist/Listener.app"
mv "$STAGE/dist/Listener.app" "$REPO/dist/Listener.app"
rm -rf "$STAGE"

echo
echo "[build] done -> dist/Listener.app"
echo "[build] launch it with:  open dist/Listener.app   (log: ~/.listener/listener.log)"
