"""Adding and removing API keys from Margin's settings page.

Keys are written to Margin's own file, ~/.margin/keys.env, readable by you
alone (mode 600). A key only ever goes in: nothing returns one, the settings
page learns only whether a name is set and where. Your environment and
~/.config/keys.env are read (see config.load_api_keys) but never written.
"""
from __future__ import annotations

import os
import re

from sidecar import config

NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class KeyProblem(ValueError):
    pass


def _check(name: str) -> None:
    if not NAME.match(name or ""):
        raise KeyProblem("A key's name is capital letters, digits and underscores, like GROQ_API_KEY")


def _lines() -> list[str]:
    path = config.MARGIN_KEYS_PATH
    return path.read_text().splitlines() if path.exists() else []


def _write(lines: list[str]) -> None:
    path = config.MARGIN_KEYS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def set_key(name: str, value: str) -> None:
    _check(name)
    value = (value or "").strip()
    if len(value) < 8 or len(value) > 2000 or re.search(r"\s", value) or not value.isprintable():
        raise KeyProblem("That does not look like a key: no spaces or line breaks, at least 8 characters")
    line = f"{name}={value}"
    lines = _lines()
    for i, existing in enumerate(lines):
        if re.match(rf"^\s*(?:export\s+)?{name}\s*=", existing):
            lines[i] = line
            break
    else:
        lines.append(line)
    _write(lines)


def remove_key(name: str) -> bool:
    """Take a key out of Margin's file. Keys set elsewhere are yours to remove."""
    _check(name)
    lines = _lines()
    kept = [ln for ln in lines if not re.match(rf"^\s*(?:export\s+)?{name}\s*=", ln)]
    if len(kept) == len(lines):
        return False
    _write(kept)
    return True


def status(names: list[str]) -> dict[str, str | None]:
    """Where each key is set ("environment", "margin", "shared") or None."""
    return {n: config.key_location(n) for n in names if NAME.match(n)}
