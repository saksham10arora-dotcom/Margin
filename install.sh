#!/usr/bin/env bash
# Margin, installed in one go.
#
#   ./install.sh
#
# 1. sets up the sidecar (the part that writes notes) and its notebook kernel,
# 2. on a Mac, has it start at login, so nothing needs a terminal again,
# 3. opens Chrome's extensions page for the last step, which Chrome keeps for
#    you to do: loading the extension from this folder.
#
# Keep this folder where it is afterwards: Chrome and the sidecar run from it.
set -euo pipefail
cd "$(dirname "$0")"
bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }

bold "Where should Margin save your notes?"
# Updating? The folder you already use, so pressing Return never moves your notes.
SAVED="$HOME/.margin/notes-folder"
DEFAULT="$(cat "$SAVED" 2>/dev/null || true)"
[ -n "$DEFAULT" ] || DEFAULT="$(curl -s --max-time 2 http://127.0.0.1:8766/health 2>/dev/null \
  | python3 -c 'import sys, json; print(json.load(sys.stdin).get("vault") or "")' 2>/dev/null || true)"
[ -n "$DEFAULT" ] || DEFAULT="$HOME/MarginNotes"
echo "  A folder inside your Obsidian vault works best, for example ~/Documents/Vault/Lectures."
read -r -p "  Folder [$DEFAULT]: " VAULT || true
VAULT="${VAULT:-$DEFAULT}"
VAULT="${VAULT/#\~/$HOME}"
mkdir -p "$VAULT" "$HOME/.margin"
printf '%s\n' "$VAULT" > "$SAVED"
export MARGIN_VAULT_PATH="$VAULT"

bold "Setting up"
./scripts/setup.sh

if [ "$(uname)" = "Darwin" ]; then
  bold "Starting it at login"
  ./scripts/login-item.sh install
  echo "  If macOS asks whether \"Margin Sidecar\" may access a folder, click Allow."
else
  bold "Start it with: MARGIN_VAULT_PATH=\"$VAULT\" ./scripts/start.sh"
fi

bold "Last step, in Chrome"
echo "  1. Turn on Developer mode (top right of the page that just opened)."
echo "  2. Click Load unpacked and choose:"
echo "       $(pwd)/extension"
echo "  3. Open a Udemy or YouTube lecture. For your models: ... then Settings in the Margin panel."
if [ "$(uname)" = "Darwin" ]; then
  open -a "Google Chrome" "chrome://extensions" 2>/dev/null || true
  open -R "$(pwd)/extension/manifest.json" 2>/dev/null || true
fi
