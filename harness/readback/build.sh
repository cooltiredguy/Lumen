#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/LumenReadback"
echo "[readback/build.sh] building → $OUT"
swiftc -O -o "$OUT" "$SCRIPT_DIR/LumenReadback.swift" \
    -framework Cocoa -framework ScreenCaptureKit \
    -framework CoreGraphics -framework CoreMedia \
    -framework CoreVideo
echo "[readback/build.sh] done: $OUT"
