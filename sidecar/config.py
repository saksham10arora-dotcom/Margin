import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# The project was called Marginalia before it was called Margin. Every setting
# is read as MARGIN_* first and falls back to the old MARGINALIA_* name, so a
# rename does not silently empty someone's vault or flip their engine back to
# the default. This project has already shipped exactly that bug once (a
# sidecar restart quietly serving an empty default vault), which is why the
# fallback exists rather than a clean break.
_LEGACY_PREFIX = "MARGINALIA_"


def _setting(name: str, default: str | None = None) -> str | None:
    current = os.environ.get(f"MARGIN_{name}")
    if current is not None:
        return current
    legacy = os.environ.get(f"{_LEGACY_PREFIX}{name}")
    if legacy is not None:
        logger.warning(
            "%s%s is deprecated, rename it to MARGIN_%s", _LEGACY_PREFIX, name, name
        )
        return legacy
    return default


def _default_vault_path() -> Path:
    """~/MarginNotes, unless a pre-rename ~/MarginaliaNotes already has notes
    in it -- picking the empty new folder over someone's existing notes would
    look exactly like data loss."""
    new = Path.home() / "MarginNotes"
    legacy = Path.home() / "MarginaliaNotes"
    if not new.exists() and legacy.exists() and any(legacy.rglob("*.md")):
        logger.warning(
            "Using legacy vault %s; rename it to %s or set MARGIN_VAULT_PATH", legacy, new
        )
        return legacy
    return new


# Defaults to ~/MarginNotes so a fresh clone has somewhere to write;
# override with MARGIN_VAULT_PATH to point at an existing Obsidian vault.
# Created on import if it doesn't exist yet -- main.py mounts it as a static
# file directory at startup, which raises immediately if the folder is missing.
_vault_setting = _setting("VAULT_PATH")
VAULT_PATH = Path(_vault_setting) if _vault_setting else _default_vault_path()
VAULT_PATH.mkdir(parents=True, exist_ok=True)

# v2 port. v1 runs on 8765; keeping them apart means both can be installed
# at once and falling back to v1 is a matter of which sidecar you start.
PORT = int(_setting("PORT", "8766"))

# Where API keys come from, in this order: the environment, Margin's own key
# file (the settings page writes it), then ~/.config/keys.env for people who
# keep their keys there. A name set in several places uses all of them.
MARGIN_KEYS_PATH = Path(os.environ.get("MARGIN_KEYS_FILE") or Path.home() / ".margin" / "keys.env")
SHARED_KEYS_PATH = Path.home() / ".config" / "keys.env"
KEYS_PATH = SHARED_KEYS_PATH  # the file older versions read

# Auto-wikilink concepts in generated notes against titles already in the
# vault, turning isolated notes into a connected Obsidian graph. On by
# default -- it is most of the reason to keep notes in Obsidian rather than
# a folder of text files. Set MARGIN_AUTOLINK=0 to write plain prose.
AUTOLINK = _setting("AUTOLINK", "1") not in ("0", "false", "False")
# Per generated section. A cap because a lecture that name-drops thirty
# concepts should not produce a section that is more link than sentence.
MAX_AUTOLINKS_PER_SECTION = int(_setting("MAX_AUTOLINKS", "8"))


def _numbered(env_name: str, pairs) -> list[str]:
    pattern = re.compile(rf"^{re.escape(env_name)}(?:_(\d+))?$")
    numbered: dict[int, str] = {}
    for name, value in pairs:
        m = pattern.match(name)
        if m and value.strip():
            numbered[int(m.group(1)) if m.group(1) else 1] = value.strip()
    return [numbered[i] for i in sorted(numbered)]


def _file_pairs(path: Path):
    if not path.exists():
        return []
    pairs = []
    for line in path.read_text().splitlines():
        m = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"?([^"\n]*)"?\s*$', line)
        if m and not line.strip().startswith("#"):
            pairs.append((m.group(1), m.group(2)))
    return pairs


def key_sources() -> list[tuple[str, list[tuple[str, str]]]]:
    return [("environment", list(os.environ.items())), ("margin", _file_pairs(MARGIN_KEYS_PATH)),
            ("shared", _file_pairs(KEYS_PATH))]


def load_api_keys(env_name: str) -> list[str]:
    """All configured keys for one env var name, in numbered order:
    NAME, then NAME_2, NAME_3, ... -- e.g. one per account, each with its own
    independent daily free-tier quota. Ignores commented-out lines (leading `#`).
    Read from the environment, Margin's key file and ~/.config/keys.env.

    Every engine in llm.py reads its keys through this, so any of them can be
    given several accounts' keys and rotate across their free tiers.
    """
    found: list[str] = []
    for _where, pairs in key_sources():
        for value in _numbered(env_name, pairs):
            if value not in found:
                found.append(value)
    return found


def key_location(env_name: str) -> str | None:
    """Where a key is set ("environment", "margin", "shared"), never its value."""
    for where, pairs in key_sources():
        if _numbered(env_name, pairs):
            return where
    return None


def load_gemini_api_keys() -> list[str]:
    return load_api_keys("GEMINI_API_KEY")
