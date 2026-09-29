"""Any provider, any local model, through ~/.margin/engines.toml."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sidecar import llm, providers
from sidecar.llm import Picture


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "engines.toml"
    monkeypatch.setattr(providers, "CONFIG_PATH", path)
    monkeypatch.setattr(providers, "_cache", None)
    monkeypatch.delenv("MARGIN_ENGINES", raising=False)

    def write(text):
        path.write_text(text)
        providers._cache = None

    return write


def test_without_a_file_nothing_changes(config):
    assert llm.engine_chain() == llm.DEFAULT_CHAIN.split(",")


def test_presets_fill_in_the_address_and_key(config):
    config('[engines.groq]\nprovider = "groq"\nmodel = "openai/gpt-oss-120b"\n'
           '[engines.local]\nprovider = "lmstudio"\nmodel = "qwen3.5-9b"\ncontext = 32000\n')
    specs, chain = providers.load()
    assert chain is None
    assert specs["groq"].base_url == "https://api.groq.com/openai/v1" and specs["groq"].key == "GROQ_API_KEY"
    assert specs["groq"].tier == "full" and not specs["groq"].vision
    local = specs["local"]
    assert local.base_url == "http://localhost:1234/v1" and local.key is None and local.local
    assert local.tier == "lite" and local.timeout == 900
    # Full engines go before the Gemini Lite fallback, lite ones after it.
    assert llm.engine_chain() == ["gemini", "claude", "openrouter", "groq", "gemini-lite", "local"]
    assert llm.is_lite("local/qwen3.5-9b") and not llm.is_lite("groq/openai/gpt-oss-120b")


def test_an_explicit_chain_is_used_as_written(config):
    config('chain = ["local", "gemini"]\n[engines.local]\nbase_url = "http://127.0.0.1:9000/v1/"\nmodel = "m"\n')
    assert llm.engine_chain() == ["local", "gemini"]
    assert providers.load()[0]["local"].base_url == "http://127.0.0.1:9000/v1"


@pytest.mark.parametrize("text, message", [
    ('[engines.gemini]\nprovider = "groq"\nmodel = "x"\n', "built-in"),
    ('[engines.a]\nprovider = "nope"\nmodel = "x"\n', "unknown provider"),
    ('[engines.a]\nprovider = "groq"\n', "needs a model"),
    ('chain = ["ghost"]\n', "not defined"),
])
def test_mistakes_are_named(text, message):
    import tomllib

    with pytest.raises(providers.ConfigError, match=message):
        providers.parse(tomllib.loads(text))


def test_a_broken_file_is_ignored_not_fatal(config):
    config("this is not toml [")
    assert llm.engine_chain() == llm.DEFAULT_CHAIN.split(",")


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._payload


def spec(**kw):
    base = dict(name="groq", base_url="https://api.groq.com/openai/v1", model="gpt-oss-120b", key="GROQ_API_KEY")
    return providers.EngineSpec(**{**base, **kw})


def test_a_text_only_model_gets_no_images_and_is_told_so(monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["gsk-test"])

    def post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, body=json)
        return FakeResponse(200, {"choices": [{"message": {"content": "a note"}}]})

    monkeypatch.setattr(llm.httpx, "post", post)
    call = llm._compatible_engine(spec())
    assert call("prompt", [Picture("S001 at 00:10", b"jpeg")], 24000, 0.4) == ("a note", "groq/gpt-oss-120b")
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer gsk-test"
    content = sent["body"]["messages"][0]["content"]
    assert isinstance(content, str) and "cannot see images" in content
    assert sent["body"]["max_tokens"] == 16000  # capped to what the engine takes


def test_a_vision_model_sees_the_slides(monkeypatch):
    sent = {}
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["k"])
    monkeypatch.setattr(llm.httpx, "post", lambda url, **kw: sent.update(kw["json"]) or FakeResponse(
        200, {"choices": [{"message": {"content": "ok"}}]}))
    llm._compatible_engine(spec(vision=True))("prompt", [Picture("S001", b"jpeg")], 1000, 0.4)
    parts = sent["messages"][0]["content"]
    assert parts[0]["text"] == "prompt" and parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_rate_limits_are_busy_and_bad_requests_are_not(monkeypatch):
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["k"])
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(429, {"error": {"message": "slow down"}}))
    with pytest.raises(llm.Busy):
        llm._compatible_engine(spec())("p", [], 100, 0)
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(400, {"error": {"message": "bad model"}}))
    with pytest.raises(llm.EngineError) as err:
        llm._compatible_engine(spec())("p", [], 100, 0)
    assert not isinstance(err.value, llm.Busy)


def test_a_missing_key_is_named(monkeypatch):
    monkeypatch.setattr(llm, "load_api_keys", lambda name: [])
    with pytest.raises(llm.EngineError, match="GROQ_API_KEY"):
        llm._compatible_engine(spec())("p", [], 100, 0)


def test_a_lite_engine_is_never_used_to_upgrade_a_note():
    with pytest.raises(llm.EngineError, match="lite"):
        llm._compatible_engine(spec(tier="lite", key=None))("p", [], 100, 0, quality="full")


def test_a_long_lecture_is_trimmed_to_fit_a_small_model():
    prompt = ("Instructions.\n<<<COURSE NOTEBOOK\n" + "code\n" * 5000 + "COURSE NOTEBOOK>>>\n"
              "Transcript (timestamps in [mm:ss]):\n[00:00] start " + "words " * 20000 + "[59:00] the end")
    fitted = llm.fit_prompt(prompt, 20000)
    assert len(fitted) < 21000
    assert "COURSE NOTEBOOK" not in fitted and "does not fit this model" in fitted
    assert "[00:00] start" in fitted and fitted.endswith("[59:00] the end")
    assert "trimmed to fit this model" in fitted
    assert llm.fit_prompt("short", 100) == "short"


# --- a real local server, end to end ------------------------------------------------

class LocalModel(BaseHTTPRequestHandler):
    """What Ollama, LM Studio or llama.cpp look like to Margin."""
    received = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LocalModel.received.append((self.path, self.headers.get("Authorization"), body))
        answer = json.dumps({"choices": [{"message": {"role": "assistant", "content": "<<<NOTE>>>\nlocal note"}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(answer.encode())

    def log_message(self, *args):
        pass


def test_a_local_model_writes_when_everything_before_it_fails(config, monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), LocalModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        config(f'chain = ["gemini", "local"]\n[engines.local]\nbase_url = "http://127.0.0.1:{port}/v1"\n'
               f'model = "qwen3.5-9b"\n')

        def gemini_down(*a, **k):
            raise llm.Busy("overloaded")

        monkeypatch.setitem(llm._ENGINES, "gemini", gemini_down)
        text, engine = llm.generate("write the note", [Picture("S001", b"jpeg")])
        assert (text, engine) == ("<<<NOTE>>>\nlocal note", "local/qwen3.5-9b")
        path, auth, body = LocalModel.received[-1]
        assert path == "/v1/chat/completions" and auth is None and body["model"] == "qwen3.5-9b"
        assert llm.is_lite(engine)  # so the upgrader rewrites it with a full model later
    finally:
        server.shutdown()


def test_a_local_server_that_is_not_running_says_so(config):
    config('[engines.local]\nprovider = "ollama"\nbase_url = "http://127.0.0.1:1/v1"\nmodel = "llama3.2"\n')
    with pytest.raises(llm.EngineError, match="nothing is listening"):
        llm._compatible_engine(providers.load()[0]["local"])("p", [], 100, 0)


def test_a_per_minute_token_limit_is_temporary(monkeypatch):
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["k"])
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(413, {"error": {"message":
        "Request too large for model on tokens per minute (TPM): Limit 8000, Requested 8415"}}))
    with pytest.raises(llm.Busy):
        llm._compatible_engine(spec())("p", [], 100, 0)


def test_an_answer_cut_off_by_its_limit_is_refused(monkeypatch):
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["k"])
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(
        200, {"choices": [{"finish_reason": "length", "message": {"content": "half a note"}}]}))
    with pytest.raises(llm.EngineError, match="output limit"):
        llm._compatible_engine(spec())("p", [], 100, 0)


# --- the model menu ------------------------------------------------------------------

def test_a_chosen_model_goes_first_and_the_chain_backs_it_up(monkeypatch):
    monkeypatch.setattr(llm, "engine_chain", lambda: ["gemini"])
    monkeypatch.setitem(llm._ENGINES, "gemini", lambda *a: ("<<<NOTE>>>\nx\n<<<GIST>>>\ng", "gemini-3.5-flash"))
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["k"])
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(
        200, {"choices": [{"message": {"content": "<<<NOTE>>>\nfrom groq\n<<<GIST>>>\ng"}}]}))
    choice = {"provider": "groq", "model": "openai/gpt-oss-120b"}
    assert llm.generate("p", choice=choice)[1] == "groq/openai/gpt-oss-120b"
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(503, {"error": {"message": "down"}}))
    assert llm.generate("p", choice=choice)[1] == "gemini-3.5-flash"  # your default takes over


def test_a_gemini_or_claude_pick_uses_their_own_engines(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "_gemini", lambda *a, **k: seen.update(gemini=k.get("models")) or ("t", "g"))
    monkeypatch.setattr(llm, "_claude", lambda *a, **k: seen.update(claude=k.get("model")) or ("t", "c"))
    llm.choice_engine({"provider": "gemini", "model": "gemini-3.6-flash"})("p", [], 10, 0)
    llm.choice_engine({"provider": "claude", "model": "opus"})("p", [], 10, 0)
    assert seen == {"gemini": ["gemini-3.6-flash"], "claude": "opus"}
