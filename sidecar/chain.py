"""Your model order: which model writes your notes, and which take over, in
turn, when it is busy or fails (1st, 2nd, 3rd, ...).

Stored in ~/.margin/chain.json, and only once you change it in the menu or on
the settings page. Until then Margin uses its default order, which is also
what an untouched install shows as the list to start from:

    1. Google Gemini, best available Flash model (3.5, then 3, then 3.6)
    2. Claude, through your Pro/Max plan (if its token is set)
    3. OpenRouter
    4. Gemini Lite, the last resort (its notes are rewritten later)

Each entry is {"provider": ..., "model": ...}: a provider from the catalog,
a subscription (claude, codex, gemini-cli, opencode, ...) or a local server,
and one of its models. "auto" and "auto-lite" are Margin's own Gemini
failover and its Lite tier; "auto" on OpenRouter is its own list.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CHAIN_PATH = Path(os.environ.get("MARGIN_CHAIN_FILE") or Path.home() / ".margin" / "chain.json")
MAX_ENTRIES = 8

# The built-in engine names (llm.DEFAULT_CHAIN, MARGIN_ENGINES) as entries.
BUILT_IN = {
    "gemini": {"provider": "google", "model": "auto"},
    "gemini-lite": {"provider": "google", "model": "auto-lite"},
    "openrouter": {"provider": "openrouter", "model": "auto"},
}


class ChainProblem(ValueError):
    pass


def from_name(name: str, claude_model: str = "sonnet") -> dict:
    if name == "claude":
        return {"provider": "claude", "model": claude_model}
    return dict(BUILT_IN.get(name) or {"provider": "custom", "model": name})


def load() -> list[dict] | None:
    """Your saved order, or None if you have not set one."""
    if not CHAIN_PATH.exists():
        return None
    try:
        data = json.loads(CHAIN_PATH.read_text())
        entries = data.get("entries") if isinstance(data, dict) else data
        return [_clean(e) for e in entries] or None
    except (ValueError, TypeError, ChainProblem):
        return None  # a broken file never stops notes: the default order applies


def _clean(entry) -> dict:
    if not isinstance(entry, dict):
        raise ChainProblem("each entry is a provider and a model")
    provider, model = str(entry.get("provider") or "").strip(), str(entry.get("model") or "").strip()
    if not provider or not model or len(provider) > 64 or len(model) > 200:
        raise ChainProblem("each entry needs a provider and a model")
    return {"provider": provider, "model": model}


def save(entries: list) -> list[dict]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ChainProblem(f"keep between 1 and {MAX_ENTRIES} models in the order")
    cleaned = [_clean(e) for e in entries]
    CHAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHAIN_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"entries": cleaned}, indent=1))
    tmp.replace(CHAIN_PATH)
    return cleaned


def reset() -> None:
    CHAIN_PATH.unlink(missing_ok=True)
