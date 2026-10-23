#!/usr/bin/env bash
# Start the J.A.R.V.I.S. core on Linux: whisper-server, local voice (Piper), core, voice daemon.
# Counterpart of scripts/start.sh on the Mac; the desktop app runs it at start (not when it is only a client).
# Usage: scripts/linux/start.sh [--no-voice] [--no-app]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis"
LOGS="${XDG_STATE_HOME:-$HOME/.local/state}/jarvis/logs"
RUN="$DATA/run"
MODELS="$DATA/models"
mkdir -p "$LOGS" "$RUN"
export PATH="$HOME/.local/bin:$DATA/bin:$PATH"
VOICE=1; APP=1
for a in "$@"; do case "$a" in --no-voice) VOICE=0;; --no-app) APP=0;; esac; done
# headless server: no microphone session, no desktop app
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then VOICE=0; APP=0; fi

running() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }
start_bg() {  # name, command...
  local name="$1"; shift
  if running "$name"; then echo "✓ $name läuft bereits"; return; fi
  setsid nohup "$@" >>"$LOGS/$name.log" 2>&1 &
  echo $! >"$RUN/$name.pid"
  echo "→ $name gestartet (pid $!)"
}
wait_http() {  # url, seconds
  for _ in $(seq 1 "$2"); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done
  echo "✗ $1 nicht erreichbar"; return 1
}

MODEL=""
for m in ggml-large-v3-turbo-q5_0.bin ggml-small-q5_1.bin; do
  [ -f "$MODELS/$m" ] && { MODEL="$MODELS/$m"; break; }
done
if command -v whisper-server >/dev/null && [ -n "$MODEL" ]; then
  start_bg whisper whisper-server -m "$MODEL" --host 127.0.0.1 --port 8178 -l de -t 4 -ac 768 -nf
else
  echo "ℹ whisper-server oder Modell fehlt – Spracherkennung aus (oder voice.stt.server_url auf den Mac)"
fi
TTS_PY="$DATA/tts-local/.venv/bin/python"
VOICE_MODEL="$MODELS/piper/de_DE-thorsten-high.onnx"
if [ -x "$TTS_PY" ] && [ -f "$VOICE_MODEL" ]; then
  start_bg tts "$TTS_PY" -I "$ROOT/scripts/local-tts/piper_server.py" --port 8179 --model "$VOICE_MODEL"
fi
start_bg core bash -c "cd '$ROOT/core' && exec uv run --frozen jarvisctl serve"
wait_http http://127.0.0.1:8765/api/health 90 && echo "✓ Core bereit"
if [ "$VOICE" = 1 ]; then
  start_bg voice bash -c "cd '$ROOT/core' && exec uv run --frozen jarvisctl voice"
fi
if [ "$APP" = 1 ]; then
  APPIMAGE="$(ls "$ROOT"/desktop/src-tauri/target/release/bundle/appimage/*.AppImage 2>/dev/null | head -1 || true)"
  if command -v jarvis-desktop >/dev/null; then (setsid jarvis-desktop >/dev/null 2>&1 &); echo "→ Desktop-App geöffnet"
  elif [ -n "$APPIMAGE" ]; then (setsid "$APPIMAGE" >/dev/null 2>&1 &); echo "→ Desktop-App geöffnet"
  else echo "ℹ Desktop-App nicht installiert"; fi
fi
echo "Logs: $LOGS"
