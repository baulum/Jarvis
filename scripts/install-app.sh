#!/usr/bin/env bash
# Build and install J.A.R.V.I.S. into /Applications: the desktop app (which starts all services
# itself) and JarvisBridge. Both are signed with the same stable identity so macOS keeps the
# privacy grants (microphone, accessibility, screen recording) across updates.
# Usage: scripts/install-app.sh [--skip-build]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$HOME/Library/Application Support/Jarvis"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/opt/rustup/bin:$PATH"
BUILD=1; [ "${1:-}" = "--skip-build" ] && BUILD=0

if [ -z "${SIGN_IDENTITY:-}" ]; then
  SIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null | sed -n 's/.*"\(Apple Development: [^"]*\)".*/\1/p' | head -1)"
fi
export SIGN_IDENTITY="${SIGN_IDENTITY:--}"
[ "$SIGN_IDENTITY" = "-" ] && echo "⚠ Kein Apple-Development-Zertifikat gefunden: ad-hoc signiert, macOS-Rechte gehen bei Updates verloren."

if [ "$BUILD" = 1 ]; then
  (cd "$ROOT/bridge" && ./scripts/build-app.sh)
  # Tauri signs the bundle with APPLE_SIGNING_IDENTITY ("-" = ad-hoc).
  (cd "$ROOT/desktop" && APPLE_SIGNING_IDENTITY="$SIGN_IDENTITY" pnpm tauri build --bundles app)
fi

APPBUNDLE="$ROOT/desktop/src-tauri/target/release/bundle/macos/J.A.R.V.I.S..app"
[ -d "$APPBUNDLE" ] || { echo "✗ Desktop-App nicht gebaut: $APPBUNDLE"; exit 1; }

# Quit running copies (the app's own quit also stops the services; they restart on next launch).
pkill -TERM -f "J.A.R.V.I.S..app/Contents/MacOS/jarvis-desktop" 2>/dev/null || true
pkill -TERM -f "JarvisBridge.app/Contents/MacOS/JarvisBridge" 2>/dev/null || true
sleep 2

mkdir -p "$DATA"
printf '%s\n' "$ROOT" >"$DATA/root"   # where the app finds scripts/start.sh, the venv and config
ditto "$ROOT/bridge/build/JarvisBridge.app" /Applications/JarvisBridge.app
ditto "$APPBUNDLE" "/Applications/J.A.R.V.I.S..app"
codesign -v "/Applications/J.A.R.V.I.S..app" && codesign -v /Applications/JarvisBridge.app
echo "✓ Installiert: /Applications/J.A.R.V.I.S..app und /Applications/JarvisBridge.app"
open "/Applications/J.A.R.V.I.S..app"
echo "→ J.A.R.V.I.S. gestartet – Core, Sprache und Bridge starten automatisch mit."
