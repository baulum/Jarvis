#!/usr/bin/env bash
# Stop the J.A.R.V.I.S. stack (PostgreSQL keeps running; it is a shared service).
set -uo pipefail
RUN="$HOME/Library/Application Support/Jarvis/run"
for name in voice core whisper; do
  if [ -f "$RUN/$name.pid" ]; then
    pid="$(cat "$RUN/$name.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      pkill -TERM -P "$pid" 2>/dev/null; kill -TERM "$pid" 2>/dev/null && echo "■ $name gestoppt"
    fi
    rm -f "$RUN/$name.pid"
  fi
done
pkill -TERM -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765" && echo "■ Telefon-Tunnel beendet" || true
# SIGTERM instead of AppleScript "quit": no Automation prompt when the desktop app runs this.
pkill -TERM -f "JarvisBridge.app/Contents/MacOS/JarvisBridge" && echo "■ JarvisBridge beendet" || true
