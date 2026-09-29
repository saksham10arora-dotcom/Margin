"""The engine chain under the failures seen in real use: Gemini overloaded for
minutes, an OpenRouter balance too small for a whole note, a sidecar
restarted in the middle of writing."""
import json

import httpx
import pytest

from sidecar import llm, main, pipeline, sessions


class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def ok_gemini(text="the note"):
    return FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def overloaded():
    return FakeResponse(503, {"error": {"code": 503, "message": "This model is currently experiencing high demand."}})


@pytest.fixture
def calls(monkeypatch):
    """Record every request; the test decides each answer."""
    seen = []
    monkeypatch.setattr(llm, "load_api_keys", lambda name: [] if "CLAUDE" in name
                        else ["key-one-0123456789abcdef", "key-two-0123456789abcdef"])
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    return seen


def test_an_overloaded_model_hands_off_to_the_next_one(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash", "backup-flash"])

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers))
        return overloaded() if "main-flash" in url else ok_gemini()

    monkeypatch.setattr(llm.httpx, "post", post)
    progress = []
    text, engine = llm.generate("prompt", progress=progress.append)
    assert (text, engine) == ("the note", "backup-flash")
    # One retry of the preferred model, then straight on: no cycling through
    # every key for an overload that is per model.
    assert [u.split("/")[-1] for u, _ in calls] == ["main-flash:generateContent"] * 2 + ["backup-flash:generateContent"]
    assert progress == ["Main Flash is busy, trying Backup Flash"]


def test_keys_travel_in_a_header_never_the_url(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash"])

    def post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers))
        return ok_gemini()

    monkeypatch.setattr(llm.httpx, "post", post)
    llm.generate("prompt")
    url, headers = calls[0]
    assert "key" not in url and headers["x-goog-api-key"].startswith("key-")


