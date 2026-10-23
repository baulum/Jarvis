#!/usr/bin/env bash
# Stop the services started by scripts/linux/start.sh (PostgreSQL keeps running; it is a shared service).
set -uo pipefail
RUN="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis/run"
for name in voice core tts whisper; do
  if [ -f "$RUN/$name.pid" ]; then
    pid="$(cat "$RUN/$name.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      # started with setsid: the pid is also the process group (uv → python → Claude Code …)
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
      echo "■ $name gestoppt"
    fi
    rm -f "$RUN/$name.pid"
  fi
done
pkill -TERM -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765" && echo "■ Telefon-Tunnel beendet" || true
