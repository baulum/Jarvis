#!/usr/bin/env bash
set -uo pipefail
for label in com.jarvis.voice com.jarvis.core com.jarvis.tts com.jarvis.whisper; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "■ $label entfernt"
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
[ -x "$ROOT/bridge/scripts/uninstall-launchagent.sh" ] && "$ROOT/bridge/scripts/uninstall-launchagent.sh"
