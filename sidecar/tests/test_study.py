"""The study tools: the crux, asking the lecture, and flashcards to quiz."""
from sidecar import compose as C
from sidecar import llm, study
from sidecar.tests.test_pipeline_api import CANNED, META, _jpeg, _wait_done, client  # noqa: F401 (fixture)

CRUX = """Expected return is a weighted average; risk is not.
1. **Weights are money shares**: they sum to one because together they are all your money.
2. **Returns average, risk does not**: combining assets that do not move together lowers risk.
**Remember:** $\\mu_p = \\sum_i w_i \\mu_i$
**If you forget everything else:** a portfolio's return is the weighted mean of its assets' returns."""

WITH_CRUX = CANNED.replace("<<<CODE>>>", f"<<<CRUX>>>\n{CRUX}\n<<<CODE>>>")


def test_the_crux_block_is_read_and_tidied():
    assert C.parse_crux(WITH_CRUX) == CRUX
    assert C.parse_crux(CANNED) == ""  # a model that leaves it out still gives a note
    assert C.parse_crux("<<<CRUX>>>\n## The crux\nIt is this — and that.\n<<<END>>>") == "It is this, and that."
    # The note itself is unchanged by it.
    assert C.parse_response(WITH_CRUX)[0] == C.parse_response(CANNED)[0]


def test_a_crux_states_the_idea_not_the_instruction():
    crux = lambda t: C.parse_crux(f"<<<CRUX>>>\n{t}\n<<<END>>>")
    assert crux("The single idea this lecture exists to teach is that RAG bridges the gap.") == "RAG bridges the gap."
    assert crux("The main idea: models need context.") == "Models need context."
    assert crux("This lecture teaches that weights sum to one.") == "Weights sum to one."
    # An idea that happens to start like that is left as it is.
    assert crux("The single idea here is simple: search first.") == "The single idea here is simple: search first."


def test_the_crux_sits_folded_under_in_one_breath_and_replaces_an_old_one():
    note = "---\ntitle: x\n---\n# 28 · X\n\n> [!abstract] In one breath\n> It is short.\n\n## The idea\nBody.\n"
    once = study.with_crux(note, "Old crux.")
    twice = study.with_crux(once, "The one idea.\n1. **First**: why.")
    assert twice.count("[!tldr]- The crux (80/20)") == 1
    assert "Old crux" not in twice
    breath, crux, idea = twice.index("In one breath"), twice.index("The crux"), twice.index("## The idea")
    assert breath < crux < idea
    assert "\n> 1. **First**: why.\n\n## The idea" in twice  # its own callout, not merged into the next
    assert study.crux_in_note(twice) == "The one idea.\n1. **First**: why."
    assert study.crux_in_note(note) is None


def test_a_new_note_gets_its_crux_in_the_vault_and_the_panel(client, monkeypatch):
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (WITH_CRUX, "fake-model"))
    api, vault = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Today, expected return."}]})
    api.post(f"/session/{key}/frame", json={"t": 1, "data": _jpeg()})
    api.post(f"/session/{key}/compose")
    assert _wait_done(api, key)["state"] == "done"
    note = api.get(f"/session/{key}/note").json()["content"]
    assert "> [!tldr]- The crux (80/20)" in note and "> **If you forget everything else:**" in note
    assert api.get(f"/session/{key}/crux").json()["crux"] == CRUX


def test_an_older_note_gets_a_crux_on_request_but_your_edits_are_never_touched(client, monkeypatch):
    api, vault = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Today, expected return."}]})
    api.post(f"/session/{key}/compose")  # the canned note has no crux
    assert _wait_done(api, key)["state"] == "done"
    assert api.get(f"/session/{key}/crux").status_code == 404
    monkeypatch.setattr(llm, "generate", lambda prompt, **kw: (CRUX, "fake-model"))
    made = api.post(f"/session/{key}/crux").json()
    assert made["crux"] == CRUX and made["engine"] == "fake-model"
    note_file = vault / api.get(f"/session/{key}/note").json()["filename"]
    assert "The crux (80/20)" in note_file.read_text()

    # Edited by you in Obsidian: the crux is made, shown, and not written in.
    edited = note_file.read_text().replace("> [!tldr]- The crux (80/20)", "> [!note] My own summary")
    note_file.write_text(edited.replace("blended smoothie", "blended smoothie, as I see it"))
    session_crux = vault.parent / "sessions" / key / "crux.json"
    session_crux.unlink()
    assert api.post(f"/session/{key}/crux").json()["crux"] == CRUX
    assert "The crux (80/20)" not in note_file.read_text()


def test_asking_the_lecture_answers_with_moments_to_jump_to(client, monkeypatch):
    api, _ = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [
        {"start": 10, "end": 20, "text": "It is a weighted average of the means."}]})
    seen = {}

    def answer(prompt, **kw):
        seen["prompt"] = prompt
        return "It is the weighted average of the means [00:10] — nothing more.", "fake-model"
    monkeypatch.setattr(llm, "generate", answer)
    res = api.post(f"/session/{key}/ask", json={"question": "What is the expected return?"}).json()
    assert "What is the expected return?" in seen["prompt"] and "weighted average of the means" in seen["prompt"]
    assert "[00:10](https://www.udemy.com/course/q/learn/lecture/7986740#t=10)" in res["answer"]
    assert "—" not in res["answer"]
    assert api.post(f"/session/{key}/ask", json={"question": " "}).status_code == 422


def test_flashcards_are_kept_for_the_quiz_and_marked_stale_after_a_rewrite(client, monkeypatch):
    api, _ = client
    key = api.post("/session", json=META).json()["key"]
    api.post(f"/session/{key}/captions", json={"cues": [{"start": 0, "end": 5, "text": "Today, expected return."}]})
    assert api.post(f"/session/{key}/cards").status_code == 404  # no note yet
    api.post(f"/session/{key}/compose")
    assert _wait_done(api, key)["state"] == "done"
    monkeypatch.setattr(llm, "generate", lambda prompt, **kw: (
        "Q: What is a portfolio's expected return?\nA: The weighted average of its assets' returns.\n---\n"
        "Q: Why do weights sum to one?\nA: Together they are all of your money.", "fake-model"))
    made = api.post(f"/session/{key}/cards").json()
    assert len(made["cards"]) == 2 and made["cards"][0]["front"].startswith("What is") and not made["stale"]
    assert made["tsv"].count("\n") >= 1
    assert api.get(f"/session/{key}/cards").json()["cards"] == made["cards"]

    api.post(f"/session/{key}/compose")  # written again, word for word: the cards still fit
    assert _wait_done(api, key)["state"] == "done"
    assert api.get(f"/session/{key}/cards").json()["stale"] is False
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (
        CANNED.replace("blended smoothie", "fruit salad"), "fake-model"))
    api.post(f"/session/{key}/compose")  # written again, differently
    assert _wait_done(api, key)["state"] == "done"
    assert api.get(f"/session/{key}/cards").json()["stale"] is True
