from sidecar import compose as C
from sidecar.sessions import open_session

META = {
    "platform": "udemy", "course_id": "1", "course_title": "Quant", "section_title": "MPT",
    "lecture_id": "28", "lecture_title": "Expected return", "lecture_index": 28,
    "url": "https://www.udemy.com/course/q/learn/lecture/28",
}

RESPONSE = """<<<NOTE>>>
# Should be dropped
> [!abstract] In one breath
> Averages matter.

## Weighted average [01:10]
The mean is $\\mu$.
{{slide:S001}}
{{slide:S999}}

## In code
Computes it.

## Watch out
Nothing.
<<<GIST>>>
"Portfolio return is the weighted average of asset returns."
<<<CODE>>>
```python
# %% [markdown]
# ## 28 · Expected return
# *Illustration by Margin, not shown in the lecture.*

# %%
import numpy as np
print(np.dot([0.5, 0.5], [0.1, 0.2]))
```
<<<END>>>
"""


def test_parse_response_splits_all_three_blocks():
    note, gist, cells = C.parse_response(RESPONSE)
    assert note.startswith("> [!abstract]")  # stray H1 removed
    assert gist == "Portfolio return is the weighted average of asset returns."
    assert [c["type"] for c in cells] == ["markdown", "code"]
    assert cells[0]["source"].startswith("## 28 · Expected return")
    assert "np.dot" in cells[1]["source"]


def test_a_response_with_no_markers_is_still_a_note():
    note, gist, cells = C.parse_response("## Just a note\nbody")
    assert "Just a note" in note and gist == "" and cells == []


def test_code_none_means_no_cells():
    assert C.parse_percent_cells("NONE") == []
    assert C.parse_percent_cells("```\nNONE\n```") == []


def test_code_before_any_marker_becomes_a_code_cell():
    cells = C.parse_percent_cells("import os\n# %% [markdown]\n# hi")
    assert cells == [{"type": "code", "source": "import os"}, {"type": "markdown", "source": "hi"}]


def test_timestamps_become_links_per_platform():
    yt = {"platform": "youtube", "url": "https://www.youtube.com/watch?v=abc&t=10s"}
    assert C.link_timestamps("## A [01:10]", yt) == "## A [01:10](https://www.youtube.com/watch?v=abc&t=70s)"
    udemy = C.link_timestamps("[1:02:03]", META)
    assert udemy == "[1:02:03](https://www.udemy.com/course/q/learn/lecture/28#t=3723)"
    # Existing links are left alone.
    assert C.link_timestamps("[01:10](x)", yt) == "[01:10](x)"


def test_place_slides_copies_known_and_drops_unknown(tmp_path):
    session = open_session(META, root=tmp_path / "s")
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), "white").save(buf, "JPEG")
    session.add_frame(5, buf.getvalue())
    vault = tmp_path / "vault"
    body, written = C.place_slides("a\n{{slide:S001}}\nb {{slide:S999}}", session, vault, META)
    assert written == ["28-S001.jpg"]
    assert "![[assets/28-S001.jpg|720]]" in body and "S999" not in body
    assert (vault / "Quant" / "assets" / "28-S001.jpg").exists()


def test_figures_land_inside_in_code_section():
    note = "## In code\nComputes it.\n\n## Watch out\nx"
    out = C.add_figures(note, ["28-fig1.png"], "Quant.ipynb")
    before, after = out.split("## Watch out")
    assert "![[assets/28-fig1.png|640]]" in before and "Quant.ipynb" in before
    assert "fig1" not in after


def test_figures_create_the_section_if_the_model_forgot_it():
    out = C.add_figures("## Idea\nx", ["f.png"], None)
    assert out.rstrip().endswith("![[assets/f.png|640]]")


def test_select_slides_spreads_over_the_whole_lecture():
    frames = [{"id": f"S{i:03d}", "t": i} for i in range(100)]
    picked = C.select_slides(frames, limit=10)
    assert len(picked) == 10 and picked[0]["t"] == 0 and picked[-1]["t"] >= 90


def test_prompt_warns_about_speech_recognition_errors():
    prompt = C.build_prompt(META, "[00:00] hello", "asr", [], None)
    assert "mis-heard" in prompt
    assert "{{slide:S003}}" in prompt  # the placeholder syntax survives the f-string
    assert "## 28 · Expected return" in prompt
    assert "em dashes" in prompt


def test_prompt_without_transcript_says_so():
    prompt = C.build_prompt(META, "", None, [{"id": "S001", "t": 3}], "Previously: returns")
    assert "NO transcript" in prompt and "S001 at 00:03" in prompt
    assert "Previously: returns" in prompt


def test_previous_note_is_the_nearest_lower_lecture(tmp_path):
    folder = tmp_path / "Quant"
    folder.mkdir()
    for name in ["00 - Quant.md", "26 - A.md", "27 - B.md", "29 - C.md"]:
        (folder / name).write_text("---\ngist: g\n---\n")
    assert C.previous_note(tmp_path, META).name == "27 - B.md"


