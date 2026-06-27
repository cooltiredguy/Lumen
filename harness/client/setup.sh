#!/usr/bin/env bash
# Usage: setup.sh <build-root>
# Clones moonlight-qt at the pinned commit, applies trace.patch, builds.
# build-root: directory where the clone lives (created if absent).
# Output: <build-root>/moonlight-qt/app/Moonlight.app
#
# Run on each machine that needs the instrumented client (dev box + mini).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PINNED_COMMIT="$(cat "$SCRIPT_DIR/pinned-commit.txt")"
BUILD_ROOT="${1:?usage: setup.sh <build-root>}"

CLONE_DIR="$BUILD_ROOT/moonlight-qt"
APP_PATH="$CLONE_DIR/app/Moonlight.app"
BINARY="$APP_PATH/Contents/MacOS/Moonlight"

# --- Qt path ---
# Homebrew Qt on Apple Silicon:
export PATH="/opt/homebrew/opt/qt/bin:$PATH"
QMK6=$(command -v qmake6 2>/dev/null || echo "")
if [ -z "$QMK6" ]; then
  echo "ERROR: qmake6 not found. Install Qt: brew install qt" >&2
  exit 1
fi

# --- Clone if needed ---
if [ ! -d "$CLONE_DIR/.git" ]; then
  echo "[setup] Cloning moonlight-qt..."
  git clone https://github.com/moonlight-stream/moonlight-qt.git "$CLONE_DIR"
  cd "$CLONE_DIR"
  git checkout "$PINNED_COMMIT"
  git submodule update --init --recursive
else
  echo "[setup] Clone exists at $CLONE_DIR"
  cd "$CLONE_DIR"
fi

# --- Check if patch already applied (idempotent) ---
if grep -q 'client_trace' app/streaming/video/ffmpeg.cpp 2>/dev/null; then
  echo "[setup] Patch already applied"
else
  echo "[setup] Applying trace.patch..."
  git apply "$SCRIPT_DIR/trace.patch"
fi

# --- Build ---
echo "[setup] Building (release)..."
"$QMK6" moonlight-qt.pro
make release -j"$(sysctl -n hw.logicalcpu)"

echo "[setup] Built: $BINARY"
echo "$BINARY"