def test_a_spent_key_moves_to_the_next_key_not_the_next_model(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash", "backup-flash"])
    monkeypatch.setattr(llm, "_gemini_turn", 0)

    def post(url, headers=None, json=None, timeout=None):
        calls.append(headers["x-goog-api-key"])
        if headers["x-goog-api-key"].startswith("key-one"):
            return FakeResponse(429, {"error": {"message": "quota"}})
        return ok_gemini()

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm.generate("prompt")[1] == "main-flash"
    assert [k[:7] for k in calls] == ["key-one", "key-two"]


def test_everything_busy_is_reported_as_busy_so_it_is_retried(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash"])
    monkeypatch.setattr(llm, "engine_chain", lambda: ["gemini"])
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: overloaded())
    with pytest.raises(llm.Busy):
        llm.generate("prompt")


def test_a_bad_request_is_not_retried(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash", "backup-flash"])
    monkeypatch.setattr(llm, "engine_chain", lambda: ["gemini"])

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return FakeResponse(400, {"error": {"message": "API key not valid"}})

    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(llm.EngineError) as err:
        llm.generate("prompt")
    assert not isinstance(err.value, llm.Busy)
    assert "API key not valid" in str(err.value) and len(calls) == 1


def test_openrouter_asks_for_what_the_balance_covers(monkeypatch, calls):
    monkeypatch.setattr(llm, "OPENROUTER_MODELS", ["google/flash"])
    budgets = []

    def post(url, headers=None, json=None, timeout=None):
        budgets.append(json["max_tokens"])
        if json["max_tokens"] > 9000:
            return FakeResponse(402, {"error": {"message": "You requested up to 24000 tokens, but can only afford 9000."}})
        return FakeResponse(200, {"choices": [{"message": {"content": "a note"}}]})

    monkeypatch.setattr(llm.httpx, "post", post)
    assert llm._openrouter("prompt", [], 24000, 0.4) == ("a note", "openrouter/google/flash")
    assert budgets == [24000, 9000]


def test_openrouter_with_too_little_credit_for_a_note_says_so(monkeypatch, calls):
    monkeypatch.setattr(llm, "OPENROUTER_MODELS", ["google/flash"])
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResponse(
        402, {"error": {"message": "You requested up to 24000 tokens, but can only afford 4792."}}))
    with pytest.raises(llm.EngineError, match="too little credit"):
        llm._openrouter("prompt", [], 24000, 0.4)


def test_a_network_failure_counts_as_busy(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash"])
    monkeypatch.setattr(llm, "engine_chain", lambda: ["gemini"])

    def post(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(llm.Busy):
        llm.generate("prompt")


# --- the pipeline around it ------------------------------------------------------

UDEMY = {"platform": "udemy", "course_id": "9", "lecture_id": "1", "lecture_title": "Intro", "lecture_index": 1}


def test_a_busy_spell_is_waited_out_and_the_note_still_written(tmp_path, monkeypatch):
    session = sessions.open_session(UDEMY, root=tmp_path)
    attempts = []

    def run(s, vault, choice=None):
        attempts.append(s.status.get("message", ""))
        if len(attempts) < 3:
            raise llm.Busy("overloaded")
        return {"ok": True}

    waits = []
    monkeypatch.setattr(pipeline, "run", run)
    monkeypatch.setattr(pipeline.time, "sleep", waits.append)
    assert pipeline._run_with_retries(session, tmp_path) == {"ok": True}
    assert waits == list(pipeline.RETRY_WAITS_SEC[:2])
    assert attempts[1].startswith("Every model is busy right now. Trying again at")


def test_a_note_asked_for_while_one_is_being_written_is_written_again(tmp_path, monkeypatch):
    session = sessions.open_session(UDEMY, root=tmp_path)
    runs = []

    def run(s, vault, choice=None):
        runs.append(1)
        rev = s.material_rev  # what this note is written from, as in the real run()
        if len(runs) == 1:
            # Material arrives and a second compose is asked for mid-write.
            s.add_asr_cues([{"start": 0, "end": 30, "text": "new speech"}])
            assert pipeline.start(s, vault) is False
        s.mark_composed(rev)
        return {}

    monkeypatch.setattr(pipeline, "run", run)
    with pipeline._running_guard:
        pipeline._running.add(session.key)
    pipeline._run_guarded(session, tmp_path)
    assert len(runs) == 2 and not pipeline.is_running(session.key)


def test_notes_interrupted_by_a_restart_are_picked_up_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_ROOT", tmp_path)
    started = []

    def enqueue(sessions, vault):
        started.extend(s.key for s in sessions)
        return [s.key for s in sessions]

    monkeypatch.setattr(pipeline, "enqueue", enqueue)
    session = sessions.open_session(UDEMY, root=tmp_path)
    idle = sessions.open_session({**UDEMY, "lecture_id": "2"}, root=tmp_path)
    session.set_status("composing", "Writing notes")
    idle.set_status("done", "Notes ready")

    assert pipeline.resume_interrupted(tmp_path) == [session.key]
    assert session.status["resumed"] is True
    # Interrupted again before it could finish: left for a person to retry.
    session.set_status("composing", "Writing notes")
    assert pipeline.resume_interrupted(tmp_path) == []
    assert session.status["state"] == "error"
    assert started == [session.key]


def test_the_log_is_scrubbed_of_keys_and_kept_bounded(tmp_path):
    log = tmp_path / "sidecar.log"
    log.write_text('POST https://x/models/m:generateContent?key=AQ.FAKE-key-for-tests-only-00 "503"\n' + "x" * 50)
    main.tidy_log(log, limit=40, keep=30)
    text = log.read_text()
    assert "AQ.FAKE" not in text and len(text) == 30
    small = tmp_path / "small.log"
    small.write_text('GET ?key=AQ.FAKE-key-for-tests-only-00 ok\n')
    main.tidy_log(small)
    assert small.read_text() == "GET ?key=[redacted] ok\n"


def test_full_quality_leaves_out_the_lite_models(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash", "backup-flash-lite"])
    monkeypatch.setattr(llm, "engine_chain", lambda: ["gemini", "gemini-lite"])

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url.split("/")[-1])
        return overloaded() if "main-flash:" in url else ok_gemini()

    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(llm.Busy):
        llm.generate("prompt", quality="full")
    assert all("lite" not in c for c in calls)
    assert llm.generate("prompt")[1] == "backup-flash-lite"


def test_the_default_chain_puts_claude_before_the_lite_models():
    assert llm.DEFAULT_CHAIN.split(",") == ["gemini", "claude", "openrouter", "gemini-lite"]


def test_claude_is_not_tried_without_a_token(monkeypatch):
    monkeypatch.setattr(llm, "load_api_keys", lambda name: [])
    ran = []
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: ran.append(1))
    with pytest.raises(llm.EngineError, match="setup-token"):
        llm._claude("prompt", [], 1000, 0.4)
    assert ran == []


def test_claude_runs_isolated_and_stops_at_its_daily_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["sk-ant-oat-token"] if "CLAUDE" in name else [])
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(llm, "CLAUDE_DAILY", 2)
    commands = []

    class Done:
        returncode = 0
        stdout = json.dumps({"is_error": False, "result": "a note"})
        stderr = ""

    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: commands.append(cmd) or Done())
    assert llm._claude("p", [], 1000, 0.4)[0] == "a note"
    cmd = commands[0]
    for flag in ("--strict-mcp-config", "--disable-slash-commands"):
        assert flag in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "" and cmd[cmd.index("--tools") + 1] == "Read"
    assert cmd[-2] == "--"  # the prompt comes last, after --, so no option can swallow it
    llm._claude("p", [], 1000, 0.4)
    with pytest.raises(llm.EngineError, match="daily limit"):
        llm._claude("p", [], 1000, 0.4)
    assert len(commands) == 2


