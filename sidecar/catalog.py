"""Every provider and model, for the model menu, from models.dev.

models.dev is the open catalog OpenCode and others use: 200+ providers and
thousands of models, each with what it takes in (text, images), how much it
reads and writes, and what it costs. Margin reads it once a day
(~/.margin/cache/models.dev.json) and keeps working from the last copy when
offline, or from its own short list if it has never fetched one.

Margin can write with any provider that speaks the OpenAI Chat Completions API
with one key: most say so in the catalog (an `api` address); the big
first-party ones are known below. Those needing more than a key (cloud
accounts: AWS Bedrock, Azure, Google Vertex and the like) are listed as not
supported yet rather than offered and failing. Local servers are not in the
catalog: they are found by asking their usual ports.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CATALOG_URL = "https://models.dev/api.json"
CACHE_PATH = Path.home() / ".margin" / "cache" / "models.dev.json"
CATALOG_TTL_SEC = 24 * 3600

# OpenAI-compatible addresses the catalog leaves out (their SDKs know them).
KNOWN_BASES = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "togetherai": "https://api.together.xyz/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "perplexity": "https://api.perplexity.ai",
    "cohere": "https://api.cohere.ai/compatibility/v1",
    "venice": "https://api.venice.ai/api/v1",
    "vercel": "https://ai-gateway.vercel.sh/v1",
    "aihubmix": "https://aihubmix.com/v1",
}
# More than a key: account ids, regions, IAM, or a token exchange.
NEEDS_MORE = {"amazon-bedrock", "azure", "azure-cognitive-services", "cloudflare-ai-gateway",
              "cloudflare-workers-ai", "gitlab", "google-vertex", "google-vertex-anthropic", "sap-ai-core",
              "watsonx", "github-copilot", "qvac", "salad-cloud", "v0"}
LOCAL = [
    {"id": "ollama", "name": "Ollama", "base_url": "http://localhost:11434/v1",
     "hint": "Install Ollama, then: ollama pull <model>"},
    {"id": "lmstudio", "name": "LM Studio", "base_url": "http://localhost:1234/v1",
     "hint": "In LM Studio, load a model and start the server (or: lms server start)"},
    {"id": "llamacpp", "name": "llama.cpp", "base_url": "http://localhost:8080/v1", "hint": "Run llama-server (port 8080)"},
    {"id": "vllm", "name": "vLLM", "base_url": "http://localhost:8000/v1", "hint": "Run vllm serve (port 8000)"},
    {"id": "jan", "name": "Jan", "base_url": "http://localhost:1337/v1", "hint": "In Jan, turn on its local API server"},
]
CLAUDE_SUBSCRIPTION = {"id": "claude", "name": "Claude (your subscription)", "key": "CLAUDE_CODE_OAUTH_TOKEN",
                       "hint": "Needs Claude Code: run `claude setup-token`, then add the token",
                       "doc": "https://docs.claude.com/en/docs/claude-code"}
GEMINI_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")
# Without a catalog at all (first run, offline): enough to get going.
FALLBACK = {
    "google": {"name": "Google", "env": list(GEMINI_KEYS), "doc": "https://aistudio.google.com/apikey"},
    "openai": {"name": "OpenAI", "env": ["OPENAI_API_KEY"], "doc": "https://platform.openai.com/api-keys"},
    "anthropic": {"name": "Anthropic", "env": ["ANTHROPIC_API_KEY"], "doc": "https://console.anthropic.com/settings/keys"},
    "openrouter": {"name": "OpenRouter", "env": ["OPENROUTER_API_KEY"], "api": "https://openrouter.ai/api/v1",
                   "doc": "https://openrouter.ai/keys"},
    "groq": {"name": "Groq", "env": ["GROQ_API_KEY"], "doc": "https://console.groq.com/keys"},
    "deepseek": {"name": "DeepSeek", "env": ["DEEPSEEK_API_KEY"], "api": "https://api.deepseek.com"},
    "mistral": {"name": "Mistral", "env": ["MISTRAL_API_KEY"]},
}
# Shown first in the menu when you have no key for them yet: the ones most
# people have or can get, and the free and cheap ones.
POPULAR = ["google", "openai", "anthropic", "openrouter", "groq", "deepseek", "mistral", "xai", "cerebras",
           "togetherai", "fireworks-ai", "deepinfra", "perplexity", "huggingface", "nvidia", "moonshotai",
           "zai", "alibaba"]
_NOT_FOR_NOTES = re.compile(r"embed|tts|whisper|moderation|dall-e|image-gen|imagen|veo|realtime|"
                            r"transcri|guard|rerank|speech|lyria|computer-use|\baqa\b|robotics", re.IGNORECASE)

_loaded: tuple[float, dict] | None = None


def _fetch() -> dict | None:
    try:
        resp = httpx.get(CATALOG_URL, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or len(data) < 10:
            raise ValueError("unexpected catalog shape")
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data))
        return data
    except Exception as e:  # noqa: BLE001 -- the last copy, or the short list, will do
        logger.warning("Could not refresh the model catalog: %s", e)
        return None


def raw() -> dict:
    """The catalog: fresh within a day, else refetched, else the last copy,
    else Margin's short list."""
    global _loaded
    if _loaded and time.time() - _loaded[0] < CATALOG_TTL_SEC:
        return _loaded[1]
    data = None
    if CACHE_PATH.exists() and time.time() - CACHE_PATH.stat().st_mtime < CATALOG_TTL_SEC:
        try:
            data = json.loads(CACHE_PATH.read_text())
        except ValueError:
            data = None
    data = data or _fetch()
    if data is None and CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())  # stale beats nothing
        except ValueError:
            data = None
    data = data or {pid: {**p, "id": pid, "models": {}} for pid, p in FALLBACK.items()}
    _loaded = (time.time(), data)
    return data


