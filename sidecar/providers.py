"""Any provider, any local model: engines you add in ~/.margin/engines.toml.

Nearly every provider and every local model server speaks the OpenAI Chat
Completions API (OpenAI, Anthropic's compatibility endpoint, Gemini's, Groq,
Cerebras, Together, Fireworks, DeepSeek, Mistral, xAI, OpenRouter; Ollama,
LM Studio, llama.cpp, vLLM on your Mac). So one generic engine covers them
all; a preset only fills in the address and the key's name.

    # ~/.margin/engines.toml
    [engines.groq]
    provider = "groq"                  # a preset: address and key name
    model = "openai/gpt-oss-120b"

    [engines.local]
    provider = "lmstudio"
    model = "qwen3.5-9b"
    vision = true                      # it can look at slides (default: false)
    context = 32000                    # tokens it can read; longer lectures are trimmed to fit

    # chain = ["gemini", "claude", "groq", "openrouter", "gemini-lite", "local"]

Without a `chain`, your engines join the default one: full-quality engines
before the Gemini Lite fallback, `tier = "lite"` engines (local ones by
default) after it. Notes a lite engine writes are rewritten later by a full
model, like Gemini Lite's. Keys are read from ~/.config/keys.env by name.
Without the file, nothing changes.
"""
from __future__ import annotations

import logging
import os
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("MARGIN_ENGINES_FILE") or Path.home() / ".margin" / "engines.toml")

PRESETS: dict[str, dict] = {
    "openai": {"base_url": "https://api.openai.com/v1", "key": "OPENAI_API_KEY"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "key": "ANTHROPIC_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": "GEMINI_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY"},
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key": "CEREBRAS_API_KEY"},
    "fireworks": {"base_url": "https://api.fireworks.ai/inference/v1", "key": "FIREWORKS_API_KEY"},
    "together": {"base_url": "https://api.together.xyz/v1", "key": "TOGETHER_API_KEY"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "key": "DEEPSEEK_API_KEY"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "key": "MISTRAL_API_KEY"},
    "xai": {"base_url": "https://api.x.ai/v1", "key": "XAI_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key": None, "local": True},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "key": None, "local": True},
    "llamacpp": {"base_url": "http://localhost:8080/v1", "key": None, "local": True},
    "vllm": {"base_url": "http://localhost:8000/v1", "key": None, "local": True},
}
BUILT_IN = {"gemini", "gemini-lite", "claude", "openrouter"}


@dataclass(frozen=True)
class EngineSpec:
    name: str
    base_url: str
    model: str
    key: str | None          # the key's name in keys.env, or None (local servers)
    vision: bool = False
    tier: str = "full"       # "lite": its notes are rewritten later by a full model
    max_output: int = 16000
    context: int | None = None  # tokens the model can read
    timeout: float = 240.0
    local: bool = False

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"


class ConfigError(ValueError):
    pass


