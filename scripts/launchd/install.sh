#!/usr/bin/env bash
# Optional: start Jarvis automatically at login (LaunchAgents in ~/Library/LaunchAgents).
# Remove again with scripts/launchd/uninstall.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LA="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/Jarvis"
DATA="$HOME/Library/Application Support/Jarvis"
UV="$(command -v uv)"
mkdir -p "$LA" "$LOGS"
PATHVAL="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

plist() {  # label, workdir, args...
  local label="$1" wd="$2"; shift 2
  local args=""; for a in "$@"; do args+="    <string>$a</string>\n"; done
  cat > "$LA/$label.plist" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>
$(printf "$args")  </array>
  <key>WorkingDirectory</key><string>$wd</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATHVAL</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/$label.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$label.log</string>
</dict></plist>
P
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$LA/$label.plist"
  echo "✓ $label"
}

plist com.jarvis.whisper "$ROOT" /opt/homebrew/bin/whisper-server -m "$DATA/models/ggml-large-v3-turbo-q5_0.bin" --host 127.0.0.1 --port 8178 -l de -t 4
plist com.jarvis.core "$ROOT/core" "$UV" run --frozen jarvisctl serve
if [ "${1:-}" = "--with-voice" ]; then
  plist com.jarvis.voice "$ROOT/core" "$UV" run --frozen jarvisctl voice
fi
[ -x "$ROOT/bridge/scripts/install-launchagent.sh" ] && "$ROOT/bridge/scripts/install-launchagent.sh"
echo "Fertig. Die Desktop-App kann ihren Login-Start selbst in den Einstellungen aktivieren."
