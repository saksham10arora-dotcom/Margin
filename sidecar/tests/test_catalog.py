"""The model menu's catalog: every provider and model from models.dev."""
import json

import pytest

from sidecar import catalog, providers

MINI = {
    "google": {"id": "google", "name": "Google", "env": ["GOOGLE_API_KEY", "GEMINI_API_KEY"], "npm": "@ai-sdk/google",
               "models": {"gemini-3.5-flash": {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "release_date": "2026-05-01",
                                               "modalities": {"input": ["text", "image"], "output": ["text"]},
                                               "limit": {"context": 1048576, "output": 65536}, "cost": {"input": 0.3, "output": 2.5}}}},
    "groq": {"id": "groq", "name": "Groq", "env": ["GROQ_API_KEY"], "npm": "@ai-sdk/groq", "models": {
        "openai/gpt-oss-120b": {"id": "openai/gpt-oss-120b", "name": "GPT OSS 120B", "release_date": "2025-08-05",
                                "reasoning": True, "modalities": {"input": ["text"], "output": ["text"]},
                                "limit": {"context": 131072, "output": 65536}, "cost": {"input": 0.15, "output": 0.6}},
        "whisper-large-v3": {"id": "whisper-large-v3", "modalities": {"input": ["audio"], "output": ["text"]}},
        "llama-4-scout": {"id": "llama-4-scout", "name": "Llama 4 Scout", "release_date": "2025-04-05",
                          "modalities": {"input": ["text", "image"], "output": ["text"]}}}},
    "deepseek": {"id": "deepseek", "name": "DeepSeek", "env": ["DEEPSEEK_API_KEY"], "api": "https://api.deepseek.com/",
                 "models": {"deepseek-chat": {"id": "deepseek-chat", "name": "DeepSeek Chat", "release_date": "2025-12-01",
                                              "modalities": {"input": ["text"], "output": ["text"]}}}},
    "amazon-bedrock": {"id": "amazon-bedrock", "name": "Amazon Bedrock", "env": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
                       "models": {"x": {"id": "x"}}},
    "mystery": {"id": "mystery", "name": "Mystery", "env": ["MYSTERY_KEY"], "npm": "mystery-sdk", "models": {}},
    "lmstudio": {"id": "lmstudio", "name": "LMStudio", "env": ["LMSTUDIO_API_KEY"], "api": "http://127.0.0.1:1234/v1", "models": {}},
}


@pytest.fixture
def mini(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "CACHE_PATH", tmp_path / "models.dev.json")
    monkeypatch.setattr(catalog, "_loaded", None)
    monkeypatch.setattr(catalog, "_fetch", lambda: json.loads(json.dumps(MINI)))
    return MINI


def test_providers_say_how_margin_talks_to_them(mini):
    rows = {p["id"]: p for p in catalog.providers()}
    assert "claude" not in rows                                                 # a subscription, not a catalog row
    assert rows["google"]["kind"] == "gemini" and "GOOGLE_API_KEY" in rows["google"]["keys"]
    assert rows["groq"]["base_url"] == "https://api.groq.com/openai/v1"          # known, not in the catalog
    assert rows["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert rows["amazon-bedrock"]["supported"] is False and "cloud-account" in rows["amazon-bedrock"]["reason"]
    assert rows["mystery"]["supported"] is False                                 # no address known: not offered
    assert rows["lmstudio"]["local"] is True
    assert rows["ollama"]["local"] and rows["jan"]["local"]                      # local servers added


def test_models_are_the_ones_that_write_newest_first_with_what_they_can_do(mini):
    groq = catalog.models("groq")
    assert [m["id"] for m in groq] == ["openai/gpt-oss-120b", "llama-4-scout"]  # no whisper
    oss = groq[0]
    assert oss["vision"] is False and oss["reasoning"] and oss["context"] == 131072
    assert (oss["cost_in"], oss["cost_out"]) == (0.15, 0.6)
    assert groq[1]["vision"] is True


def test_search_finds_models_across_providers_ready_ones_first(mini):
    hits = catalog.search("gpt oss", ready={"groq"})
    assert [(h["provider"], h["id"]) for h in hits] == [("groq", "openai/gpt-oss-120b")]
    hits = catalog.search("chat", ready=set())
    assert hits and hits[0]["provider"] == "deepseek" and hits[0]["ready"] is False
    assert catalog.search("   ", ready=set()) == []


def test_the_catalog_is_kept_for_a_day_and_used_when_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "CACHE_PATH", tmp_path / "models.dev.json")
    monkeypatch.setattr(catalog, "_loaded", None)
    fetched = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {**MINI, **{f"p{i}": {"name": f"P{i}"} for i in range(10)}}

    monkeypatch.setattr(catalog, "_fetch", catalog._real_fetch)
    monkeypatch.setattr(catalog.httpx, "get", lambda *a, **k: fetched.append(1) or Resp())
    assert "groq" in catalog.raw() and len(fetched) == 1
    monkeypatch.setattr(catalog, "_loaded", None)
    assert "groq" in catalog.raw() and len(fetched) == 1                         # from the file
    # A week later and offline: the last copy still serves.
    import os
    old = catalog.CACHE_PATH.stat().st_mtime - 7 * 86400
    os.utime(catalog.CACHE_PATH, (old, old))
    monkeypatch.setattr(catalog, "_loaded", None)

    def offline(*a, **k):
        raise catalog.httpx.ConnectError("offline")

    monkeypatch.setattr(catalog.httpx, "get", offline)
    assert "groq" in catalog.raw()


