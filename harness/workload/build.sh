#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SCRIPT_DIR/LumenWorkload"
echo "[workload/build.sh] building → $OUT"
swiftc -O -o "$OUT" "$SCRIPT_DIR/LumenWorkload.swift" \
    -framework Cocoa -framework Metal -framework QuartzCore \
    -framework CoreVideo
echo "[workload/build.sh] done: $OUT"
