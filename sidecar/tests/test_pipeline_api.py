"""The whole path, through the HTTP API, with only the model call faked."""
import base64
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from sidecar import compose as C
from sidecar import llm, main, sessions
from sidecar import notebook as NB

CANNED = """<<<NOTE>>>
> [!abstract] In one breath
> A portfolio's expected return is the weighted average of its assets' returns.

## The idea
Think of it as a blended smoothie.

## Weights and means [00:10]
$$\\mu_p = \\sum_i w_i \\mu_i$$
where $w_i$ is the weight of asset $i$.
{{slide:S001}}

```mermaid
flowchart LR
  A["Asset returns"] --> B["Weighted average"]
```

## In code
Computes and plots it.

## Check yourself
> [!question]- Why weights?
> Because money is split.
<<<GIST>>>
Expected portfolio return is the weight-averaged mean of asset returns.
<<<CODE>>>
# %% [markdown]
# ## 28 · Expected return

# %%
import numpy as np
import matplotlib.pyplot as plt
w = np.array([0.6, 0.4]); mu = np.array([0.08, 0.12])
print(round(float(w @ mu), 4))
plt.bar(["A", "B"], mu); plt.show()
<<<END>>>
"""


def _jpeg() -> str:
    img = Image.new("RGB", (640, 360), "white")
    ImageDraw.Draw(img).rectangle([40, 40, 600, 100], fill="navy")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / ".obsidian").mkdir()  # the vault's parent is an Obsidian vault
    monkeypatch.setattr(sessions, "SESSIONS_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (CANNED, "fake-model"))
    main.app.dependency_overrides[main.get_vault_path] = lambda: vault
    yield TestClient(main.app), vault
    main.app.dependency_overrides.clear()


META = {
    "platform": "udemy", "course_id": "1350350", "course_title": "Quant Finance",
    "section_title": "Modern Portfolio Theory", "section_index": 6,
    "lecture_id": "7986740", "lecture_title": "Expected return of the portfolio",
    "lecture_index": 28, "duration_sec": 336, "url": "https://www.udemy.com/course/q/learn/lecture/7986740",
}


def _wait_done(api, key, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api.get(f"/session/{key}").json()["status"]
        if status["state"] in ("done", "error"):
            return status
        time.sleep(0.3)
    raise AssertionError("compose never finished")


def test_health_reports_capabilities(client):
    api, _ = client
    body = api.get("/health").json()
    assert tuple(map(int, body["version"].split(".")[:2])) >= (2, 1)
    assert "gemini" in body["engines"]
    assert "available" in body["asr"]


def test_capture_then_compose_produces_note_notebook_and_index(client):
    api, vault = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [
        {"start": 0, "end": 5, "text": "Today, expected return."},
        {"start": 10, "end": 20, "text": "It is a weighted average of the means."},
    ]})
    frame = api.post(f"/session/{key}/frame", json={"t": 12.5, "data": _jpeg()}).json()
    assert frame["id"] == "S001"
    assert api.get(f"/session/{key}/frame/S001").headers["content-type"] == "image/jpeg"
    assert api.post(f"/session/{key}/watched", json={"start": 0, "end": 336}).json()["coverage"] == 1.0

    assert api.post(f"/session/{key}/compose").json()["started"] is True
    status = _wait_done(api, key)
    assert status["state"] == "done", status
    result = status["result"]
    assert result["engine"] == "fake-model"
    assert result["obsidian_uri"].startswith("obsidian://open?vault=")

    note = api.get(f"/session/{key}/note").json()["content"]
    assert note.startswith("---\n") and "# 28 · Expected return of the portfolio" in note
    assert "gist: Expected portfolio return" in note
    assert "![[assets/28-S001.jpg|720]]" in note
    assert "[00:10](https://www.udemy.com/course/q/learn/lecture/7986740#t=10)" in note
    assert "[[00 - Quant Finance|Course index]]" in note

    course = vault / "Quant Finance"
    assert (course / "assets" / "28-S001.jpg").exists()
    index = (course / "00 - Quant Finance.md").read_text()
    assert "## Modern Portfolio Theory" in index
    assert "[[28 - Expected return of the portfolio]]" in index

    if NB.kernel_available():
        assert result["run"]["ok"] is True, result["run"]
        assert result["figures"] == ["28-fig1.png"]
        assert "![[assets/28-fig1.png|640]]" in note
        code = api.get(f"/session/{key}/code").json()
        outputs = [o for c in code["cells"] for o in c["outputs"]]
        assert any(o["type"] == "text" and "0.096" in o["text"] for o in outputs)
        assert any(o["type"] == "image" for o in outputs)

    lectures = api.get("/course/1350350").json()["lectures"]
    assert lectures[0]["composed"] is True and lectures[0]["lecture_index"] == 28


