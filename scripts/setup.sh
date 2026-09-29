#!/usr/bin/env bash
# One-time setup for Margin v2. Safe to re-run: every step checks first.
#
#   ./scripts/setup.sh                 sidecar env + notebook kernel + checks
#   ./scripts/setup.sh --whisper-model also download a Whisper model (~150 MB)
#
# Nothing here touches your own Python environments. The sidecar gets its own
# venv in this folder; course notebooks run in ~/.margin/nbenv, registered as
# the Jupyter kernel "margin".
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

say "1. Sidecar environment"
# Python 3.11 or newer (the sidecar uses tomllib). uv brings its own.
if ! have uv; then
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    warn "Python 3.11 or newer is needed (found: $(python3 --version 2>&1 || echo none))."
    warn "Easiest: install uv (curl -LsSf https://astral.sh/uv/install.sh | sh), or: brew install python@3.12"
    exit 1
  fi
fi
if have uv; then
  [ -d venv ] || uv venv venv --python 3.12 -q
  uv pip install -q --python venv/bin/python -r sidecar/requirements.txt
else
  [ -d venv ] || python3 -m venv venv
  venv/bin/pip install -q -r sidecar/requirements.txt
fi
ok "venv/ ready ($(venv/bin/python --version))"

say "2. Notebook kernel (where course code is run and checked)"
NB="$HOME/.margin/nbenv"
if [ ! -x "$NB/bin/python" ]; then
  mkdir -p "$HOME/.margin"
  if have uv; then uv venv "$NB" --python 3.12 -q; else python3 -m venv "$NB"; fi
fi
PKGS="ipykernel numpy pandas matplotlib scipy yfinance statsmodels scikit-learn"
if have uv; then uv pip install -q --python "$NB/bin/python" $PKGS; else "$NB/bin/pip" install -q $PKGS; fi
"$NB/bin/python" -m ipykernel install --user --name margin --display-name "Margin (course notebooks)" >/dev/null
ok "kernel 'margin' registered ($NB)"

say "3. Listening (only used for lectures with no captions)"
if have ffmpeg; then ok "ffmpeg"; else warn "ffmpeg missing: brew install ffmpeg"; fi
if have whisper-cli; then ok "whisper.cpp"; else warn "whisper.cpp missing: brew install whisper-cpp"; fi
MODELS="$HOME/.margin/models"
if [ "${1:-}" = "--whisper-model" ] && ! ls "$MODELS"/ggml-*.bin >/dev/null 2>&1; then
  mkdir -p "$MODELS"
  curl -L --fail -o "$MODELS/ggml-small.en.bin" \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin
fi
MODEL=$(venv/bin/python -c "from sidecar.asr import find_whisper_model as f; m=f(); print(m or '')")
if [ -n "$MODEL" ]; then ok "model: $MODEL"; else warn "no Whisper model: re-run with --whisper-model"; fi

say "4. A model to write the notes"
READY=$(venv/bin/python -c "
from sidecar import llm, providers
print(sum(providers.entry_status(e)['ready'] for e in llm.chain_entries()))" 2>/dev/null || echo 0)
if [ "${READY:-0}" -gt 0 ]; then
  ok "a model is ready to write notes"
else
  warn "no model yet: after loading the extension, open Margin's Settings (... then Settings)"
  warn "and add a key (a Gemini key is free at aistudio.google.com) or sign in to a subscription."
fi

say "Done."
echo "  Start the sidecar:  ./scripts/start.sh"
echo "  Load the extension: chrome://extensions → Developer mode → Load unpacked → $(pwd)/extension"
echo "  Optional, start at login: ./scripts/login-item.sh install"
