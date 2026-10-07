"""Your own notes: typed, pasted and spoken while you watch or read, kept in
the lecture's note (or a reading note) and never lost to Margin's own writes."""
import base64
import io

from PIL import Image

from sidecar import compose as C
from sidecar import mine, sessions
from sidecar.tests.test_pipeline_api import CANNED, META, _compose_done, client  # noqa: F401

PAGE = {"platform": "page", "url": "https://dotnotes.in/subject/CN", "lecture_id": "dotnotes.in/subject/CN",
        "lecture_title": "Computer Networks | Dotnotes", "author": "dotnotes.in"}


def _png() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "teal").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _entry(**kw):
    return {"id": "M001", "text": "", "quote": None, "where": None, "t": None, "images": [], "audio": None,
            "transcript": None, **kw}


# --- the block a note of yours becomes ------------------------------------------------

def test_a_lecture_note_of_yours_links_its_moment_and_embeds_what_you_added():
    block = mine.render(_entry(t=100, text="Variance is risk, squared.", quote="sigma squared",
                               images=["M001-1.png"], audio="M001.webm", transcript="variance is the spread"),
                        prefix="28", meta=META)
    assert block.startswith("%% mine M001 %%\n")
    assert "[01:40](https://www.udemy.com" in block and "Variance is risk, squared." in block
    assert "> sigma squared" in block
    assert "![[assets/28-M001-1.png|480]]" in block and "![[assets/28-M001.webm]]" in block
    assert "variance is the spread" in block


def test_a_reading_note_of_yours_says_where_in_the_page_it_was():
    block = mine.render(_entry(text="Two sub-layers.", where="Functionality of Data-link Layer"),
                        prefix="cn", meta=PAGE)
    assert "Functionality of Data-link Layer" in block and "Two sub-layers." in block
    assert "](" not in block  # no video to link to


# --- the "My notes" section ---------------------------------------------------------------

NOTE = "---\ntitle: L\n---\n# L\n\n## Risk [01:40]\nVariance.\n\n## Key terms\n- **Risk**: variance.\n"


def test_your_notes_go_in_a_section_at_the_end_once_each():
    one = mine.insert(NOTE, [mine.render(_entry(text="First."), "28", META)])
    assert one.rstrip().endswith("First.") and one.index("## Key terms") < one.index("## My notes")
    two = mine.insert(one, [mine.render(_entry(text="First."), "28", META),
                            mine.render(_entry(id="M002", text="Second."), "28", META)])
    assert two.count("%% mine M001 %%") == 1 and two.index("First.") < two.index("Second.")


def test_a_note_of_yours_you_changed_in_obsidian_is_never_rewritten():
    note = mine.insert(NOTE, [mine.render(_entry(text="First."), "28", META)])
    note = note.replace("First.", "First, which I fixed in Obsidian.")
    again = mine.insert(note, [mine.render(_entry(text="First."), "28", META),
                               mine.render(_entry(id="M002", text="Second."), "28", META)])
    assert "First, which I fixed in Obsidian." in again and "\nFirst.\n" not in again and "Second." in again


def test_deleting_one_removes_only_its_block():
    note = mine.insert(NOTE, [mine.render(_entry(text="First."), "28", META),
                              mine.render(_entry(id="M002", text="Second."), "28", META)])
    left = mine.remove(note, "M001")
    assert "First." not in left and "Second." in left and "## My notes" in left
    assert "## My notes" not in mine.remove(left, "M002")  # nothing of yours left: no empty section


def test_a_rewrite_of_the_note_carries_your_section_over():
    yours = mine.insert(NOTE, [mine.render(_entry(text="Mine, edited by hand."), "28", META)])
    rewritten = "---\ntitle: L\n---\n# L\n\n## Risk and return [01:40]\nAll new.\n"
    carried = mine.carry(yours, rewritten)
    assert "All new." in carried and "Mine, edited by hand." in carried
    assert carried.index("## Risk and return") < carried.index("## My notes")


def test_margins_updates_leave_your_section_alone_and_last():
    note = mine.insert(NOTE, [mine.render(_entry(text="Mine."), "28", META)])
    update = "## Diversification [03:00]\nMixing lowers risk.\n\n## My notes\nA model writing in your section.\n"
    merged = C.merge_update(note, update)
    assert "Mixing lowers risk." in merged and "A model writing in your section." not in merged
    assert merged.rstrip().endswith("Mine.")


# --- through the API: a page you read ---------------------------------------------------

def test_a_note_on_a_page_you_read_is_written_to_your_vault_at_once(client):  # noqa: F811
    api, vault = client
    key = api.post("/session", json=PAGE).json()["key"]
    res = api.post(f"/session/{key}/mine", json={"text": "Flow control stops a fast sender swamping the receiver.",
                                                 "quote": "Data link layer has two sub-layers",
                                                 "where": "Functionality of Data-link Layer", "images": [_png()]}).json()
    assert res["in_note"] is True
    entry_id = res["entry"]["id"]
    note = vault / "Reading" / "Computer Networks Dotnotes (dotnotes.in).md"
    text = note.read_text()
    assert "source: https://dotnotes.in/subject/CN" in text and "## My notes" in text
    assert "Flow control stops a fast sender" in text and "> Data link layer has two sub-layers" in text
    image = res["entry"]["images"][0]
    assert len(list((vault / "Reading" / "assets").glob(f"*-{entry_id}-1.png"))) == 1 and f"-{image}|480]]" in text
    assert api.get(f"/session/{key}").json()["edited"] is False      # still Margin's own file
    assert [e["id"] for e in api.get(f"/session/{key}/mine").json()["entries"]] == [entry_id]
    assert api.get(f"/session/{key}/mine/file/{image}").status_code == 200

    api.delete(f"/session/{key}/mine/{entry_id}")
    assert "Flow control" not in note.read_text() and not list((vault / "Reading" / "assets").glob(f"*-{entry_id}*"))
    assert api.get(f"/session/{key}/mine").json()["entries"] == []