def key_env(provider: dict) -> str | None:
    """The environment variable holding the provider's key."""
    env = provider.get("env") or []
    keyish = [e for e in env if re.search(r"KEY|TOKEN", e)]
    return keyish[0] if keyish else (env[0] if env else None)


def providers() -> list[dict]:
    """Every provider: how Margin talks to it, or why it cannot yet."""
    out = []
    for pid, p in sorted(raw().items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
        row = {"id": pid, "name": p.get("name") or pid, "doc": p.get("doc"), "models": len(p.get("models") or {})}
        env = p.get("env") or []
        if pid == "google":
            row.update(kind="gemini", key="GEMINI_API_KEY", keys=list(GEMINI_KEYS), supported=True)
        elif pid in NEEDS_MORE or len([e for e in env if not re.search(r"KEY|TOKEN", e)]) > 0 and len(env) > 1:
            row.update(kind="unsupported", supported=False, reason="needs cloud-account setup Margin does not do yet")
        else:
            base = (p.get("api") or KNOWN_BASES.get(pid) or "").rstrip("/")
            if not base:
                row.update(kind="unsupported", supported=False, reason="no OpenAI-compatible address known")
            else:
                row.update(kind="compatible", base_url=base, key=key_env(p), supported=True,
                           local=base.startswith(("http://127.", "http://localhost")))
        out.append(row)
    # The local apps people use, first, under their own names and instructions
    # (the catalog also lists niche ones on localhost; they follow).
    known = []
    for local in LOCAL:
        existing = next((r for r in out if r["id"] == local["id"]), None)
        if existing:
            out.remove(existing)
        known.append({**(existing or {"models": 0, "key": None}), **local, "kind": "compatible", "local": True,
                      "supported": True})
    return known + out


def provider(pid: str) -> dict | None:
    return next((p for p in providers() if p["id"] == pid), None)


def _model_row(m: dict) -> dict | None:
    mods = m.get("modalities") or {}
    inputs, outputs = mods.get("input") or ["text"], mods.get("output") or ["text"]
    mid = m.get("id") or ""
    if "text" not in outputs or "text" not in inputs or _NOT_FOR_NOTES.search(mid):
        return None
    cost = m.get("cost") or {}
    limit = m.get("limit") or {}
    return {"id": mid, "name": m.get("name") or mid, "vision": "image" in inputs,
            "reasoning": bool(m.get("reasoning")), "context": limit.get("context") or None,
            "output": limit.get("output") or None, "cost_in": cost.get("input"), "cost_out": cost.get("output"),
            "released": m.get("release_date") or m.get("last_updated") or ""}


def models(pid: str) -> list[dict]:
    """A provider's models that can write notes, newest first."""
    p = raw().get(pid) or {}
    rows = [r for r in (_model_row(m) for m in (p.get("models") or {}).values()) if r]
    return sorted(rows, key=lambda r: (r["released"], r["id"]), reverse=True)


def search(query: str, ready: set[str], limit: int = 80) -> list[dict]:
    """Models across every supported provider matching all the words typed,
    your ready providers first, then newest."""
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    if not words:
        return []
    hits = []
    for p in providers():
        if not p.get("supported"):
            continue
        pname = p["name"].lower()
        for m in models(p["id"]):
            text = f"{pname} {p['id']} {m['id'].lower()} {m['name'].lower()}"
            if all(w in text for w in words):
                hits.append({**m, "provider": p["id"], "provider_name": p["name"], "ready": p["id"] in ready})
    ready_hits = sorted([h for h in hits if h["ready"]], key=lambda h: h["released"], reverse=True)
    other_hits = sorted([h for h in hits if not h["ready"]], key=lambda h: h["released"], reverse=True)
    return (ready_hits + other_hits)[:limit]
