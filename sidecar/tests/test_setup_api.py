"""Setting Margin up: keys, subscriptions, local models and your model order."""
import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from sidecar import chain, config, keys, llm, main, providers, subscriptions
from sidecar.llm import Picture

MARGIN = {"X-Margin": "1"}


@pytest.fixture
def api():
    return TestClient(main.app)


# --- keys --------------------------------------------------------------------------

def test_a_key_goes_in_and_never_comes_back_out(api):
    assert api.post("/keys", json={"name": "GROQ_API_KEY", "value": "gsk_test_1234567890"}, headers=MARGIN).status_code == 200
    body = api.get("/keys?names=GROQ_API_KEY,OPENAI_API_KEY").json()
    assert body == {"keys": {"GROQ_API_KEY": "margin", "OPENAI_API_KEY": None}}
    assert "gsk_test" not in json.dumps(api.get("/providers").json())
    assert config.load_api_keys("GROQ_API_KEY") == ["gsk_test_1234567890"]
    mode = stat.S_IMODE(os.stat(config.MARGIN_KEYS_PATH).st_mode)
    assert mode == 0o600  # yours alone
    # Replacing keeps one line; removing takes it out.
    api.post("/keys", json={"name": "GROQ_API_KEY", "value": "gsk_test_replaced_99"}, headers=MARGIN)
    assert config.MARGIN_KEYS_PATH.read_text().count("GROQ_API_KEY=") == 1
    assert api.delete("/keys/GROQ_API_KEY", headers=MARGIN).json()["removed"] is True
    assert config.load_api_keys("GROQ_API_KEY") == []


def test_only_margins_own_pages_can_change_settings(api):
    assert api.post("/keys", json={"name": "GROQ_API_KEY", "value": "gsk_test_1234567890"}).status_code == 403
    assert api.put("/chain", json={"entries": [{"provider": "groq", "model": "x"}]}).status_code == 403
    assert api.delete("/chain").status_code == 403


@pytest.mark.parametrize("name, value", [("lower", "gsk_test_1234567890"), ("GROQ_API_KEY", "short"),
                                         ("GROQ_API_KEY", "has a space inside it"), ("GROQ_API_KEY", "two\nlines")])
def test_nonsense_is_refused(api, name, value):
    assert api.post("/keys", json={"name": name, "value": value}, headers=MARGIN).status_code == 400


def test_keys_you_keep_elsewhere_are_read_but_never_written(tmp_path, monkeypatch):
    shared = tmp_path / "keys.env"
    shared.write_text("GEMINI_API_KEY=from-your-own-file\n")
    monkeypatch.setattr(config, "KEYS_PATH", shared)
    monkeypatch.setenv("OPENAI_API_KEY", "from-the-environment")
    assert keys.status(["GEMINI_API_KEY", "OPENAI_API_KEY"]) == {"GEMINI_API_KEY": "shared", "OPENAI_API_KEY": "environment"}
    assert keys.remove_key("GEMINI_API_KEY") is False
    assert shared.read_text() == "GEMINI_API_KEY=from-your-own-file\n"


# --- your model order ------------------------------------------------------------------

def test_without_a_saved_order_the_default_is_shown_and_used(api):
    order = api.get("/chain").json()
    assert order["custom_order"] is False
    assert [(e["provider"], e["model"]) for e in order["order"]] == [
        ("google", "auto"), ("claude", "sonnet"), ("openrouter", "auto"), ("google", "auto-lite")]
    assert order["order"][0]["title"] == "Google Gemini"
    assert llm.engine_chain() == llm.DEFAULT_CHAIN.split(",")


