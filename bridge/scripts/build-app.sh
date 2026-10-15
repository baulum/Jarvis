#!/usr/bin/env bash
# Builds JarvisBridge in release mode and assembles bridge/build/JarvisBridge.app.
#
# Signing: ad-hoc by default. macOS TCC permissions (Accessibility, Screen Recording,
# Automation) are bound to the code signature; an ad-hoc signature changes on every
# rebuild, so permissions may have to be re-granted. To keep grants stable, sign with
# a real identity:  SIGN_IDENTITY="Apple Development: Name (TEAMID)" scripts/build-app.sh
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$BRIDGE_DIR/build/JarvisBridge.app"
SIGN_IDENTITY="${SIGN_IDENTITY:--}"

cd "$BRIDGE_DIR"

echo "==> swift build -c release"
swift build -c release

BIN_DIR="$(swift build -c release --show-bin-path)"
BIN="$BIN_DIR/JarvisBridge"
[[ -x "$BIN" ]] || { echo "error: binary not found at $BIN" >&2; exit 1; }

echo "==> Assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/JarvisBridge"
cp "$BRIDGE_DIR/Support/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
plutil -lint "$APP/Contents/Info.plist" >/dev/null

echo "==> Signing (identity: $SIGN_IDENTITY)"
codesign --force --sign "$SIGN_IDENTITY" --options runtime --timestamp=none \
    --identifier com.jarvis.bridge \
    --entitlements "$BRIDGE_DIR/Support/JarvisBridge.entitlements" \
    "$APP"
codesign --verify --strict --verbose=1 "$APP"

echo "==> Done: $APP"
echo "    Run directly:  \"$APP/Contents/MacOS/JarvisBridge\""
echo "    Or install the LaunchAgent:  scripts/install-launchagent.sh"