def test_a_voice_note_is_kept_and_written_down(client, monkeypatch):  # noqa: F811
    api, vault = client
    monkeypatch.setattr("sidecar.asr.transcribe", lambda audio, **kw: [{"start": 0, "end": 2, "text": "check the window size"}])
    key = api.post("/session", json=PAGE).json()["key"]
    res = api.post(f"/session/{key}/mine", json={"audio": base64.b64encode(b"\x1aE\xdf\xa3" + b"0" * 4000).decode(),
                                                 "audio_mime": "audio/webm"}).json()
    assert res["entry"]["transcript"] == "check the window size"
    assert res["entry"]["audio"] == f"{res['entry']['id']}.webm"
    text = (vault / "Reading" / "Computer Networks Dotnotes (dotnotes.in).md").read_text()
    assert f"{res['entry']['id']}.webm]]" in text and "check the window size" in text


def test_a_voice_note_of_silence_is_kept_without_inventing_words(client, monkeypatch):  # noqa: F811
    api, vault = client
    monkeypatch.setattr("sidecar.asr.transcribe", lambda audio, **kw: [{"start": 0, "end": 2, "text": "[Music]"},
                                                                       {"start": 2, "end": 3, "text": "[BLANK_AUDIO]"}])
    key = api.post("/session", json=PAGE).json()["key"]
    res = api.post(f"/session/{key}/mine", json={"audio": base64.b64encode(b"0" * 4000).decode()}).json()
    assert res["entry"]["transcript"] is None and res["entry"]["audio"].endswith(".webm")
    assert "Music" not in (vault / "Reading" / "Computer Networks Dotnotes (dotnotes.in).md").read_text()


def test_two_pages_with_one_title_on_one_site_share_a_note_and_keep_both(client):  # noqa: F811
    # "Introduction" twice on one docs site: one note file, and neither page's
    # note may be taken for the other's (ids are unique, not M001 each).
    api, vault = client
    one = api.post("/session", json={**PAGE, "url": "https://dotnotes.in/a", "lecture_id": "dotnotes.in/a"}).json()["key"]
    two = api.post("/session", json={**PAGE, "url": "https://dotnotes.in/b", "lecture_id": "dotnotes.in/b"}).json()["key"]
    api.post(f"/session/{one}/mine", json={"text": "From page A."})
    api.post(f"/session/{two}/mine", json={"text": "From page B."})
    text = (vault / "Reading" / "Computer Networks Dotnotes (dotnotes.in).md").read_text()
    assert "From page A." in text and "From page B." in text


def test_an_empty_note_is_refused(client):  # noqa: F811
    api, _ = client
    key = api.post("/session", json=PAGE).json()["key"]
    assert api.post(f"/session/{key}/mine", json={"text": "  "}).status_code == 400


# --- through the API: a lecture ------------------------------------------------------------

def test_a_lecture_keeps_your_notes_through_its_first_note_its_updates_and_a_rewrite(client, monkeypatch):  # noqa: F811
    api, vault = client
    key = api.post("/session", json={**META, "lecture_id": "mine"}).json()["key"]
    sessions.load_session(key).add_asr_cues([{"start": 0, "end": 5, "text": "Today, expected return."}])
    api.post(f"/session/{key}/watched", json={"start": 0, "end": 60})

    # Before Margin has written the lecture's note, yours waits for it.
    early = api.post(f"/session/{key}/mine", json={"text": "Weights sum to one.", "t": 12}).json()
    assert early["in_note"] is False
    _compose_done(api, key)
    note = vault / "Quant Finance" / "28 - Expected return of the portfolio.md"
    assert "Weights sum to one." in note.read_text() and "[00:12](" in note.read_text()

    # Added once the note exists: straight in, and the note is still Margin's own.
    api.post(f"/session/{key}/mine", json={"text": "Ask about shorting.", "t": 50})
    assert "Ask about shorting." in note.read_text()
    assert api.get(f"/session/{key}").json()["edited"] is False

    # An update adds Margin's part and leaves yours.
    api.post(f"/session/{key}/watched", json={"start": 60, "end": 300})
    sessions.load_session(key).add_asr_cues([{"start": 100, "end": 110, "text": "Variance measures risk."}])
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (
        "<<<NOTE>>>\n## Risk [01:40]\nVariance measures risk.\n<<<CODE>>>\nNONE\n<<<END>>>", "fake-model"))
    _compose_done(api, key)
    text = note.read_text()
    assert "Variance measures risk." in text and text.rstrip().endswith("Ask about shorting.")

    # "Rewrite the notes" writes Margin's part again and carries yours over.
    monkeypatch.setattr(C, "generate", lambda prompt, pictures=None, **kw: (
        CANNED.replace("blended smoothie", "fruit salad"), "fake-model"))
    _compose_done(api, key, full=True)
    text = note.read_text()
    assert "fruit salad" in text and "Weights sum to one." in text and "Ask about shorting." in text