def test_a_saved_order_is_what_writes_the_notes(api, monkeypatch):
    entries = [{"provider": "ollama", "model": "gemma3:12b"}, {"provider": "google", "model": "auto"}]
    saved = api.put("/chain", json={"entries": entries}, headers=MARGIN).json()
    assert saved["custom_order"] and [e["model"] for e in saved["order"]] == ["gemma3:12b", "auto"]
    assert llm.engine_chain() == entries
    tried = []
    monkeypatch.setattr(llm, "entry_engine", lambda e: (lambda *a, **k: tried.append(e["provider"]) or (
        (_ for _ in ()).throw(llm.Busy("down")) if e["provider"] == "ollama" else ("<<<NOTE>>>\\nx\\n<<<GIST>>>\\ng", "g"))))
    assert llm.generate("p")[1] == "g" and tried == ["ollama", "google"]  # 1st busy, 2nd writes
    assert api.delete("/chain", headers=MARGIN).json()["custom_order"] is False
    assert llm.engine_chain() == llm.DEFAULT_CHAIN.split(",")


def test_an_order_must_have_something_in_it(api):
    assert api.put("/chain", json={"entries": []}, headers=MARGIN).status_code == 400
    assert api.put("/chain", json={"entries": [{"provider": "groq"}]}, headers=MARGIN).status_code == 400


def test_a_broken_order_file_falls_back_to_the_default():
    chain.CHAIN_PATH.write_text("{ not json")
    assert llm.engine_chain() == llm.DEFAULT_CHAIN.split(",")


# --- subscriptions, through their own tools --------------------------------------------

