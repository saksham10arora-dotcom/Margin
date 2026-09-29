import io
import numpy as np
from pathlib import Path

from PIL import Image, ImageDraw

from sidecar import library
from sidecar.sessions import format_ts, load_session, merge_ranges, open_session, unpack_moving

UDEMY = {
    "platform": "udemy", "course_id": "1350350",
    "course_title": "Quantitative Finance & Algorithmic Trading in Python",
    "section_title": "Modern Portfolio Theory", "section_index": 6,
    "lecture_id": "7986740", "lecture_title": "Expected return of the portfolio",
    "lecture_index": 28, "duration_sec": 336,
    "url": "https://www.udemy.com/course/x/learn/lecture/7986740",
}


# --- library -------------------------------------------------------------------

def test_names_stay_readable_but_safe():
    assert library.clean_name("Risk: the #1 issue [part 2] / why?") == "Risk the 1 issue part 2 why"
    assert library.clean_name("   ") == "Untitled"


def test_long_names_are_cut_on_a_word_boundary():
    name = library.clean_name("word " * 40, limit=30)
    assert len(name) <= 30 and not name.endswith(" ")


def test_course_layout(tmp_path):
    assert library.course_dir(tmp_path, UDEMY).name == "Quantitative Finance & Algorithmic Trading in Python"
    assert library.lecture_note_path(tmp_path, UDEMY).name == "28 - Expected return of the portfolio.md"
    nb = library.notebook_path(tmp_path, UDEMY)
    assert nb.name == "Quantitative Finance & Algorithmic Trading in Python.ipynb"
    assert library.index_note_path(tmp_path, UDEMY).name.startswith("00 - ")
    assert library.asset_prefix(UDEMY) == "28"


def test_a_video_with_no_course_is_filed_under_its_platform(tmp_path):
    meta = {"platform": "youtube", "lecture_title": "Essence of calculus"}
    assert library.course_dir(tmp_path, meta).name == "YouTube"
    assert library.lecture_note_path(tmp_path, meta).name == "Essence of calculus.md"


def test_obsidian_uri_finds_the_real_vault_root(tmp_path):
    (tmp_path / "vault" / ".obsidian").mkdir(parents=True)
    note = tmp_path / "vault" / "notesyt" / "Course" / "01 - Intro.md"
    note.parent.mkdir(parents=True)
    note.write_text("x")
    uri = library.obsidian_uri(note)
    assert uri == "obsidian://open?vault=vault&file=notesyt/Course/01%20-%20Intro"