def test_em_dashes_are_removed_from_prose_but_not_code_or_math():
    note = "Risk depends on covariance\u2014how assets move.\n```python\nx = 'a\u2014b'\n```\n$a \u2014 b$"
    out = C.remove_em_dashes(note)
    assert out.startswith("Risk depends on covariance, how assets move.")
    assert "x = 'a\u2014b'" in out and "$a \u2014 b$" in out


def test_code_heading_has_no_dangling_separator_without_a_lecture_number():
    prompt = C.build_prompt({**META, "lecture_index": None}, "x", "captions", [], None)
    assert "`## Expected return`" in prompt and "`##  ·" not in prompt
    assert "`## 28 · Expected return`" in C.build_prompt(META, "x", "captions", [], None)


def test_the_prompt_forbids_invented_content_and_filler_code():
    prompt = C.build_prompt(META, "[00:00] welcome back", "captions", [], None)
    assert "Faithful first" in prompt
    assert "`## Worked example (by Margin)`" in prompt  # made-up numbers are labelled
    assert "Never invent code to fill this block" in prompt
    assert "# margin: run it yourself" in prompt


def test_an_in_code_section_with_no_code_is_dropped():
    note = "## The idea\nx\n\n## In code\nThe notebook plots it.\n\n## Watch out\ny\n"
    assert C.drop_section(note, "In code") == "## The idea\nx\n\n## Watch out\ny\n"
    assert C.drop_section("## The idea\nx\n", "In code") == "## The idea\nx\n"


def test_code_left_for_you_is_said_so_in_the_note():
    note = C.add_figures("## In code\nCalls the API.\n\n## Watch out\ny", [], "Course.ipynb",
                         extra="Margin did not run it: it calls the OpenAI API. Run it yourself.")
    assert "Run it yourself." in note.split("## Watch out")[0]


def test_a_missing_in_code_section_goes_before_the_closing_sections():
    note = "## Topic\nx\n\n## Watch out\ny\n\n## Key terms\nz\n"
    out = C.add_figures(note, [], "Course.ipynb")
    assert out.index("## In code") < out.index("## Watch out")


def test_platform_resources_reach_the_prompt():
    meta = {**META, "resources": [{"title": "Slides", "url": None, "kind": "file"}],
            "course_links": [{"title": "Github link", "url": "https://github.com/ed-donner/llm_engineering"}]}
    prompt = C.build_prompt(meta, "[00:00] open the repo", "captions", [], None)
    assert "- Slides (a file under this lecture's Resources)" in prompt
    assert "- Github link: https://github.com/ed-donner/llm_engineering (listed for the course)" in prompt
    assert "Resources the platform lists" not in C.build_prompt(META, "x", "captions", [], None)


def test_gists_start_with_the_subject():
    assert C.tidy_gist("This lecture introduces the three pillars of LLM engineering.") == \
        "The three pillars of LLM engineering."
    assert C.tidy_gist("This lecture outlines the eight-week curriculum.") == "The eight-week curriculum."
    assert C.tidy_gist("In this video we build a chatbot.") == "We build a chatbot."
    assert C.tidy_gist("Expected return is a weighted average.") == "Expected return is a weighted average."
    assert C.tidy_gist("This lecture") == "This lecture"  # nothing left: keep it


def test_prose_in_a_code_cell_becomes_markdown():
    text = ("# %%\n## 17 · Chat Completions API\nThis section shows raw HTTP, then the client.\n\n"
            "# margin: run it yourself\n\n# %%\nimport requests\nprint(1)\n\n# %%\n%pip install openai\n")
    cells = C.parse_percent_cells(text)
    assert [c["type"] for c in cells] == ["markdown", "code", "code"]
    assert cells[0]["source"] == "## 17 · Chat Completions API\nThis section shows raw HTTP, then the client."
    assert cells[2]["source"] == "%pip install openai"  # magics are code, not prose


def test_markers_written_other_ways_still_split_the_blocks():
    raw = ("## The idea  \nChat‑Completions API.\n\n---\n\n**GIST**\nThe Chat‑Completions API.\n\n---\n\n"
           "**CODE**\n```python\n# %%\nprint(1)\n```\n")
    note, gist, cells = C.parse_response(raw)
    assert note == "## The idea\nChat-Completions API."
    assert gist == "The Chat-Completions API."
    assert cells == [{"type": "code", "source": "print(1)"}]


def test_a_code_topic_heading_is_not_a_marker():
    raw = "<<<NOTE>>>\n## Code\nWe write code.\n<<<GIST>>>\nCode.\n<<<CODE>>>\nNONE\n<<<END>>>"
    note, gist, cells = C.parse_response(raw)
    assert note == "## Code\nWe write code." and gist == "Code." and cells == []


def test_a_note_with_no_markers_is_all_note():
    note, gist, cells = C.parse_response("## Topic\nThe NOTE on this is short.\nEND")
    assert note.startswith("## Topic") and gist == "" and cells == []