def _tool(tmp_path, name, script):
    path = tmp_path / name
    path.write_text("#!/bin/bash\n" + script)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def usage(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")


def test_chatgpt_through_codex_gets_the_prompt_and_the_slides(tmp_path, monkeypatch, usage):
    # Codex writes its last message to the file after -o; the images come with -i.
    exe = _tool(tmp_path, "codex", 'args="$*"; out=""; prev=""\nfor a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                'echo "note with $(echo "$args" | grep -o -- "-i" | wc -l | tr -d " ") images" > "$out"\n')
    monkeypatch.setattr(subscriptions, "find", lambda b: exe)
    text = subscriptions.run("codex", "write the note", [Picture("S001 at 00:10", b"jpg"), Picture("S002", b"jpg")])
    assert text.strip() == "note with 2 images"


def test_a_subscription_limit_counts_as_busy(tmp_path, monkeypatch, usage):
    exe = _tool(tmp_path, "codex", 'echo "ERROR: You have hit your usage limit. Try again later." >&2; exit 1\n')
    monkeypatch.setattr(subscriptions, "find", lambda b: exe)
    with pytest.raises(subscriptions.SubscriptionBusy):
        subscriptions.run("codex", "p", [])


def test_opencode_answers_are_read_from_its_events(tmp_path, monkeypatch, usage):
    exe = _tool(tmp_path, "opencode", "echo '{\"type\":\"step_start\"}'\n"
                "echo '{\"type\":\"text\",\"part\":{\"type\":\"text\",\"text\":\"a note\"}}'\n")
    monkeypatch.setattr(subscriptions, "find", lambda b: exe)
    assert subscriptions.run("opencode", "p", [], model="opencode/big-pickle") == "a note"
    with pytest.raises(subscriptions.SubscriptionError, match="needs a model"):
        subscriptions.run("opencode", "p", [])


def test_gemini_cli_errors_are_shown_as_they_are(tmp_path, monkeypatch, usage):
    exe = _tool(tmp_path, "gemini", "echo '{\"error\": {\"message\": \"When using Vertex AI, you must specify a project\"}}'\n")
    monkeypatch.setattr(subscriptions, "find", lambda b: exe)
    with pytest.raises(subscriptions.SubscriptionError, match="Vertex AI"):
        subscriptions.run("gemini-cli", "p", [])


def test_a_tool_that_is_not_installed_says_how_to_get_it(monkeypatch, usage):
    monkeypatch.setattr(subscriptions, "find", lambda b: None)
    with pytest.raises(subscriptions.SubscriptionError, match="npm install -g @github/copilot"):
        subscriptions.run("copilot", "p", [])


def test_a_subscription_in_the_order_writes_through_generate(tmp_path, monkeypatch, usage):
    exe = _tool(tmp_path, "codex", 'prev=""\nfor a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done\n'
                'printf "<<<NOTE>>>\\nfrom chatgpt\\n<<<GIST>>>\\ng\\n" > "$out"\n')
    monkeypatch.setattr(subscriptions, "find", lambda b: exe)
    chain.save([{"provider": "codex", "model": "default"}])
    text, engine = llm.generate("p")
    assert engine == "codex/default" and "from chatgpt" in text


# --- local models -------------------------------------------------------------------------

def test_downloaded_local_models_show_even_with_the_server_off(tmp_path, monkeypatch):
    monkeypatch.setattr(providers.Path, "home", lambda: tmp_path)
    (tmp_path / ".ollama/models/manifests/registry.ollama.ai/library/gemma3").mkdir(parents=True)
    (tmp_path / ".ollama/models/manifests/registry.ollama.ai/library/gemma3/12b").write_text("{}")
    lm = tmp_path / ".lmstudio/models/mlx-community/Qwen3.5-9B-4bit"
    lm.mkdir(parents=True)
    (lm / "model.safetensors").write_bytes(b"0" * 2_000_000)
    (tmp_path / ".lmstudio/models/mlx-community/Empty-Model").mkdir()
    assert providers.local_installed("ollama") == ["gemma3:12b"]
    assert providers.local_installed("lmstudio") == ["mlx-community/Qwen3.5-9B-4bit"]

    def off(*a, **k):
        raise providers.httpx.ConnectError("off")

    monkeypatch.setattr(providers.httpx, "get", off)
    models = providers.models_for("ollama")
    assert [m["id"] for m in models] == ["gemma3:12b"] and models[0]["installed_only"] and models[0]["vision"]


def test_the_menu_lists_subscriptions_and_says_what_each_needs(api, monkeypatch):
    monkeypatch.setattr(subscriptions, "find", lambda b: "/usr/local/bin/codex" if b == "codex" else None)
    body = api.get("/providers").json()
    subs = {s["id"]: s for s in body["subscriptions"]}
    assert subs["codex"]["ready"] and not subs["cursor"]["ready"]
    assert subs["cursor"]["hint"].startswith("Install:")
    assert "grok" in body["not_yet"]
    assert body["order"][0]["title"] == "Google Gemini"


def test_a_google_ai_pro_plan_writes_through_antigravity(tmp_path, monkeypatch, usage):
    # Gemini CLI stopped working for Google AI Pro and Ultra on 18 June 2026;
    # Antigravity's agy took its place, headless with -p, reading the slides
    # from its folder, in its sandbox.
    exe = _tool(tmp_path, "agy", 'args="$*"\n'
                'if [ "$1" = "models" ]; then printf "Fetching available models...\\ngemini-3.1-pro-high\\tGemini 3.1 Pro (High)\\n'
                'claude-sonnet-4-6\\tClaude Sonnet 4.6 (Thinking)\\n"; exit 0; fi\n'
                'echo "note: $(echo "$args" | grep -o "_[0-9][0-9]\\.jpg" | wc -l | tr -d " ") slides, '
                'sandbox $(echo "$args" | grep -c -- "--sandbox"), model $(echo "$args" | grep -o "gemini-3.1-pro-high")"\n')
    monkeypatch.setattr(subscriptions, "find", lambda b: exe)
    assert subscriptions.models("antigravity") == ["gemini-3.1-pro-high", "claude-sonnet-4-6"]
    text = subscriptions.run("antigravity", "write the note", [Picture("S001 at 00:10", b"jpg"), Picture("S002", b"jpg")],
                             model="gemini-3.1-pro-high")
    assert text.strip() == "note: 2 slides, sandbox 1, model gemini-3.1-pro-high"
