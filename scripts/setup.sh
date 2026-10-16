#!/usr/bin/env bash
# Idempotent setup of J.A.R.V.I.S. on macOS (Apple Silicon). Safe to re-run.
# Never touches existing projects; asks before anything that needs admin rights (nothing here does).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$HOME/Library/Application Support/Jarvis"
MODELS="$DATA/models"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/opt/rustup/bin:$PATH"
step() { printf "\n\033[36m▸ %s\033[0m\n" "$*"; }

step "Homebrew-Pakete prüfen"
for pkg in postgresql@17 pgvector whisper-cpp ffmpeg uv node pnpm rustup cloudflared; do
  brew list --formula "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done
command -v claude >/dev/null || echo "⚠ Claude Code CLI fehlt (für Coding-Jobs): https://claude.com/claude-code"
rustup show active-toolchain >/dev/null 2>&1 || rustup default stable

step "PostgreSQL + Datenbank"
brew services list | grep -q "postgresql@17.*started" || brew services start postgresql@17
sleep 2
PSQL=/opt/homebrew/opt/postgresql@17/bin/psql
if ! $PSQL -h localhost -d postgres -tAc "select 1 from pg_roles where rolname='jarvis'" | grep -q 1; then
  PW="$(openssl rand -hex 24)"
  (cd "$ROOT/core" && printf '%s' "$PW" | uv run python -c "import sys,keyring; keyring.set_password('jarvis','postgres',sys.stdin.read())")
  $PSQL -h localhost -d postgres -c "CREATE ROLE jarvis LOGIN PASSWORD '$PW';" -c "CREATE DATABASE jarvis OWNER jarvis;"
  $PSQL -h localhost -d jarvis -c "CREATE EXTENSION IF NOT EXISTS vector;"
fi

step "Python-Umgebung"
(cd "$ROOT/core" && uv sync --all-extras && uv run playwright install chromium && uv run alembic upgrade head)
bash "$ROOT/scripts/build-audio-helper.sh"

step "Embedding-Modell für das Gedächtnis (läuft im Core, ~1,1 GB, kein Ollama)"
(cd "$ROOT/core" && uv run python -c "
import asyncio; from jarvis.config import get_settings; from jarvis.memory.embeddings import LocalEmbedder
s = get_settings(); asyncio.run(LocalEmbedder(s.paths.data_dir / 'models' / 'fastembed', s.llm.embedding_model).embed(['ok']))
print('✓ Embedding-Modell bereit')")

# Legacy option only: Hermes + local Ollama model (not used by default).
if [ "${JARVIS_WITH_HERMES:-0}" = 1 ] && command -v ollama >/dev/null && ! ollama list | grep -q "jarvis-brain"; then
  ollama list | grep -q "qwen3.5:4b" || ollama pull qwen3.5:4b
  printf 'FROM qwen3.5:4b\nPARAMETER num_ctx 65536\nPARAMETER temperature 0.4\n' > /tmp/jarvis-Modelfile
  ollama create jarvis-brain -f /tmp/jarvis-Modelfile && rm /tmp/jarvis-Modelfile
fi

step "Sprach- und Wake-Word-Modelle"
mkdir -p "$MODELS"
[ -f "$MODELS/ggml-large-v3-turbo-q5_0.bin" ] || curl -fL -o "$MODELS/ggml-large-v3-turbo-q5_0.bin" \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin
for f in melspectrogram.onnx embedding_model.onnx hey_jarvis_v0.1.onnx; do
  [ -f "$MODELS/$f" ] || curl -fL -o "$MODELS/$f" "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/$f"
done

step "Claude Code (Agent-Gehirn)"
env -u ANTHROPIC_API_KEY claude auth status | grep -q '"loggedIn": true' || echo "⚠ Claude Code nicht angemeldet: im Terminal 'claude' starten und /login ausführen"

if [ "${JARVIS_WITH_HERMES:-0}" = 1 ]; then
step "Hermes Agent (optional)"
if ! command -v hermes >/dev/null; then
  echo "Installiere Hermes Agent über den offiziellen Installer (ohne eigene Browser-/Computer-Use-Tools)…"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/hermes-install.sh
  bash /tmp/hermes-install.sh --non-interactive --skip-browser --skip-computer-use
fi
[ -d "$HOME/.hermes/profiles/jarvis" ] || hermes profile create jarvis --description "J.A.R.V.I.S. (local Ollama + Jarvis MCP tools)"

(cd "$ROOT/core" && uv run jarvisctl setup-hermes)
(cd "$ROOT/core" && uv run python - <<'PY'
from pathlib import Path
from jarvis.agent.hermes_setup import _update_env_file
from jarvis.secrets import secret_store
key = secret_store.ensure_random("hermes-default-api-key")
_update_env_file(Path.home() / ".hermes/.env", {"API_SERVER_ENABLED": "true", "API_SERVER_KEY": key,
                                                "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": "8642"})
print("✓ Hermes-API-Listener (127.0.0.1:8642) aktiviert")
PY
)
fi

step "Jarvis-Geräte"
(cd "$ROOT/core" && uv run jarvisctl init)

step "Apps bauen und nach /Applications installieren"
(cd "$ROOT/desktop" && pnpm install --frozen-lockfile)
"$ROOT/scripts/install-app.sh"

step "Prüfung"
(cd "$ROOT/core" && uv run jarvisctl doctor) || true
echo -e "\nFertig. J.A.R.V.I.S. liegt in Programme und startet alle Dienste selbst."
