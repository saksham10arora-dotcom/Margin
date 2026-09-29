#!/usr/bin/env bash
# Start the Margin sidecar in the foreground.
#
# Notes go to $MARGIN_VAULT_PATH (default ~/MarginNotes). Point it at a
# folder inside your Obsidian vault to have "Open in Obsidian" work:
#   MARGIN_VAULT_PATH="$HOME/Documents/Vault/Lectures" ./scripts/start.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x venv/bin/python ]; then
  echo "Run ./scripts/setup.sh first." >&2
  exit 1
fi
PORT="${MARGIN_PORT:-8766}"
echo "Margin sidecar on http://127.0.0.1:${PORT}  →  notes in ${MARGIN_VAULT_PATH:-$HOME/MarginNotes}"
exec venv/bin/python -m uvicorn sidecar.main:app --host 127.0.0.1 --port "$PORT"
