#!/usr/bin/env bash
# Builds the native audio helper (microphone + playback with macOS echo cancellation) for the voice daemon.
# Rebuilds only when the source is newer than the binary. Output: ~/Library/Application Support/Jarvis/bin/jarvis-audio
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/core/native/JarvisAudio/main.swift"
OUT="$HOME/Library/Application Support/Jarvis/bin/jarvis-audio"
mkdir -p "$(dirname "$OUT")"
if [ -x "$OUT" ] && [ "$OUT" -nt "$SRC" ]; then echo "✓ jarvis-audio aktuell"; exit 0; fi
command -v swiftc >/dev/null || { echo "ℹ swiftc fehlt (Xcode Command Line Tools) – Sprachausgabe nutzt den Fallback"; exit 0; }
swiftc -O -o "$OUT.tmp" "$SRC" && mv "$OUT.tmp" "$OUT" && echo "✓ jarvis-audio gebaut"