def test_claude_uses_the_token_and_reports_rate_limits_as_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(llm, "load_api_keys", lambda name: ["sk-ant-oat-token"] if "CLAUDE" in name else [])
    seen = {}

    class Done:
        returncode = 1
        stdout = json.dumps({"is_error": True, "result": "Claude AI usage limit reached"})
        stderr = ""

    def run(cmd, **kw):
        seen.update(kw)
        return Done()

    monkeypatch.setattr(llm.subprocess, "run", run)
    with pytest.raises(llm.Busy):
        llm._claude("prompt", [], 1000, 0.4)
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-token"


def test_a_cut_off_answer_moves_to_the_next_model(monkeypatch, calls):
    monkeypatch.setattr(llm, "GEMINI_MODELS", ["main-flash", "backup-flash"])
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json["generationConfig"]["maxOutputTokens"])
        if "main-flash" in url:
            return FakeResponse(200, {"candidates": [{"finishReason": "MAX_TOKENS",
                                                      "content": {"parts": [{"text": "## Check yourself\n> [!question]- What is"}]}}]})
        return ok_gemini("<<<NOTE>>>\nfull\n<<<GIST>>>\ng")

    monkeypatch.setattr(llm.httpx, "post", post)
    text, engine = llm.generate("prompt")
    assert engine == "backup-flash" and "full" in text
    assert sent[0] == llm.GEMINI_MAX_OUTPUT  # thinking has room, the answer too


def test_an_incomplete_note_is_not_accepted(monkeypatch, calls):
    from sidecar import compose as C

    monkeypatch.setattr(llm, "engine_chain", lambda: ["first", "second"])
    monkeypatch.setitem(llm._ENGINES, "first", lambda *a: ("<<<NOTE>>>\n## Topic\nstops here", "first-model"))
    monkeypatch.setitem(llm._ENGINES, "second", lambda *a: ("<<<NOTE>>>\nwhole\n<<<GIST>>>\ng\n<<<END>>>", "second-model"))
    assert llm.generate("p", accept=C.is_complete)[1] == "second-model"
    assert C.is_complete("**GIST**\nx") and not C.is_complete("## just a note")
