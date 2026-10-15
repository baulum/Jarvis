#!/usr/bin/env bash
# Stops and removes the com.jarvis.bridge LaunchAgent. Leaves token, policy and logs in place.
set -euo pipefail

LABEL="com.jarvis.bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    echo "==> Unloading $LABEL"
    launchctl bootout "$DOMAIN/$LABEL" || true
else
    echo "==> $LABEL is not loaded"
fi

if [[ -f "$PLIST" ]]; then
    rm -f "$PLIST"
    echo "==> Removed $PLIST"
fi

echo "Done. Token/policy remain in ~/Library/Application Support/Jarvis, logs in ~/Library/Logs/Jarvis."
