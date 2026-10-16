#!/usr/bin/env bash
# Stop the J.A.R.V.I.S. stack (Ollama and PostgreSQL keep running; they are shared services).
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
# SIGTERM instead of AppleScript "quit": no Automation prompt when the desktop app runs this.
pkill -TERM -f "JarvisBridge.app/Contents/MacOS/JarvisBridge" && echo "■ JarvisBridge beendet" || true
