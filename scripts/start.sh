#!/usr/bin/env bash
# Start the J.A.R.V.I.S. stack: whisper-server, JarvisBridge, core, voice daemon, desktop app (no Ollama).
# Usage: scripts/start.sh [--no-voice] [--no-app]
# The installed app (/Applications) is kept current: when the desktop or bridge code changed since the last
# scripts/install-app.sh, the app is rebuilt and reinstalled in the background (also with --no-app, which the app
# itself and the self-restart after coding jobs use); it then reopens if it was running or was asked for.
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

# Paths whose changes need a new app build (desktop UI, Tauri shell, JarvisBridge); see core/src/jarvis/desktop_build.py
APP_PATHS="desktop/src desktop/index.html desktop/package.json desktop/vite.config.ts desktop/src-tauri/src desktop/src-tauri/tauri.conf.json desktop/src-tauri/Cargo.toml bridge/Sources bridge/Support bridge/Package.swift"
INSTALLED_APP="/Applications/J.A.R.V.I.S..app"
app_outdated() {  # true when the installed app is missing or older than the app code in the repository
  local head built
  head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)" || return 1   # not a git checkout: nothing to compare
  [ -d "$INSTALLED_APP" ] || return 0
  built="$(cat "$DATA/app-build.commit" 2>/dev/null || true)"
  [ -n "$built" ] && git -C "$ROOT" cat-file -e "$built^{commit}" 2>/dev/null || return 0
  # shellcheck disable=SC2086 # APP_PATHS is a list of paths
  [ "$(git -C "$ROOT" rev-list --count "$built..HEAD" -- $APP_PATHS)" -gt 0 ]
}
update_app() {  # builds + installs in the background; returns 0 when an installation runs
  if running app-install; then echo "✓ App-Installation läuft bereits ($LOGS/app-install.log)"; return 0; fi
  app_outdated || return 1
  local head; head="$(git -C "$ROOT" rev-parse HEAD)"
  if [ "$(cat "$DATA/app-install.failed" 2>/dev/null || true)" = "$head" ]; then
    echo "ℹ App-Update für diesen Stand ist schon fehlgeschlagen – siehe $LOGS/app-install.log, manuell: scripts/install-app.sh"
    return 1
  fi
  if ! command -v pnpm >/dev/null || ! command -v cargo >/dev/null || ! command -v swift >/dev/null; then
    echo "ℹ App veraltet, aber pnpm/cargo/swift fehlen – scripts/setup.sh"; return 1
  fi
  local flag=""; [ "$APP" = 1 ] || flag="--reopen"
  # detached: the installer quits and reopens the very app that may have started this script
  # shellcheck disable=SC2016 # expanded by the inner shell
  nohup /bin/bash -c 'bash "$1/scripts/install-app.sh" $3 || { printf "%s\n" "$4" >"$2/app-install.failed"; exit 1; }' \
    _ "$ROOT" "$DATA" "$flag" "$head" >>"$LOGS/app-install.log" 2>&1 </dev/null &
  echo $! >"$RUN/app-install.pid"
  disown 2>/dev/null || true
  echo "→ Desktop-App veraltet: Neubau und Installation im Hintergrund (pid $!, $LOGS/app-install.log)"
}

brew services list | grep -q "postgresql@17.*started" || brew services start postgresql@17 >/dev/null

start_bg whisper whisper-server -m "$DATA/models/ggml-large-v3-turbo-q5_0.bin" --host 127.0.0.1 --port 8178 -l de -t 4 -ac 768 -nf  # -ac/-nf: halbe STT-Latenz (gemessen 1,05 → 0,55 s); lange Äußerungen teilt der Core
TTS_PY="$DATA/tts-local/.venv/bin/python"   # lokale Jarvis-Stimme (scripts/setup.sh legt sie an)
if [ -x "$TTS_PY" ] && [ -f "$DATA/voices/jarvis_ref_short.wav" ]; then
  # --voices-dir: weitere Persönlichkeits-Stimmen (René Redo …), alle auf demselben Modell
  start_bg tts "$TTS_PY" -I "$ROOT/scripts/local-tts/server.py" --port 8179 --ref "$DATA/voices/jarvis_ref_short" --voices-dir "$ROOT/scripts/local-tts/voices"
fi
BRIDGE_APP=/Applications/JarvisBridge.app   # installed location (macOS privacy grants are tied to it)
[ -d "$BRIDGE_APP" ] || BRIDGE_APP="$ROOT/bridge/build/JarvisBridge.app"
if [ -d "$BRIDGE_APP" ] && ! curl -fsS -o /dev/null http://127.0.0.1:8766/health 2>/dev/null; then
  open -g "$BRIDGE_APP" && echo "→ JarvisBridge gestartet"
fi
start_bg core bash -c "cd '$ROOT/core' && exec uv run --frozen jarvisctl serve"
wait_http http://127.0.0.1:8765/api/health 60 && echo "✓ Core bereit"
if [ "$VOICE" = 1 ]; then
  bash "$ROOT/scripts/build-audio-helper.sh" || echo "ℹ Audiohelfer nicht gebaut – Fallback ohne Echo-Unterdrückung"
  start_bg voice bash -c "cd '$ROOT/core' && exec uv run --frozen jarvisctl voice"
fi
UPDATING=0; update_app && UPDATING=1
if [ "$APP" = 1 ]; then
  if [ -d "$INSTALLED_APP" ]; then  # the installed copy: macOS privacy grants belong to it
    open "$INSTALLED_APP"
    [ "$UPDATING" = 1 ] && echo "→ Desktop-App geöffnet (startet nach dem Update neu)" || echo "→ Desktop-App geöffnet"
  elif [ "$UPDATING" = 0 ]; then
    echo "ℹ Desktop-App noch nicht installiert (scripts/install-app.sh)"
  fi
fi
echo "Logs: $LOGS"
