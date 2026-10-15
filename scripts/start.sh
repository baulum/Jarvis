#!/usr/bin/env bash
# Start the J.A.R.V.I.S. stack: whisper-server, JarvisBridge, core (+ Hermes gateway), voice daemon, desktop app.
# Usage: scripts/start.sh [--no-voice] [--no-app]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$HOME/Library/Application Support/Jarvis"
LOGS="$HOME/Library/Logs/Jarvis"
RUN="$DATA/run"
mkdir -p "$LOGS" "$RUN"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/opt/rustup/bin:$PATH"
VOICE=1; APP=1
for a in "$@"; do case "$a" in --no-voice) VOICE=0;; --no-app) APP=0;; esac; done

running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }
start_bg() {  # name, command...
  local name="$1"; shift
  if running "$name"; then echo "✓ $name läuft bereits"; return; fi
  nohup "$@" >>"$LOGS/$name.log" 2>&1 &
  echo $! >"$RUN/$name.pid"
  echo "→ $name gestartet (pid $!)"
}
wait_http() {  # url, seconds
  for _ in $(seq 1 "$2"); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done
  echo "✗ $1 nicht erreichbar"; return 1
}

pgrep -x ollama >/dev/null || pgrep -f "Ollama.app" >/dev/null || open -ga Ollama
wait_http http://127.0.0.1:11434/api/version 30
brew services list | grep -q "postgresql@17.*started" || brew services start postgresql@17 >/dev/null

start_bg whisper whisper-server -m "$DATA/models/ggml-large-v3-turbo-q5_0.bin" --host 127.0.0.1 --port 8178 -l de -t 4
if [ -d "$ROOT/bridge/build/JarvisBridge.app" ] && ! curl -fsS -o /dev/null http://127.0.0.1:8766/health 2>/dev/null; then
  open -g "$ROOT/bridge/build/JarvisBridge.app" && echo "→ JarvisBridge gestartet"
fi
start_bg core bash -c "cd '$ROOT/core' && exec uv run --frozen jarvisctl serve"
wait_http http://127.0.0.1:8765/api/health 60 && echo "✓ Core bereit"
if [ "$VOICE" = 1 ]; then
  start_bg voice bash -c "cd '$ROOT/core' && exec uv run --frozen jarvisctl voice"
fi
if [ "$APP" = 1 ]; then
  APPDIR="$ROOT/desktop/src-tauri/target/release/bundle/macos"
  APPBUNDLE="$(ls -d "$APPDIR"/*.app 2>/dev/null | head -1 || true)"
  if [ -n "$APPBUNDLE" ]; then open "$APPBUNDLE"; echo "→ Desktop-App geöffnet"; else echo "ℹ Desktop-App noch nicht gebaut (scripts/setup.sh)"; fi
fi
echo "Logs: $LOGS"