def test_never_fetched_and_offline_still_offers_the_main_providers(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "CACHE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(catalog, "_loaded", None)
    monkeypatch.setattr(catalog, "_fetch", lambda: None)
    ids = {p["id"] for p in catalog.providers()}
    assert {"google", "openai", "anthropic", "openrouter", "groq"} <= ids


def test_the_menu_says_what_is_ready_and_never_shows_a_key(mini, monkeypatch):
    monkeypatch.setattr("sidecar.config.load_api_keys", lambda name: ["gsk-secret-value"] if name == "GROQ_API_KEY" else [])
    monkeypatch.setattr("sidecar.config.key_location", lambda name: "margin" if name == "GROQ_API_KEY" else None)

    def get(url, **kw):
        raise providers.httpx.ConnectError("nothing on that port")

    monkeypatch.setattr(providers.httpx, "get", get)
    rows = {r["id"]: r for r in providers.menu()}
    assert rows["groq"]["ready"] and rows["groq"]["key_set_in"] == "margin"
    assert not rows["deepseek"]["ready"] and rows["deepseek"]["key"] == "DEEPSEEK_API_KEY"
    assert not rows["ollama"]["ready"] and "ollama pull" in rows["ollama"]["hint"]
    assert not rows["amazon-bedrock"]["supported"]
    assert "gsk-secret-value" not in json.dumps(rows)


def test_a_local_server_lists_its_own_models(mini, monkeypatch):
    monkeypatch.setattr(providers, "_live_cache", {})

    class Resp:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    monkeypatch.setattr(providers.httpx, "get", lambda url, **kw: Resp({"object": "list", "data": None}))
    assert providers.models_for("ollama") == []      # what Ollama says before any pull
    monkeypatch.setattr(providers, "_live_cache", {})
    monkeypatch.setattr(providers.httpx, "get", lambda url, **kw: Resp({"data": [{"id": "qwen2.5vl:7b"}, {"id": "nomic-embed-text"}]}))
    models = providers.models_for("ollama")
    assert [m["id"] for m in models] == ["qwen2.5vl:7b"] and models[0]["vision"] is True


def test_a_pick_from_the_catalog_becomes_an_engine(mini):
    spec = providers.spec_for_choice({"provider": "groq", "model": "openai/gpt-oss-120b"})
    assert spec.base_url == "https://api.groq.com/openai/v1" and spec.key == "GROQ_API_KEY"
    assert spec.vision is False and spec.context == 131072 and spec.tier == "full"
    with pytest.raises(providers.ConfigError):
        providers.spec_for_choice({"provider": "amazon-bedrock", "model": "x"})
    assert providers.spec_for_choice({"provider": "google", "model": "gemini-3.5-flash"}) is None  # its own engine
