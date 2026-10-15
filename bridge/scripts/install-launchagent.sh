#!/usr/bin/env bash
# Installs and loads the com.jarvis.bridge LaunchAgent (per-user, starts at login, kept alive).
# Run scripts/build-app.sh first.
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="/Applications/JarvisBridge.app"; [ -d "$APP" ] || APP="$BRIDGE_DIR/build/JarvisBridge.app"
BIN="$APP/Contents/MacOS/JarvisBridge"
LABEL="com.jarvis.bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Jarvis"
DOMAIN="gui/$(id -u)"

[[ -x "$BIN" ]] || { echo "error: $BIN not found - run scripts/build-app.sh first" >&2; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
chmod 700 "$LOG_DIR"

# Stop a previously loaded instance.
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    echo "==> Unloading existing $LABEL"
    launchctl bootout "$DOMAIN/$LABEL" || true
fi

echo "==> Writing $PLIST"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$BIN</string>
	</array>
	<key>AssociatedBundleIdentifiers</key>
	<array>
		<string>com.jarvis.bridge</string>
	</array>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>ProcessType</key>
	<string>Interactive</string>
	<key>LimitLoadToSessionType</key>
	<string>Aqua</string>
	<key>StandardOutPath</key>
	<string>$LOG_DIR/bridge.log</string>
	<key>StandardErrorPath</key>
	<string>$LOG_DIR/bridge.log</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null

echo "==> Loading $LABEL"
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl enable "$DOMAIN/$LABEL"

sleep 1
if curl -fsS --max-time 3 http://127.0.0.1:8766/health; then
    echo
    echo "==> JarvisBridge is running."
else
    echo "warning: health check failed; see $LOG_DIR/bridge.log" >&2
fi