def parse(data: dict) -> tuple[dict[str, EngineSpec], list[str] | None]:
    """Engines and the optional chain from a parsed engines.toml."""
    specs: dict[str, EngineSpec] = {}
    for name, raw in (data.get("engines") or {}).items():
        if name in BUILT_IN:
            raise ConfigError(f"'{name}' is a built-in engine; give yours another name")
        if not isinstance(raw, dict) or not raw.get("model"):
            raise ConfigError(f"engine '{name}' needs a model")
        preset = PRESETS.get(raw.get("provider", ""), {})
        if raw.get("provider") and not preset:
            raise ConfigError(f"engine '{name}': unknown provider '{raw['provider']}' "
                              f"(known: {', '.join(sorted(PRESETS))}; or give base_url)")
        base_url = (raw.get("base_url") or preset.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ConfigError(f"engine '{name}' needs a provider or a base_url")
        local = bool(raw.get("local", preset.get("local", "localhost" in base_url or "127.0.0.1" in base_url)))
        tier = raw.get("tier", "lite" if local else "full")
        if tier not in ("full", "lite"):
            raise ConfigError(f"engine '{name}': tier is 'full' or 'lite'")
        specs[name] = EngineSpec(
            name=name, base_url=base_url, model=str(raw["model"]),
            key=raw.get("key", preset.get("key")),
            vision=bool(raw.get("vision", False)), tier=tier,
            max_output=int(raw.get("max_output", 8192 if local else 16000)),
            context=int(raw["context"]) if raw.get("context") else None,
            timeout=float(raw.get("timeout", 900 if local else 240)), local=local,
        )
    chain = data.get("chain")
    if chain is not None:
        unknown = [e for e in chain if e not in BUILT_IN and e not in specs]
        if unknown:
            raise ConfigError(f"chain names engines that are not defined: {', '.join(unknown)}")
    return specs, chain


_cache: tuple[float, tuple[dict[str, EngineSpec], list[str] | None]] | None = None


def load() -> tuple[dict[str, EngineSpec], list[str] | None]:
    """The configured engines, re-read when the file changes. A broken file is
    reported in the log and ignored, never allowed to stop notes."""
    global _cache
    if not CONFIG_PATH.exists():
        return {}, None
    mtime = CONFIG_PATH.stat().st_mtime
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        result = parse(tomllib.loads(CONFIG_PATH.read_text()))
    except (tomllib.TOMLDecodeError, ConfigError, ValueError, TypeError) as e:
        logger.error("Ignoring %s: %s", CONFIG_PATH, e)
        result = ({}, None)
    _cache = (mtime, result)
    return result


def chain_with(default: list[str]) -> list[str]:
    """The engine order: the file's `chain`, or the default with your engines
    added (full ones before the Gemini Lite fallback, lite ones after)."""
    specs, chain = load()
    if chain is not None:
        return list(chain)
    if not specs:
        return list(default)
    full = [n for n, s in specs.items() if s.tier == "full"]
    lite = [n for n, s in specs.items() if s.tier == "lite"]
    out = [e for e in default if e != "gemini-lite"] + full
    if "gemini-lite" in default:
        out.append("gemini-lite")
    return out + lite


def lite_labels() -> set[str]:
    specs, _ = load()
    return {s.label for s in specs.values() if s.tier == "lite"}


# --- the model menu -----------------------------------------------------------------
#
# Every provider in the catalog (catalog.py: models.dev, 200+ providers), with
# whether it is ready: a key set (environment, Margin's key file or
# ~/.config/keys.env), your Claude subscription token, or a local server
# answering. Choosing a model there changes nothing here: the choice lives in
# the extension and rides along with each compose request. Without a choice,
# notes use your default chain exactly as before.

from sidecar import catalog, subscriptions  # noqa: E402 -- the menu half of this module

_live_cache: dict[str, tuple[float, list[dict]]] = {}
LIVE_TTL_SEC = 60


def sees_images(provider: str, model: str) -> bool:
    """Whether a model can look at the slides, from the catalog; by name for
    models it does not list (local ones)."""
    if provider in ("google", "gemini", "claude", "anthropic"):
        return True
    known = next((m for m in catalog.models(provider) if m["id"] == model), None)
    if known is not None:
        return known["vision"]
    return bool(re.search(r"vision|[-_]vl\b|[-_]vl[-_]|llava|pixtral|llama-?4|gemma-?3|gemma-?4|qwen[\w.-]*vl|"
                          r"moondream|minicpm-v|internvl|gpt-4o|gpt-5|gemini|claude", model, re.IGNORECASE))


def _ready(p: dict) -> tuple[bool, str | None]:
    """Can Margin write with this provider now, and if not, what to do."""
    from sidecar.config import key_location, load_api_keys

    if not p.get("supported"):
        return False, p.get("reason")
    if p.get("local"):
        try:
            ok = httpx.get(f"{p['base_url']}/models", timeout=0.6).status_code < 500
        except httpx.HTTPError:
            ok = False
        return ok, None if ok else p.get("hint") or "Start its local server"
    names = p.get("keys") or [p.get("key")]
    if any(n and load_api_keys(n) for n in names):
        return True, None
    return False, p.get("hint") or "Add a key"


def menu() -> list[dict]:
    """Every provider, with whether it can be used right now and where its key
    is set. Never a key."""
    from sidecar.config import key_location

    rows = []
    for p in catalog.providers():
        ready, hint = _ready(p)
        names = p.get("keys") or [p.get("key")]
        where = next((key_location(n) for n in names if n and key_location(n)), None)
        rows.append({"id": p["id"], "name": p["name"], "local": bool(p.get("local")),
                     "supported": bool(p.get("supported")), "ready": ready, "hint": hint,
                     "key": p.get("key"), "key_set_in": where, "doc": p.get("doc"),
                     "models": p.get("models", 0)})
    return rows


def local_installed(provider: str) -> list[str]:
    """Models a local app has downloaded, read from disk, so they show in the
    menu even while its server is off."""
    home = Path.home()
    found: list[str] = []
    if provider == "ollama":
        root = home / ".ollama" / "models" / "manifests"
        for tag in root.glob("*/*/*/*") if root.exists() else []:
            registry, namespace, name = tag.parts[-4], tag.parts[-3], tag.parts[-2]
            base = name if namespace == "library" else f"{namespace}/{name}"
            found.append(f"{base}:{tag.name}" if registry == "registry.ollama.ai" else f"{registry}/{base}:{tag.name}")
    elif provider == "lmstudio":
        root = home / ".lmstudio" / "models"
        for model_dir in root.glob("*/*") if root.exists() else []:
            if model_dir.is_dir() and any(f.is_file() and f.stat().st_size > 1_000_000 for f in model_dir.rglob("*")):
                found.append(f"{model_dir.parent.name}/{model_dir.name}")
    return sorted(set(found))


def _plain(mid: str, vision: bool, local: bool = False) -> dict:
    return {"id": mid, "name": mid, "vision": vision, "reasoning": False, "context": None, "output": None,
            "cost_in": 0 if local else None, "cost_out": 0 if local else None, "released": ""}


def models_for(provider: str) -> list[dict]:
    """A provider's models: a subscription's own list, the catalog's, or for a
    local server what it serves (or, while it is off, what is downloaded)."""
    if provider in subscriptions.IDS:
        sub = subscriptions.get(provider)
        if provider == "opencode":  # images pass through; only some of its models read them
            return [_plain(m, sees_images("", m.split("/", 1)[-1])) for m in subscriptions.models(provider)]
        return [_plain(m, sub["images"]) for m in subscriptions.models(provider)]
    p = catalog.provider(provider)
    if p is None:
        raise ConfigError(f"unknown provider '{provider}'")
    listed = catalog.models(provider)
    if listed and not p.get("local"):
        return listed
    if p.get("local"):
        up, _ = _ready(p)
        if not up:
            return [{**_plain(m, sees_images(provider, m), True), "installed_only": True}
                    for m in local_installed(provider)]
    hit = _live_cache.get(provider)
    if hit and time.time() - hit[0] < LIVE_TTL_SEC:
        return hit[1]
    from sidecar.config import load_api_keys

    headers = {}
    if p.get("key"):
        key = (load_api_keys(p["key"]) or [None])[0]
        if key:
            headers = {"Authorization": f"Bearer {key}"}
    resp = httpx.get(f"{p['base_url']}/models", headers=headers, timeout=10)
    resp.raise_for_status()
    found = []
    for m in resp.json().get("data") or []:  # Ollama with nothing pulled says "data": null
        mid = m.get("id") or ""
        if mid and not catalog._NOT_FOR_NOTES.search(mid):
            found.append(_plain(mid, sees_images(provider, mid), bool(p.get("local"))))
    found.sort(key=lambda m: m["id"])
    _live_cache[provider] = (time.time(), found)
    return found


def spec_for_choice(choice: dict) -> EngineSpec | None:
    """An engine for a model picked in the menu, when it is an OpenAI-compatible
    one. Google Gemini and Claude have engines of their own (see llm.py)."""
    provider, model = choice.get("provider"), choice.get("model")
    if not provider or not model or provider in ("google", "gemini", "claude"):
        return None
    p = catalog.provider(provider)
    if p is None or not p.get("supported"):
        raise ConfigError(f"Margin cannot write with '{provider}' yet")
    local = bool(p.get("local"))
    listed = next((m for m in catalog.models(provider) if m["id"] == model), None)
    output = (listed or {}).get("output") or (8192 if local else 16000)
    return EngineSpec(name=provider, base_url=p["base_url"], model=str(model), key=p.get("key"),
                      vision=bool(choice["vision"]) if "vision" in choice else sees_images(provider, model),
                      tier="lite" if local else "full", max_output=min(int(output), 32000),
                      context=(listed or {}).get("context"), timeout=900 if local else 240, local=local)


# --- your model order, described ----------------------------------------------------

def entry_status(entry: dict) -> dict:
    """What one entry of your order is, and whether it can write right now."""
    from sidecar.config import load_api_keys

    provider, model = entry.get("provider"), entry.get("model")
    out = {"provider": provider, "model": model, "ready": False, "hint": None, "vision": True}
    gemini = any(load_api_keys(n) for n in catalog.GEMINI_KEYS)
    if provider == "google" and model in ("auto", "auto-lite"):
        lite = model == "auto-lite"
        out.update(title="Gemini Lite" if lite else "Google Gemini",
                   detail=("Last resort: its notes are rewritten later by a full model" if lite
                           else "Best available Flash model: 3.5, then 3, then 3.6"),
                   ready=gemini, hint=None if gemini else "Add a Gemini key (free at aistudio.google.com)")
    elif provider == "openrouter" and model == "auto":
        has = bool(load_api_keys("OPENROUTER_API_KEY"))
        out.update(title="OpenRouter", detail="Gemini 3.5 Flash, then 2.5 Flash, on OpenRouter",
                   ready=has, hint=None if has else "Add an OpenRouter key")
    elif provider in subscriptions.IDS:
        sub = subscriptions.get(provider)
        installed = bool(subscriptions.find(sub["bin"]))
        token_ok = not sub.get("key") or bool(load_api_keys(sub["key"]))
        shown = "Its default model" if model == "default" else (model.title() if provider == "claude" else model)
        out.update(title=sub["name"], detail=f"{shown}, through {sub['tool']}",
                   ready=installed and token_ok, vision=sub["images"],
                   hint=None if installed and token_ok else (sub["login"] if installed else f"Install {sub['tool']}: {sub['install']}"))
    elif provider == "custom":
        custom, _ = load()
        spec = custom.get(model)
        out.update(title=model, detail="From ~/.margin/engines.toml", ready=spec is not None,
                   vision=bool(spec and spec.vision), hint=None if spec else "Not in ~/.margin/engines.toml")
    else:
        p = catalog.provider(provider)
        if p is None:
            out.update(title=provider, detail=model, hint="Unknown provider")
            return out
        ready, hint = _ready(p)
        listed = next((m for m in catalog.models(provider) if m["id"] == model), None)
        out.update(title=p["name"] + (" (on this Mac)" if p.get("local") else ""),
                   detail=(listed or {}).get("name") or model, ready=ready, hint=hint,
                   vision=sees_images(provider, model))
    return out


def subscription_rows() -> list[dict]:
    from sidecar.config import load_api_keys

    rows = []
    for sub in subscriptions.status():
        token_ok = not sub.get("key") or bool(load_api_keys(sub["key"]))
        rows.append({**sub, "ready": sub["installed"] and token_ok,
                     "hint": None if sub["installed"] and token_ok else (sub["login"] if sub["installed"]
                                                                          else f"Install: {sub['install']}")})
    return rows
