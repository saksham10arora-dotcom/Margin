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
    # Linux and Windows have neither: Pillow's own font, at the size asked for.
    # Without the size it draws 10-pixel text, the slides come out nearly blank
    # and every slide looks like every other (why CI on Linux failed).
    return ImageFont.load_default(size)


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


def _patch_of_the_face():
    import base64 as b64
    bits = np.zeros((36, 64), dtype=np.uint8)
    bits[3:7, 57:61] = 1  # the browser saw only part of the head moving
    return unpack_moving(b64.b64encode(np.packbits(bits.ravel()).tobytes()).decode())


def test_a_head_moving_past_the_patch_the_browser_saw_is_still_one_slide(tmp_path):
    # Lecture 17: the mask covered 1% of the frame, the head moved over 8%,
    # and 100 captures were kept of about 20 screens.
    s = open_session(UDEMY, root=tmp_path)
    for i, t in enumerate(range(10, 60, 5)):
        s.add_frame(t, _two_panel("Closed-Source Frontier", ["GPT"], i), _patch_of_the_face())
    assert len(s.frames) == 1
    s.add_frame(70, _two_panel("Open-Source Frontier", ["Llama"], 99), _patch_of_the_face())
    assert len(s.frames) == 2  # a new slide is still a new slide


def test_clean_up_refolds_old_captures_without_reusing_ids(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    # An older Margin kept every one of these (today they fold as they arrive),
    # so they are written the way it stored them.
    (s.root / "frames").mkdir()
    for i, t in enumerate((10, 20, 30)):
        (s.root / "frames" / f"S00{i + 1}.jpg").write_bytes(_two_panel("Closed-Source Frontier", ["GPT"], i))
    s._write("frames.json", [{"id": f"S00{i + 1}", "t": t, "t_last": t, "file": f"S00{i + 1}.jpg", "w": 1280, "h": 720}
                             for i, t in enumerate((10, 20, 30))])
    assert len(s.frames) == 3
    np.save(s.root / "frames" / "moving.npy", _face_mask())
    report = s.compact_frames()
    assert (report["before"], report["after"]) == (3, 1) and len(s.frames) == 3  # a report changes nothing
    s.compact_frames(apply=True)
    # After the old ids, so a note's pictures in the vault are never replaced by another slide.
    assert [(f["id"], f["t"], f["t_last"]) for f in s.frames] == [("S004", 10, 30)]
    assert sorted(p.name for p in (s.root / "frames").glob("S*")) == ["S004.jpg", "S004.npy"]
    assert s.frame_bytes("S004")


# --- a hand writing on paper, filmed from above ---------------------------------

PAGE = ["Algorithm: a step by step process", "input: values from a set", "output: one for each input",
        "precision: every step is defined", "finiteness: stops after n steps"]
SKIN = (150, 110, 82)  # the writer's hand as the camera saw it on a real lecture


def _paper(lines, hand=None, shift=(0, 0)) -> bytes:
    """A page filmed from above: grey-white paper, pen-thin text in blue and
    red, and the writer's hand (with its pen) reaching in from the bottom edge."""
    img = Image.new("RGB", (1280, 720), (196, 194, 196))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((160 + shift[1], 80 + i * 100 + shift[0]), line,
               fill=(40, 70, 170) if i % 2 == 0 else (190, 40, 50), font=_font(44))
    if hand is not None:
        x, y = hand
        d.ellipse([x - 130, y - 100, x + 130, y + 420], fill=SKIN)
        d.line([x - 30, y - 90, x - 110, y - 220], fill=(30, 60, 160), width=12)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def test_a_page_written_by_hand_is_one_capture_whatever_the_hand_covers(tmp_path):
    # The hand counted as ink: every capture had some the next one lacked, and
    # hid writing the last one had, so a 10 minute lecture kept 47 captures.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _paper(PAGE[:2], hand=(700, 330)))
    s.add_frame(20, _paper(PAGE[:3], hand=(420, 430)))
    s.add_frame(30, _paper(PAGE, hand=(960, 560)))
    s.add_frame(35, _paper(PAGE))
    assert [(f["t"], f["t_last"]) for f in s.frames] == [(10, 35)]


def test_a_page_nudged_while_writing_is_still_the_same_page(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _paper(PAGE[:3]))
    s.add_frame(20, _paper(PAGE[:4], shift=(40, 24)))  # the paper slid a little, and a line was added
    assert len(s.frames) == 1
    assert s.frame_bytes(s.frames[0]["id"]) == _paper(PAGE[:4], shift=(40, 24))


def _filmed(lines, hand=None, exposure=0, seed=0) -> bytes:
    """A notebook filmed by a hand-held camera: ruled paper whose faint printed
    lines come and go with the exposure, uneven light, a desk at the edges,
    sensor noise, dark pen writing, and the writer's hand."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:720, 0:1280]
    paper = 205 + 25 * (x / 1280) - 15 * (y / 720) + exposure
    img = np.stack([paper, paper, paper + 6], axis=2)
    img[:, :140] = img[:, 1140:] = (96, 62, 46)  # the desk either side
    for row in range(60, 720, 34):
        # The ruling: faint blue, at the edge of what counts as a mark, so it
        # shows in patches, different patches in every capture.
        for x0 in range(140, 1140, 60):
            if rng.random() < 0.5:
                img[row:row + 4, x0:x0 + 60] -= (70 + exposure, 60 + exposure, 12)
    img += rng.normal(0, 7, img.shape)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((190, 52 + i * 68), line, fill=(25, 35, 90), font=_font(40))
    if hand is not None:
        hx, hy = hand
        d.ellipse([hx - 130, hy - 100, hx + 130, hy + 420], fill=SKIN)
        d.line([hx - 30, hy - 90, hx - 110, hy - 220], fill=(30, 60, 160), width=12)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


NOTEBOOK = ["Compiler: translates source to target code", "Phases: lexical, syntax, semantic analysis",
            "Intermediate code, code optimisation", "Code generation and the symbol table",
            "Lexical analysis: characters to tokens", "Syntax analysis: tokens to a parse tree"]


def test_a_filmed_notebook_page_is_one_capture_however_the_light_and_hand_change(tmp_path):
    # A real 41 minute notebook lecture kept a capture every few seconds of one
    # page: its faint printed ruling came and went with the camera's exposure,
    # and the sharp second look took each lost bit of ruling for a lost word.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _filmed(NOTEBOOK, hand=(700, 420), exposure=0, seed=1))
    s.add_frame(14, _filmed(NOTEBOOK, hand=(420, 470), exposure=-12, seed=2))
    s.add_frame(19, _filmed(NOTEBOOK, hand=(960, 520), exposure=10, seed=3))
    s.add_frame(25, _filmed(NOTEBOOK, exposure=-6, seed=4))
    assert len(s.frames) == 1


def test_two_filmed_notebook_pages_stay_two(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _filmed(NOTEBOOK, hand=(700, 420), seed=1))
    s.add_frame(40, _filmed(["Regular expressions and finite automata", "NFA to DFA by subset construction",
                             "Minimising a DFA", "Lex: a lexical analyser generator",
                             "Context free grammars, derivations", "Ambiguity and left recursion"],
                            hand=(520, 460), exposure=-8, seed=2))
    assert len(s.frames) == 2


def test_a_hand_that_roamed_the_whole_page_does_not_hide_every_page(tmp_path):
    # A 41 minute notebook lecture on a hand-held camera: the hand and pen had
    # moved over nearly every part of the page, the lecture remembered all of
    # it as "the presenter" and left it out of every comparison, so nothing
    # matched anything and 375 captures were kept.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _paper(PAGE[:3], hand=(700, 420)))
    seen = np.full((90, 160), 40, np.uint8)
    seen[:12, :40] = 0  # 97% of the frame, seen moving over and over
    np.save(s.root / "frames" / "presenter.npy", seen)
    s.add_frame(20, _paper(PAGE[:3], hand=(420, 430)))  # the same page, the hand elsewhere
    s.add_frame(40, _paper(["Properties of an algorithm", "input and output", "definiteness"], hand=(520, 460)))
    assert [f["t"] for f in s.frames] == [10, 40]


def test_two_pages_written_by_hand_stay_two(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _paper(PAGE[:3], hand=(700, 420)))
    s.add_frame(40, _paper(["Properties of an algorithm", "input and output", "definiteness"], hand=(520, 460)))
    s.add_frame(50, _paper(["Pseudo code", "flow chart", "programming language"], shift=(30, 10)))
    assert [f["t"] for f in s.frames] == [10, 40, 50]


def test_a_skin_toned_picture_does_not_hide_a_new_title(tmp_path):
    # What the hand rule must not do on slides: a photo of a person at the
    # frame's edge is left out of the comparison, the titles still count.
    def slide(title):
        img = Image.open(io.BytesIO(_slide(title, A[:1])))
        ImageDraw.Draw(img).ellipse([900, 380, 1300, 900], fill=SKIN)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, slide("Expected return"))
    s.add_frame(40, slide("Portfolio risk"))
    assert [f["t"] for f in s.frames] == [10, 40]


def test_of_two_captures_of_a_page_the_one_showing_more_writing_is_kept(tmp_path):
    # Around the hand and its pen nothing counts, so a capture whose newest
    # line is still under the pen folds into an older one: the picture kept
    # must be whichever shows more of the page, or the note loses that line.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _paper(PAGE, hand=(520, 300)))  # the hand over the middle of the page
    s.add_frame(20, _paper(PAGE))                   # the same page, nothing in the way
    s.add_frame(30, _paper(PAGE, hand=(700, 330)))  # the hand back: must not win
    assert len(s.frames) == 1
    assert s.frame_bytes(s.frames[0]["id"]) == _paper(PAGE)


def test_a_capture_hidden_by_what_is_left_out_is_part_of_nothing():
    # Only a blank screen is "contained in anything". A page that looks empty
    # because a hand (or a face bubble) covers it is not blank: two different
    # pages, each mostly under the hand, were folded together this way.
    from sidecar.sessions import covers, grey_thumb
    a = grey_thumb(Image.open(io.BytesIO(_slide("Expected return", A))))
    b = grey_thumb(Image.open(io.BytesIO(_slide("Portfolio risk", B))))
    assert covers(b, a, np.ones(a.shape[:2], dtype=bool)) is None


def test_a_capture_of_the_hand_over_a_blank_page_adds_nothing(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _paper(PAGE))
    assert s.add_frame(20, _paper([], hand=(640, 330)))["duplicate"]
    assert len(s.frames) == 1 and s.frame_bytes(s.frames[0]["id"]) == _paper(PAGE)


def test_a_page_mostly_under_the_hand_is_not_taken_over_by_the_next_page(tmp_path):
    # Two captures, each mostly hand, leave a few pixels of writing to compare,
    # and those few can line up by chance: page 2 took page 1's picture slot
    # and its time (0:25 on a page written at 5:06).
    s = open_session(UDEMY, root=tmp_path)
    first = _paper(["Algorithm"], hand=(420, 300))
    s.add_frame(25, first)
    s.add_frame(306, _paper(["Properties", "input: values"], hand=(560, 300)))
    assert s.frame_bytes(s.frames[0]["id"]) == first and s.frames[0]["t"] == 25


def test_a_longer_title_does_not_swallow_a_shorter_one(tmp_path):
    # In the small picture a line of text is a bar, and a longer line covers a
    # shorter one's bar whatever it says: "Risk" was folded into the next slide.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Risk", []))
    s.add_frame(40, _slide("Returns and more", A[:1]))
    assert [f["t"] for f in s.frames] == [10, 40]


def test_titles_of_the_same_length_are_still_different_slides(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _slide("Risk", A))
    s.add_frame(40, _slide("Rain", A))
    assert [f["t"] for f in s.frames] == [10, 40]


# --- a teacher in front of a projected slide --------------------------------------

BOARD = ["An algorithm is a finite sequence", "of well-defined instructions", "to solve a class of problems"]


def _board(lines, person=None, marks=0, picture=False) -> bytes:
    """A dark smart-board slide with light text, red annotation underlines, and
    the teacher (lit face and hands, dark shirt) standing in front of it."""
    img = Image.new("RGB", (1280, 720), (22, 30, 40))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((90, 120 + i * 90), line, fill=(225, 230, 235), font=_font(48))
    for i in range(marks):
        d.line([90, 180 + i * 90, 700, 182 + i * 90], fill=(220, 40, 40), width=5)
    if picture:
        d.rectangle([300, 420, 760, 660], fill=(60, 120, 220))
    if person is not None:
        x = person
        d.rectangle([x - 150, 330, x + 150, 720], fill=(30, 45, 95))      # shirt, to the bottom edge
        d.ellipse([x - 70, 150, x + 70, 330], fill=(205, 150, 120))       # face
        d.ellipse([x - 230, 420, x - 150, 500], fill=(205, 150, 120))     # a hand
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _filmed_board(lines, person=None, seed=0) -> bytes:
    """The smart board as a classroom camera films it: grain and uneven light."""
    rng = np.random.default_rng(seed)
    img = np.asarray(Image.open(io.BytesIO(_board(lines, person=person))), dtype=np.float32)
    img += np.linspace(-12, 12, 1280)[None, :, None] + rng.normal(0, 6, img.shape)
    buf = io.BytesIO()
    Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_two_filmed_slides_with_the_teacher_in_front_stay_two(tmp_path):
    # Judging filmed paper by its dark writing merged "Need for Analysis" into
    # "Types of Analysis" in a classroom recording: what differed between the
    # slides had been left out as someone standing in front.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _filmed_board(["Need for Analysis", "We do analysis of algorithms", "to compare them"], person=1000, seed=1))
    s.add_frame(60, _filmed_board(["Types of Analysis", "Experimental or relative", "Apriori or absolute"], person=760, seed=2))
    assert len(s.frames) == 2


def test_a_teacher_walking_in_front_of_a_slide_is_still_one_slide(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _board(BOARD, person=1100))
    s.add_frame(20, _board(BOARD, person=820))
    s.add_frame(30, _board(BOARD, person=1150, marks=1))  # and underlined a line
    s.add_frame(40, _board(BOARD, marks=1))
    assert len(s.frames) == 1
    assert s.frame_bytes(s.frames[0]["id"]) == _board(BOARD, marks=1)


def test_a_new_slide_behind_the_teacher_is_a_new_slide(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _board(BOARD, person=1100))
    s.add_frame(40, _board(["Will accept zero or more input", "but generate at least one output"], person=1080))
    assert [f["t"] for f in s.frames] == [10, 40]


def test_a_picture_revealed_on_a_slide_is_in_the_picture_kept(tmp_path):
    # Thick shapes count less than strokes when slides are compared (a person,
    # a shadow), but a picture that appears is content: the slide's picture
    # must be the one that shows it.
    s = open_session(UDEMY, root=tmp_path)
    s.add_frame(10, _board(BOARD))
    s.add_frame(20, _board(BOARD, picture=True))
    assert len(s.frames) == 1
    assert s.frame_bytes(s.frames[0]["id"]) == _board(BOARD, picture=True)


# --- a nine-hour lecture -------------------------------------------------------------

def _long_lecture(tmp_path, hours=9):
    s = open_session(UDEMY, root=tmp_path)
    cues = [{"start": t, "end": t + 9, "text": f"point {t} " + "word " * 8} for t in range(0, hours * 3600, 10)]
    s.set_caption_cues(cues, "youtube-captions")
    return s


def test_a_short_lecture_goes_to_the_note_whole(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.set_caption_cues([{"start": 0, "end": 5, "text": "Expected return."}], "youtube-captions")
    assert s.transcript_for_note(1000) == s.transcript_text()


def test_a_long_lecture_keeps_what_you_watched_whole_and_thins_the_rest_evenly(tmp_path):
    # A nine-hour one-shot is far over what a note is written from. It used to
    # keep the first and last hour and drop the seven between, whatever you had
    # watched; the note jumped from chapter 1 to the last chapters.
    s = _long_lecture(tmp_path)
    s.mark_watched(4 * 3600, 4 * 3600 + 1800)  # half an hour in the middle
    text = s.transcript_for_note(120_000)
    assert len(text) <= 120_000 + 2000
    assert all(f"point {t} " in text for t in range(4 * 3600 + 10, 4 * 3600 + 1790, 10))  # watched: all of it
    for hour in (0, 2, 6, 8):  # the rest: stretches from across the whole lecture
        assert any(f"point {t} " in text for t in range(hour * 3600, (hour + 1) * 3600, 10))
    assert "left out" in text


def test_watching_more_of_a_long_lecture_makes_its_note_out_of_date(tmp_path):
    s = _long_lecture(tmp_path)
    s.mark_watched(0, 600)
    s.mark_composed(s.material_rev)
    assert not s.stale
    s.mark_watched(3 * 3600, 3 * 3600 + 300)  # a new part: the note can now say more
    assert s.stale


def test_watching_more_of_a_short_lecture_changes_nothing(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.set_caption_cues([{"start": t, "end": t + 9, "text": "word " * 5} for t in range(0, 600, 10)], "youtube-captions")
    s.mark_watched(0, 120)
    s.mark_composed(s.material_rev)
    s.mark_watched(120, 500)  # the note was written from all of it already
    assert not s.stale


# --- what is new since the note was written ----------------------------------------

def test_the_lecture_knows_what_is_new_since_its_note(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_asr_cues([{"start": 10, "end": 19, "text": "heard by Margin"}])  # no captions: new minutes are new speech
    s.mark_watched(0, 600)
    first = s.add_frame(100, _slide("Expected return", A))
    s.mark_composed(s.material_rev)
    assert s.new_since_note() == ([], [])
    s.mark_watched(500, 900)  # rewatched a bit, then watched on
    new = s.add_frame(700, _slide("Portfolio risk", B))
    ranges, frames = s.new_since_note()
    assert ranges == [[600, 900]]
    assert [f["id"] for f in frames] == [new["id"]] and new["id"] != first["id"]


def test_the_note_is_out_of_date_exactly_when_it_lacks_something(tmp_path):
    # "Out of date" was a counter of its own and could disagree with what the
    # note covers: a note missing three minutes and two slides said up to date.
    s = _long_lecture(tmp_path)
    s.mark_watched(0, 600)
    s.mark_composed(s.material_rev, watched=[[0, 300]])  # written from the first five minutes only
    assert s.stale


def test_watching_more_of_a_short_captioned_lecture_adds_only_its_slides(tmp_path):
    # Its note was written from all its captions: newly watched minutes are
    # already in it, and sending them again only invites repetition.
    s = open_session(UDEMY, root=tmp_path)
    s.set_caption_cues([{"start": t, "end": t + 9, "text": "word " * 5} for t in range(0, 600, 10)], "udemy-captions")
    s.mark_watched(0, 120)
    s.mark_composed(s.material_rev)
    s.mark_watched(120, 500)
    assert s.new_since_note() == ([], []) and not s.stale
    new = s.add_frame(300, _slide("Portfolio risk", B))
    ranges, frames = s.new_since_note()
    assert ranges == [] and [f["id"] for f in frames] == [new["id"]] and s.stale


def test_watching_more_of_a_lecture_without_captions_adds_what_was_heard(tmp_path):
    s = open_session(UDEMY, root=tmp_path)
    s.add_asr_cues([{"start": 0, "end": 9, "text": "first part"}])
    s.mark_watched(0, 120)
    s.mark_composed(s.material_rev)
    s.mark_watched(120, 400)
    s.add_asr_cues([{"start": 130, "end": 139, "text": "second part"}])
    assert s.new_since_note()[0] == [[120, 400]] and s.stale