def test_someone_without_a_model_is_told_to_add_one_not_every_engines_reason(client, monkeypatch):
    # A new user's first lecture ends before they have set up any model.
    def no_model(prompt, pictures=None, **kw):
        raise llm.EngineError("Every engine failed. gemini: no Gemini key | claude: no CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.setattr(C, "generate", no_model)
    api, _ = client
    key = api.post("/session", json={**META, "lecture_id": "first"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Today, expected return."}]})
    api.post(f"/session/{key}/compose")
    status = _wait_done(api, key)
    assert status["state"] == "error"
    assert status["message"].startswith("Margin needs a model") and "CLAUDE_CODE_OAUTH_TOKEN" not in status["message"]


def test_compose_with_nothing_captured_fails_visibly(client):
    api, _ = client
    key = api.post("/session", json={**META, "lecture_id": "empty"}).json()["key"]
    api.post(f"/session/{key}/compose")
    status = _wait_done(api, key)
    assert status["state"] == "error" and "Nothing captured" in status["message"]


def test_unknown_session_is_404(client):
    api, _ = client
    assert api.get("/session/nope").status_code == 404
    assert api.get("/session/..%2F..%2Fetc/frame/S001").status_code == 404


def test_audio_is_skipped_once_captions_exist(client):
    api, _ = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 1, "text": "hi"}]})
    body = api.post(f"/session/{key}/audio", json={"data": "AAAA", "video_start": 0}).json()
    assert "skipped" in body


def test_web_origins_cannot_read_the_sidecar(client):
    api, _ = client
    evil = api.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    ext = api.get("/health", headers={"Origin": "chrome-extension://" + "a" * 32})
    assert ext.headers.get("access-control-allow-origin") == "chrome-extension://" + "a" * 32


def test_lookup_finds_a_lecture_without_creating_one(client):
    api, _ = client
    yt = {"platform": "youtube", "lecture_id": "abc123", "lecture_title": "Some video"}
    assert api.post("/session/lookup", json=yt).json() == {"exists": False, "summary": None}
    assert api.post("/session/lookup", json=yt).json()["exists"] is False  # still nothing stored
    key = api.post("/session", json=yt).json()["key"]
    found = api.post("/session/lookup", json=yt).json()
    assert found["exists"] and found["summary"]["key"] == key


def test_a_rewatch_that_finds_a_new_slide_rewrites_the_same_note(client):
    api, vault = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    api.post(f"/session/{key}/frame", json={"t": 12.5, "data": _jpeg()})
    api.post(f"/session/{key}/compose")
    assert _wait_done(api, key)["state"] == "done"
    assert api.get(f"/session/{key}").json()["stale"] is False

    # Second watch: the same captions and slide add nothing...
    assert api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]}).json()["stale"] is False
    assert api.post(f"/session/{key}/frame", json={"t": 13, "data": _jpeg()}).json()["stale"] is False
    # ...a slide the first watch missed does.
    img = Image.new("RGB", (640, 360), "white")
    ImageDraw.Draw(img).ellipse([300, 150, 600, 330], fill="darkred")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    new = api.post(f"/session/{key}/frame", json={"t": 200, "data": base64.b64encode(buf.getvalue()).decode()}).json()
    assert new["duplicate"] is False and new["stale"] is True

    api.post(f"/session/{key}/compose")
    assert _wait_done(api, key)["state"] == "done"
    assert api.get(f"/session/{key}").json()["stale"] is False
    notes = list((vault / "Quant Finance").glob("*.md"))
    assert sorted(p.name for p in notes) == ["00 - Quant Finance.md", "28 - Expected return of the portfolio.md"]


def test_lecture_resources_survive_the_trip_into_the_session(client):
    api, _ = client
    meta = {**META, "lecture_id": "res", "resources": [{"title": "Slides", "kind": "file"}],
            "course_links": [{"title": "Github link", "url": "https://github.com/ed-donner/llm_engineering"}]}
    key = api.post("/session", json=meta).json()["key"]
    stored = api.get(f"/session/{key}").json()["meta"]
    assert stored["resources"][0]["title"] == "Slides"
    assert stored["course_links"][0]["url"] == "https://github.com/ed-donner/llm_engineering"


# --- your edits, Lite notes, stale code ---------------------------------------------