def test_obsidian_uri_is_none_outside_a_vault(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("x")
    assert library.obsidian_uri(note) is None


# --- sessions ------------------------------------------------------------------

def test_session_resumes_across_opens(tmp_path):
    s1 = open_session(UDEMY, root=tmp_path)
    s1.set_caption_cues([{"start": 1, "end": 2, "text": "hello"}], "captions")
    s2 = open_session({**UDEMY, "lecture_title": None}, root=tmp_path)
    assert s2.key == s1.key
    assert s2.cues[0]["text"] == "hello"
    # An empty field in a later sighting never erases a known one.
    assert s2.meta["lecture_title"] == "Expected return of the portfolio"
    assert load_session(s1.key, root=tmp_path) is not None


def test_load_session_rejects_path_tricks(tmp_path):
    assert load_session("../etc", root=tmp_path) is None


def test_captions_drop_sound_tags_and_sort(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    n = s.set_caption_cues([
        {"start": 5, "end": 6, "text": "second"},
        {"start": 1, "end": 2, "text": "[Music]"},
        {"start": 0, "end": 1, "text": "  first   line "},
    ], "captions")
    assert n == 2
    assert [c["text"] for c in s.cues] == ["first line", "second"]


def test_audio_transcription_replaces_a_rewatched_span(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_asr_cues([{"start": 0, "end": 5, "text": "old take"}, {"start": 10, "end": 12, "text": "keep"}])
    s.add_asr_cues([{"start": 0, "end": 6, "text": "new take"}])
    assert [c["text"] for c in s.cues] == ["new take", "keep"]


def test_audio_never_overrides_real_captions(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.set_caption_cues([{"start": 0, "end": 5, "text": "caption"}], "captions")
    assert s.add_asr_cues([{"start": 0, "end": 5, "text": "asr"}]) == 0
    assert s.cues[0]["text"] == "caption"


def test_transcript_text_places_sparse_timestamps(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.set_caption_cues([{"start": t, "end": t + 1, "text": f"w{t}"} for t in range(0, 100, 10)], "captions")
    text = s.transcript_text(every=30)
    assert text.count("[") == 4  # 0, 30, 60, 90
    assert "[01:30]" in text


def _font(size):
    from PIL import ImageFont
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _slide(title: str, bullets: list[str]) -> bytes:
    """A white slide from one template: coloured bar, title, bullet text."""
    img = Image.new("RGB", (1280, 720), (250, 250, 247))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1280, 8], fill=(214, 72, 98))
    d.text((80, 70), title, fill=(30, 60, 120), font=_font(56))
    for i, bullet in enumerate(bullets):
        d.ellipse([90, 216 + i * 110, 104, 230 + i * 110], fill=(214, 72, 98))
        d.text((130, 200 + i * 110), bullet, fill=(40, 40, 40), font=_font(40))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


A = ["A portfolio splits money across assets", "Each asset has an expected return",
     "The return is their weighted average"]
B = ["mu_p = w1*mu1 + w2*mu2", "Weights sum to one", "60% at 8% and 40% at 12% gives 9.6%"]


def test_a_slide_building_up_is_stored_once_with_the_fullest_image(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    first = s.add_frame(10, _slide("Expected return", A[:1]))
    s.add_frame(18, _slide("Expected return", A[:2]))
    last = s.add_frame(25, _slide("Expected return", A))
    assert last["duplicate"] and last["id"] == first["id"]
    frames = s.frames
    assert len(frames) == 1
    assert frames[0]["t"] == 10 and frames[0]["t_last"] == 25
    assert s.frame_bytes(first["id"]) == _slide("Expected return", A)


def test_different_slides_from_one_template_stay_separate(tmp_path):
    # The regression that matters: v2's first detector merged these, because
    # white slides with thin text look alike to an average or a phash.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Expected return", A))
    s.add_frame(40, _slide("The formula", B))
    s.add_frame(70, _slide("Risk", ["Variance depends on covariance"]))
    assert [f["t"] for f in s.frames] == [10, 40, 70]


def test_seeking_back_into_a_build_does_not_add_a_partial_copy(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Expected return", A))
    again = s.add_frame(12, _slide("Expected return", A[:1]))
    assert again["duplicate"]
    assert len(s.frames) == 1
    assert s.frame_bytes("S001") == _slide("Expected return", A)


def test_an_empty_frame_is_never_kept_on_its_own(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Expected return", A))
    buf = io.BytesIO()
    Image.new("RGB", (1280, 720), (250, 250, 247)).save(buf, "JPEG")
    assert s.add_frame(11, buf.getvalue())["duplicate"]
    assert len(s.frames) == 1


def test_watched_ranges_merge_and_give_coverage(tmp_path):
    s = open_session({**UDEMY, "duration_sec": 100}, root=tmp_path)
    s.mark_watched(0, 30)
    s.mark_watched(31, 50)   # 1s gap bridged
    s.mark_watched(80, 100)
    assert s.watched == [[0, 50], [80, 100]]
    assert s.coverage() == 0.7


def test_merge_ranges_ignores_empty_and_backwards():
    assert merge_ranges([[5, 5], [9, 3], [0, 1]]) == [[0, 1]]


def test_format_ts():
    assert format_ts(252) == "04:12"
    assert format_ts(3725) == "01:02:05"


# --- rewatching: new material marks the note for a rewrite, repeats do not ---

def test_rewatching_the_same_slides_adds_nothing_new(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Expected return", A))
    s.add_frame(40, _slide("The formula", B))
    s.mark_composed(s.material_rev)
    assert not s.stale
    # Second watch: the same slides again, and a build step seen out of order.
    s.add_frame(11, _slide("Expected return", A))
    s.add_frame(12, _slide("Expected return", A[:1]))
    s.add_frame(41, _slide("The formula", B))
    assert not s.stale
    assert len(s.frames) == 2


def test_a_slide_missed_the_first_time_marks_the_note_stale(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Expected return", A))
    s.mark_composed(s.material_rev)
    s.add_frame(70, _slide("Risk", ["Variance depends on covariance"]))
    assert s.stale
    assert [f["t"] for f in s.frames] == [10, 70]
    assert s.summary()["stale"] is True


def test_a_fuller_build_of_a_kept_slide_marks_the_note_stale(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Expected return", A[:1]))
    s.mark_composed(s.material_rev)
    s.add_frame(20, _slide("Expected return", A))
    assert s.stale and len(s.frames) == 1


def test_captions_posted_again_unchanged_are_not_new(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    cues = [{"start": 0, "end": 4, "text": "Today, expected return."}]
    s.set_caption_cues(cues, "udemy-captions")
    s.mark_composed(s.material_rev)
    s.set_caption_cues(cues, "udemy-captions")  # every time the lecture opens
    assert not s.stale


def test_speech_from_a_part_not_heard_before_marks_the_note_stale(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_asr_cues([{"start": 0, "end": 30, "text": "The first half."}])
    s.mark_composed(s.material_rev)
    s.add_asr_cues([{"start": 0, "end": 30, "text": "The first half, heard again."}])
    assert not s.stale
    s.add_asr_cues([{"start": 30, "end": 60, "text": "The part skipped last time."}])
    assert s.stale
    assert [c["start"] for c in s.cues] == [0, 30]


def test_summary_carries_the_whole_watch_history(tmp_path):
    s = open_session({**UDEMY, "duration_sec": 100}, root=tmp_path)
    s.mark_watched(50, 100)  # started from the middle
    s.mark_watched(0, 50)    # came back for the start after a reload
    assert s.summary()["watched"] == [[0, 100]]
    assert s.coverage() == 1.0


# --- lecture 15's layout: a fixed illustration, a face bubble, changing text ---

def _two_panel(title, bullets, face_seed):
    """Orange text panel left, a busy teal illustration right that stays the
    same across slides, and the lecturer's face in a bubble top right."""
    import random
    img = Image.new("RGB", (1280, 720), (240, 110, 40))
    d = ImageDraw.Draw(img)
    d.rectangle([640, 0, 1280, 720], fill=(40, 110, 110))
    rnd = random.Random(7)  # the same illustration on every slide
    for _ in range(60):
        x, y = rnd.randrange(660, 1240), rnd.randrange(20, 700)
        d.rectangle([x, y, x + rnd.randrange(20, 120), y + rnd.randrange(10, 60)],
                    fill=(rnd.randrange(150, 255), rnd.randrange(80, 200), rnd.randrange(30, 120)))
    d.text((60, 40), title, fill=(255, 255, 255), font=_font(48))
    for i, b in enumerate(bullets):
        d.text((70, 180 + i * 80), b, fill=(255, 255, 255), font=_font(34))
    face = random.Random(face_seed)  # a talking head: different every capture
    for _ in range(400):
        x, y = face.randrange(1100, 1250), face.randrange(10, 220)
        d.point((x, y), fill=(face.randrange(256), face.randrange(256), face.randrange(256)))
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(face.randrange(256), 90, 80))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _face_mask():
    import base64 as b64
    bits = np.zeros((36, 64), dtype=np.uint8)
    bits[0:13, 54:64] = 1  # where the bubble is, as the browser learns it
    return unpack_moving(b64.b64encode(np.packbits(bits.ravel()).tobytes()).decode())


def test_new_titles_on_an_illustrated_template_are_new_slides(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    mask = _face_mask()
    s.add_frame(10, _two_panel("Closed-Source Frontier", [], 1), mask)
    s.add_frame(60, _two_panel("Open-Source Frontier", [], 2), mask)
    s.add_frame(120, _two_panel("Frontier Labs", [], 3), mask)
    assert len(s.frames) == 3  # it used to come out as one


def test_the_same_slide_with_the_face_moving_is_one_slide_and_builds_up(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    mask = _face_mask()
    s.add_frame(10, _two_panel("Closed-Source Frontier", ["GPT"], 1), mask)
    s.add_frame(14, _two_panel("Closed-Source Frontier", ["GPT"], 2), mask)            # only the face moved
    s.add_frame(20, _two_panel("Closed-Source Frontier", ["GPT", "Claude", "Gemini"], 3), mask)  # a build step
    assert len(s.frames) == 1
    assert s.frames[0]["t"] == 10 and s.frames[0]["t_last"] == 20
    # The mask is remembered for captures sent without one.
    s.add_frame(25, _two_panel("Closed-Source Frontier", ["GPT", "Claude", "Gemini"], 4))
    assert len(s.frames) == 1
