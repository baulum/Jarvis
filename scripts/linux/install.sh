#!/usr/bin/env bash
# J.A.R.V.I.S. on Linux: checks dependencies, downloads models, sets up client, device agent and/or core.
# All arguments go to the installer (installer/jarvis_installer), e.g.:
#   scripts/linux/install.sh --check
#   scripts/linux/install.sh --roles device --core-url https://100.101.102.103:8767 --code <Code> --fingerprint <SHA-256>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv (Python-Umgebungen) fehlt – der Installer braucht es."
  read -r -p "Jetzt installieren? Quelle: https://astral.sh/uv/install.sh (J/n) " answer
  case "${answer:-j}" in j|J|ja|y|yes) ;; *) exit 1;; esac
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

PYTHONPATH="$ROOT/installer" exec uv run --no-project --python 3.12 python -m jarvis_installer --repo "$ROOT" "$@"