def _compose_done(api, key, full=False):
    api.post(f"/session/{key}/compose", json={"full": True} if full else None)
    assert _wait_done(api, key)["state"] == "done"


def test_a_note_you_edited_is_kept_when_rewritten(client):
    api, vault = client
    key = api.post("/session", json={**META, "lecture_id": "ed"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    note = vault / "Quant Finance" / "28 - Expected return of the portfolio.md"
    assert api.get(f"/session/{key}").json()["edited"] is False
    note.write_text(note.read_text() + "\nMy own line.\n")
    assert api.get(f"/session/{key}").json()["edited"] is True

    # Margin's own decision (a lecture ended) leaves it alone...
    auto = api.post(f"/session/{key}/compose", json={"auto": True}).json()
    assert auto["started"] is False and auto["skipped"] == "edited"
    # ...your "Rewrite the notes" rewrites it, and your version is kept beside it.
    _compose_done(api, key, full=True)
    kept = list((vault / "Quant Finance" / ".margin-history").glob("*your edits*.md"))
    assert len(kept) == 1 and "My own line." in kept[0].read_text()
    rewritten = note.read_text()
    assert "Rewritten by Margin with new material" in rewritten and ".margin-history/" in rewritten
    assert rewritten.index("# 28") < rewritten.index("[!note] Rewritten")
    assert api.get(f"/session/{key}").json()["edited"] is False


def test_a_note_from_a_lite_model_is_upgraded_later(client, monkeypatch):
    from sidecar import pipeline as P

    api, vault = client
    engines = iter(["gemini-3.5-flash-lite", "gemini-3.5-flash"])
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (CANNED, next(engines)))
    key = api.post("/session", json={**META, "lecture_id": "lite"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    session = sessions.load_session(key)
    due, tries = session.upgrade
    assert tries == 0 and due > time.time() + 60  # not straight away

    assert P.upgrade_once(vault, now=time.time()) == []      # not yet due
    assert P.upgrade_once(vault, now=due + 1) == [key]       # due: rewritten
    assert session.upgrade is None
    note = (vault / "Quant Finance" / "28 - Expected return of the portfolio.md").read_text()
    assert "engine: gemini-3.5-flash\n" in note


def test_an_upgrade_that_finds_every_model_busy_backs_off_and_keeps_the_note(client, monkeypatch):
    from sidecar import llm
    from sidecar import pipeline as P

    api, vault = client
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (CANNED, "gemini-3.5-flash-lite"))
    key = api.post("/session", json={**META, "lecture_id": "busy"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    session = sessions.load_session(key)
    due, _ = session.upgrade

    def busy(*a, **k):
        raise llm.Busy("overloaded")

    monkeypatch.setattr(C, "generate", busy)
    assert P.upgrade_once(vault, now=due + 1) == []
    later, tries = session.upgrade
    assert tries == 1 and later > due + 1
    assert session.status["state"] == "done"  # the panel still shows the note it has


def test_a_note_you_deleted_is_not_brought_back_by_an_upgrade(client, monkeypatch):
    from sidecar import pipeline as P

    api, vault = client
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (CANNED, "gemini-3.5-flash-lite"))
    key = api.post("/session", json={**META, "lecture_id": "gone"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    (vault / "Quant Finance" / "28 - Expected return of the portfolio.md").unlink()
    due, _ = sessions.load_session(key).upgrade
    assert P.upgrade_once(vault, now=due + 1) == []
    assert not (vault / "Quant Finance" / "28 - Expected return of the portfolio.md").exists()
    assert sessions.load_session(key).upgrade is None


def test_a_rewrite_without_code_removes_the_old_notebook_section(client, monkeypatch):
    import nbformat

    api, vault = client
    key = api.post("/session", json={**META, "lecture_id": "nocode"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    nb_path = vault / "Quant Finance" / "Quant Finance.ipynb"
    ids = lambda: {str(c.metadata.get("margin", {}).get("lecture_id")) for c in nbformat.read(nb_path, 4).cells}
    assert "nocode" in ids()
    no_code = CANNED.split("<<<CODE>>>")[0] + "<<<CODE>>>\nNONE\n<<<END>>>\n"
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (no_code, "fake-model"))
    _compose_done(api, key, full=True)
    assert "nocode" not in ids()
    assert "## In code" not in (vault / "Quant Finance" / "28 - Expected return of the portfolio.md").read_text()


def test_notes_from_before_fingerprints_are_adopted_once(client, monkeypatch):
    from sidecar import pipeline as P

    api, vault = client
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (CANNED, "gemini-3.5-flash-lite"))
    key = api.post("/session", json={**META, "lecture_id": "old"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    session = sessions.load_session(key)
    session._update_material(written_sha=None, upgrade_at=None, upgrade_tries=None)  # as an older Margin left it
    assert P.adopt_existing_notes(vault) == [key]
    assert session.written_sha and session.upgrade is not None
    assert P.adopt_existing_notes(vault) == []  # once


def test_earlier_lectures_are_written_from_captions_one_at_a_time(client, monkeypatch):
    import threading

    api, vault = client
    active, most = [0], [0]
    guard = threading.Lock()

    def slow_generate(prompt, pictures=None, **kw):
        with guard:
            active[0] += 1
            most[0] = max(most[0], active[0])
        time.sleep(0.2)
        with guard:
            active[0] -= 1
        return CANNED.replace("28 · Expected return", "x"), "fake-model"

    monkeypatch.setattr(C, "generate", slow_generate)
    keys = []
    for i, lid in enumerate(["b1", "b2", "b3"]):
        key = api.post("/session", json={**META, "lecture_id": lid, "lecture_index": 1 + i,
                                         "lecture_title": f"Earlier lecture {i + 1}"}).json()["key"]
        api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": f"Lecture {i + 1}."}]})
        keys.append(key)
    empty = api.post("/session", json={**META, "lecture_id": "b4"}).json()["key"]  # no captions: skipped
    other = api.post("/session", json={**META, "course_id": "999", "lecture_id": "b5"}).json()["key"]

    queued = api.post("/course/1350350/backfill", json={"keys": keys + [empty, other]}).json()["queued"]
    assert queued == keys
    for key in keys:
        assert _wait_done(api, key)["state"] == "done"
    assert most[0] == 1  # never two at once
    note = (vault / "Quant Finance" / "01 - Earlier lecture 1.md").read_text()
    assert "written_from: captions" in note and "slides_captured: 0" in note
    # Asking again changes nothing: they have notes now.
    assert api.post("/course/1350350/backfill", json={"keys": keys}).json()["queued"] == []


def test_notes_link_both_ways_however_they_were_watched(client):
    api, vault = client
    folder = vault / "Quant Finance"

    def write(index, title):
        key = api.post("/session", json={**META, "lecture_id": f"nav{index}", "lecture_index": index,
                                         "lecture_title": title}).json()["key"]
        api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": title}]})
        _compose_done(api, key)
        return key

    write(28, "Expected return")
    write(26, "Returns")
    write(27, "Risk")                       # written last, between the two
    nav = lambda n, t: next(ln for ln in (folder / f"{n} - {t}.md").read_text().splitlines() if "Course index" in ln)
    assert nav(26, "Returns") == "[[00 - Quant Finance|Course index]] · [[27 - Risk]] →"
    assert nav(27, "Risk") == "← [[26 - Returns]] · [[00 - Quant Finance|Course index]] · [[28 - Expected return]] →"
    assert nav(28, "Expected return") == "← [[27 - Risk]] · [[00 - Quant Finance|Course index]]"
    # Margin's own relinking is not mistaken for your edits...
    assert api.get(f"/session/{sessions.session_key({**META, 'lecture_id': 'nav26'})}").json()["edited"] is False
    # ...and a neighbour you edited keeps its line as you left it.
    mine = (folder / "28 - Expected return.md").read_text() + "\nmine\n"
    (folder / "28 - Expected return.md").write_text(mine)
    write(29, "Beta")
    assert (folder / "28 - Expected return.md").read_text() == mine
    assert nav(29, "Beta") == "← [[28 - Expected return]] · [[00 - Quant Finance|Course index]]"


def test_a_model_picked_in_the_menu_writes_the_note_and_is_not_upgraded_away(client, monkeypatch):
    api, vault = client
    got = {}

    def generate(prompt, pictures=None, **kw):
        got["choice"] = kw.get("choice")
        return CANNED, "local/qwen3.5-9b"

    monkeypatch.setattr(C, "generate", generate)
    monkeypatch.setattr("sidecar.providers.lite_labels", lambda: {"local/qwen3.5-9b"})
    key = api.post("/session", json={**META, "lecture_id": "picked"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "x"}]})
    choice = {"provider": "ollama", "model": "qwen3.5-9b"}
    api.post(f"/session/{key}/compose", json={"engine": choice})
    assert _wait_done(api, key)["state"] == "done"
    assert got["choice"] == choice
    assert sessions.load_session(key).upgrade is None  # your pick is respected


def test_the_menu_endpoints(client, monkeypatch):
    api, _ = client
    monkeypatch.setattr("sidecar.providers.menu", lambda: [{"id": "groq", "name": "Groq", "ready": True}])
    body = api.get("/providers").json()
    assert body["providers"][0]["id"] == "groq" and "gemini" in body["default"]
    assert api.get("/providers/nope/models").status_code == 400
    monkeypatch.setattr("sidecar.llm.test_choice", lambda c: {"ok": True, "model": c["model"], "seconds": 0.4})
    assert api.post("/providers/test", json={"provider": "groq", "model": "m"}).json()["ok"] is True


def test_closing_the_tab_writes_the_note_only_when_it_needs_it(client):
    # The tab closing is Margin's own decision, like moving to the next lecture:
    # a first note once most of the lecture was watched, an update when
    # something new came in, and never over a note you edited.
    api, vault = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    api.post(f"/session/{key}/frame", json={"t": 12.5, "data": _jpeg()})
    closing = {"auto": True, "if_needed": True}

    api.post(f"/session/{key}/watched", json={"start": 0, "end": 60})  # a few minutes of it
    assert api.post(f"/session/{key}/compose", json=closing).json()["started"] is False

    api.post(f"/session/{key}/watched", json={"start": 60, "end": 300})  # most of it
    assert api.post(f"/session/{key}/compose", json=closing).json()["started"] is True
    assert _wait_done(api, key)["state"] == "done"

    assert api.post(f"/session/{key}/compose", json=closing).json()["started"] is False  # nothing new
    img = Image.new("RGB", (640, 360), "white")
    ImageDraw.Draw(img).ellipse([300, 150, 600, 330], fill="darkred")
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    api.post(f"/session/{key}/frame", json={"t": 200, "data": base64.b64encode(buf.getvalue()).decode()})
    assert api.post(f"/session/{key}/compose", json=closing).json()["started"] is True  # a new slide
    assert _wait_done(api, key)["state"] == "done"


def test_adding_whats_new_keeps_the_note_and_writes_only_the_new_part(client, monkeypatch):
    # "Add what's new" wrote the whole note again, from whichever model was
    # free: a 3,400-word note came back as 1,400 words. Now only the parts
    # watched since go to the model, with the note so far, and are added in.
    api, vault = client
    key = api.post("/session", json={**META, "lecture_id": "grow"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [
        {"start": 0, "end": 5, "text": "Today, expected return."},
        {"start": 100, "end": 110, "text": "Variance measures risk."}]})
    api.post(f"/session/{key}/watched", json={"start": 0, "end": 60})
    _compose_done(api, key)
    note_path = vault / "Quant Finance" / "28 - Expected return of the portfolio.md"
    first = note_path.read_text()

    api.post(f"/session/{key}/watched", json={"start": 60, "end": 300})  # watched on
    asked = []

    def update(prompt, pictures=None, **kw):
        asked.append(prompt)
        return ("<<<NOTE>>>\n## Risk [01:40]\nVariance measures how far returns swing around the mean.\n\n"
                "## Check yourself\n> [!question]- What measures risk?\n> Variance.\n<<<CODE>>>\nNONE\n<<<END>>>",
                "fake-model")
    monkeypatch.setattr(C, "generate", update)
    _compose_done(api, key)

    prompt = asked[0]
    assert "THE NOTE SO FAR" in prompt and "blended smoothie" in prompt           # it saw the note
    assert "Variance measures risk." in prompt and "Today, expected return." not in prompt  # only the new part
    note = note_path.read_text()
    assert note.index("## Weights and means") < note.index("## Risk [01:40]")      # added in lecture order
    assert first.split("## Weights and means")[1].split("## In code")[0].strip() in note  # old section intact
    assert "Why weights?" in note and "What measures risk?" in note
    assert api.get(f"/session/{key}").json()["status"]["message"].startswith("Added 01:00 to 05:00")

    asked.clear()
    _compose_done(api, key)  # nothing watched since: nothing written
    assert asked == [] and note_path.read_text() == note


def test_rewrite_the_notes_still_writes_it_all_again(client, monkeypatch):
    api, vault = client
    key = api.post("/session", json={**META, "lecture_id": "again"}).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Expected return."}]})
    _compose_done(api, key)
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (
        CANNED.replace("blended smoothie", "fruit salad"), "fake-model"))
    _compose_done(api, key, full=True)
    assert "fruit salad" in (vault / "Quant Finance" / "28 - Expected return of the portfolio.md").read_text()
