"""Turn one captured lecture into a note, a notebook section and a gist.

This is the step v1 never really had. v1 wrote 60 seconds at a time and then,
on the one engine that supported it, regenerated the whole thing from a
re-downloaded transcript. Here the whole lecture is composed once, from
everything the browser captured: the full transcript in video time, and the
slides as they actually looked when the lecturer finished building them.

The prompt asks for three artifacts in one response, separated by markers
that are trivial to split and hard for a model to produce by accident:

    <<<NOTE>>>      the markdown body (no frontmatter, no H1: added here)
    <<<GIST>>>      one sentence, for the course index
    <<<CODE>>>      notebook cells in jupytext percent format, or NONE
    <<<END>>>

Percent format (`# %%` / `# %% [markdown]`) because every model has seen a
great deal of it, and it survives being written as plain text in a way JSON
full of escaped newlines does not.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from sidecar import library, repo_code
from sidecar.llm import Picture, generate
from sidecar.sessions import Session, format_ts

MAX_SLIDES = 24
MAX_TRANSCRIPT_CHARS = 120_000  # ~2h of speech; longer lectures are trimmed evenly


@dataclass
class Composition:
    note_body: str
    gist: str
    cells: list[dict] = field(default_factory=list)  # [{"type": "code"|"markdown", "source": str}]
    engine: str = ""
    slides_used: list[str] = field(default_factory=list)
    code_source: str | None = None  # the course repo notebook the code came from
    engine_chosen: bool = False     # written by the model picked in the menu


# --- slide selection ---------------------------------------------------------

def select_slides(frames: list[dict], limit: int = MAX_SLIDES) -> list[dict]:
    """At most `limit` slides, spread across the lecture rather than the first
    `limit`, so a long lecture's second half is not composed blind."""
    if len(frames) <= limit:
        return frames
    step = len(frames) / limit
    return [frames[int(i * step)] for i in range(limit)]


# --- prompt ------------------------------------------------------------------

def _resource_lines(meta: dict) -> str:
    """The resources the platform lists for this lecture and course, so a note
    can point to the real repo or file when the lecture refers to it."""
    lines = []
    for r in (meta.get("resources") or [])[:10]:
        lines.append(f"- {r.get('title')}: {r['url']}" if r.get("url") else f"- {r.get('title')} (a file under this lecture's Resources)")
    own = {r.get("url") for r in meta.get("resources") or []}
    for r in (meta.get("course_links") or [])[:8]:
        if r.get("url") and r["url"] not in own:
            lines.append(f"- {r.get('title')}: {r['url']} (listed for the course)")
    if not lines:
        return ""
    return "\nResources the platform lists for this lecture and course:\n" + "\n".join(lines) + "\n"


def build_prompt(meta: dict, transcript: str, transcript_source: str | None,
                 slides: list[dict], previous_gist: str | None, repo_notebook: dict | None = None) -> str:
    course = meta.get("course_title") or ""
    section = meta.get("section_title") or ""
    lecture = meta.get("lecture_title") or "Untitled lecture"
    duration = meta.get("duration_sec") or 0
    index = meta.get("lecture_index")
    code_heading = f"## {index} · {lecture}" if isinstance(index, int) and index > 0 else f"## {lecture}"

    where = []
    if course:
        where.append(f"Course: {course}")
    if section:
        where.append(f"Section: {section}")
    where.append(f"Lecture: {lecture}")
    if duration:
        where.append(f"Length: {format_ts(duration)}")
    if previous_gist:
        where.append(f"The previous lecture covered: {previous_gist}")

    if transcript_source == "asr":
        source_note = ("The transcript was produced by local speech recognition and WILL contain "
                       "mis-heard words (e.g. 'way to some' for 'weighted sum'). Silently correct "
                       "them using the slides and the subject matter.")
    elif transcript_source:
        source_note = ("The transcript comes from the platform's captions; auto-generated captions "
                       "can mis-hear technical terms, so correct those silently.")
    else:
        source_note = ("There is NO transcript for this lecture. Work from the slides alone and say "
                       "less rather than inventing what the lecturer said.")

    resources = _resource_lines(meta)
    if repo_notebook:
        resources += (
            f"\nThe course's own notebook for this lecture's day, from its repository "
            f"({repo_notebook['source']}). It usually spans several lectures. Where this lecture shows or "
            f"talks through code, that code is in here: reproduce it from this notebook exactly, and only "
            f"the part this lecture covers. A lecture that shows no code (an overview, a talk, a demo of "
            f"the result) is not a coding lecture because this exists: its CODE block is NONE.\n"
            f"<<<COURSE NOTEBOOK\n{repo_notebook['code']}\nCOURSE NOTEBOOK>>>\n")

    if slides:
        slide_lines = "\n".join(f"- {s['id']} at {format_ts(s['t'])}" for s in slides)
        slide_block = (f"You also have {len(slides)} slide captures from the video, attached after "
                       f"this text in order, each labelled with its id:\n{slide_lines}")
    else:
        slide_block = "No slides were captured, so work from the transcript alone."

    return f"""You are Margin, and you turn a lecture into study notes that make a hard idea feel obvious.
The reader is a university student who watched this lecture once and wants to understand it
deeply and remember it. They learn best from intuition first, pictures second, formulas third.

{chr(10).join(where)}

{source_note}

{slide_block}
{resources}
Faithful first. Everything the note states comes from this lecture: what was said and what was on
screen. You add understanding (analogies, diagrams, clearer explanations, the questions at the
end), never content: no facts, figures, formulas, benchmarks, model names, tools or claims the
lecture does not make. Anything you do make up, such as an example with your own numbers, is
labelled as yours. A student must be able to trust that the note is the lecture.

Lectures differ, so shape the note to this one. A concept lecture needs intuition and formulas; a
coding lecture needs its code; an overview, recap, setup or motivation lecture needs a clear map of
what it covered and what it asks you to do. Leave out any optional section that does not fit.

Produce exactly these four blocks, in this order, with the markers on their own lines.

<<<NOTE>>>
The note, in Obsidian-flavoured markdown. No frontmatter and no H1 title (both are added for you).
Use this structure:

> [!abstract] In one breath
> Two or three plain sentences a friend could follow: what this lecture is about and why it matters.

## The idea
The intuition, BEFORE any formula: a concrete mental model or analogy, the problem the idea solves,
and what would go wrong without it. For an overview lecture, the big picture of how its parts fit.
This is the most important section: make it click.

Then one `##` section per topic the lecture actually covers, in the order it covers them, each
heading ending with the timestamp where that topic starts, written exactly like `[04:12]`. In each:
- Explain in your own words, in short paragraphs and tight bullets. Bold the key terms.
- Every formula in KaTeX: inline `$...$`, display `$$...$$` on its own lines. Directly under each
  display formula add a line starting `where` that says what every symbol means in plain words,
  then one line on what the formula is really saying.
- Where a slide itself carries information words cannot (a chart, diagram, table, derivation
  layout, or code on screen), embed it on its own line as `{{{{slide:S003}}}}` using its id.
  Embed a slide only if it earns its place; never describe a slide ("the slide shows...").
- Where there is a process, pipeline, decision, cause and effect, or a relationship between
  concepts, draw it as a Mermaid diagram in a ```mermaid block. Prefer `flowchart TD` (notes are
  often read in a narrow side panel) and keep labels short; use short node ids and wrap EVERY
  node label in double quotes, e.g. A["Expected return"] --> B["Weights"].
  No parentheses or colons outside quotes. At least one diagram per note, more when it helps.
- Use a table when comparing things.

## Worked example
OPTIONAL: only when the lecture teaches something you can compute (a formula, an algorithm, a
calculation). Use the lecture's own numbers when it has them. If the numbers are yours, title the
section `## Worked example (by Margin)`. Solve step by step and check your arithmetic.

## Setup and resources
OPTIONAL: only when the lecture tells you to install, configure, sign up for, download or open
something, or points to a repo, folder, file or link. List exactly what it says, with any commands
in code blocks, and any resources listed above that it refers to. Do not add steps or links
beyond those.

## In code
OPTIONAL: only when the CODE block below is not NONE. One or two sentences on what the notebook
section for this lecture does, and whether it is the lecture's own code or Margin's illustration.

## Watch out
Mistakes, misconceptions and caveats about THIS lecture's content, including any the lecturer raises.

## Check yourself
Three to five questions that test understanding, not recall of wording, each as a collapsed
callout with the answer inside. The answers come from the lecture.
> [!question]- The question?
> The answer.

## Key terms
- **Term**: one-line definition, as the lecture uses it.

Never mention "the transcript", "the speaker said" or "in this video": write the knowledge itself.
Never use em dashes; use a comma, colon, parentheses or a new sentence instead.

<<<GIST>>>
One sentence (under 25 words) saying what this lecture teaches, for a course index. Start
with the subject itself, never "This lecture" or "In this lecture".

<<<CODE>>>
The notebook cells for this lecture, in jupytext percent format: `# %% [markdown]` starts a
markdown cell (every line prefixed with `# `), `# %%` starts a code cell. Start with a markdown
cell whose first line is `{code_heading}` followed by one short paragraph on what the code shows.
Decide which case this lecture is:
1. The lecture shows or dictates code (an editor, a notebook, a terminal, code on a slide):
   reproduce it faithfully and completely, in the lecture's order, taking it from the course's
   own notebook above when there is one rather than reading it off the slides. Only change what is needed for
   it to run today (a removed API, say) and mark each change with a `# changed:` comment.
2. The lecture teaches something computable (a formula, an algorithm, a model you can simulate)
   but shows no code: write a short implementation of exactly that, and make the first markdown
   line after the heading: `*Illustration by Margin, not shown in the lecture.*`
3. Anything else (an overview, a recap, a setup or motivation lecture, a discussion): write just
   NONE. Never invent code to fill this block.
Code that calls a paid API (OpenAI, Anthropic, Gemini and the like), a local model server (Ollama),
downloads models or datasets (Hugging Face `from_pretrained`, `pipeline`, `load_dataset`) or starts
a UI or server (Gradio, Streamlit, a web app) is reproduced exactly as shown, never replaced with a
fake or a mock. Make `# margin: run it yourself` the first line of every such cell; Margin leaves
those for you to run with your own keys.
For code Margin runs itself:
- It must run top to bottom in a FRESH kernel on its own: its own imports, its own data.
- Make it visual: when the concept can be plotted, plot it with matplotlib and call plt.show().
- Downloads (market data via yfinance, say) use a fixed date range and fall back to realistic
  synthetic data, saying so, if the download fails. No input(), nothing slower than ~30s.
- Explain with comments that say WHY, not what.
- Matplotlib labels with LaTeX use raw strings, r"$\\mu_p$", so Python does not warn about escapes.

<<<END>>>

Transcript (timestamps in [mm:ss]):
{transcript or '(none)'}
"""


# --- parsing -----------------------------------------------------------------

# <<<GIST>>> as asked, and the ways other models write it anyway: **GIST**,
# ## GIST, GIST:, [GIST]. Upper case only, so a "## Code" topic heading in a
# note is never mistaken for one.
_MARKER = re.compile(r"^[ \t]*(?:<<<\s*(NOTE|GIST|CODE|END)\s*>>>|(?:#{1,4}[ \t]*)?(?:\*\*|\[)?"
                     r"(NOTE|GIST|CODE|END)(?:\*\*|\])?:?)[ \t]*$", re.MULTILINE)


def split_blocks(raw: str) -> dict[str, str]:
    """Split the response on its markers. A model that forgets them still
    gives a usable note: everything becomes the NOTE block."""
    blocks: dict[str, str] = {}
    matches = list(_MARKER.finditer(raw))
    if not any(m.group(1) or m.group(2) == "GIST" for m in matches):
        return {"NOTE": raw.strip()}  # no real markers: a bare NOTE/END word is just text
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        # A horizontal rule a model puts around its markers is not content.
        body = re.sub(r"(?:\n[ \t]*(?:-{3,}|\*{3,})[ \t]*)+\s*$", "", raw[m.end():end].rstrip())
        blocks[m.group(1) or m.group(2)] = body.strip()
    # A model that skips the NOTE marker starts straight with the note.
    lead = re.sub(r"(?:\n[ \t]*(?:-{3,}|\*{3,})[ \t]*)+\s*$", "", raw[:matches[0].start()].rstrip()).strip()
    if "NOTE" not in blocks and lead:
        blocks["NOTE"] = lead
    return blocks


def _strip_fence(text: str) -> str:
    """Models like to wrap blocks in ``` fences even when told not to."""
    text = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", text, re.DOTALL)
    return fence.group(1) if fence else text


def parse_percent_cells(text: str) -> list[dict]:
    """jupytext percent format -> [{"type", "source"}]."""
    text = _strip_fence(text)
    if not text or text.strip().upper() == "NONE":
        return []
    cells: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        header = re.match(r"^# %%(.*)$", line)
        if header:
            if current is not None:
                cells.append(current)
            kind = "markdown" if "[markdown]" in header.group(1) else "code"
            current = {"type": kind, "lines": []}
            continue
        if current is None:
            current = {"type": "code", "lines": []}
        current["lines"].append(line)
    if current is not None:
        cells.append(current)

    out = []
    for cell in cells:
        lines = cell["lines"]
        if cell["type"] == "markdown":
            lines = [re.sub(r"^# ?", "", ln) for ln in lines]
        source = "\n".join(lines).strip("\n")
        if source.strip():
            out.append({"type": cell["type"], "source": source})
    return [_markdown_if_prose(c) for c in out]


def _compiles(source: str) -> bool:
    try:
        compile(source, "<cell>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def _markdown_if_prose(cell: dict) -> dict:
    """A "code" cell that is only a heading and sentences (a model forgetting
    the markdown marker) would crash when run: make it the markdown it is.
    Real code, or a notebook magic such as `%pip`, is left alone."""
    if cell["type"] != "code" or _compiles(cell["source"]):
        return cell
    lines = [ln for ln in cell["source"].splitlines() if ln.strip()]
    if any(ln.lstrip().startswith(("%", "!")) for ln in lines):
        return cell
    if not all(ln.lstrip().startswith("#") or not _compiles(ln.strip()) for ln in lines):
        return cell
    text = "\n".join(ln for ln in cell["source"].splitlines()
                     if not re.match(r"^\s*#\s*margin:", ln, re.IGNORECASE)).strip("\n")
    return {"type": "markdown", "source": text}


_PROTECTED = re.compile(r"```.*?```|\$\$.*?\$\$|\$[^$\n]+\$|`[^`\n]+`", re.DOTALL)


def tidy_typography(text: str) -> str:
    """Characters some models use that look right but break search and links
    in Obsidian: non-breaking and figure hyphens, narrow and non-breaking
    spaces. Also the trailing spaces some put after headings."""
    text = re.sub(r"[\u2010\u2011\u2012]", "-", text)
    text = re.sub(r"[\u00a0\u202f\u2007]", " ", text)
    return re.sub(r"^(#{1,6} [^\n]*?)[ \t]+$", r"\1", text, flags=re.MULTILINE)


def remove_em_dashes(markdown: str) -> str:
    """Em dashes read as machine-written, and this vault's owner bans them.
    The prompt forbids them; this catches the ones a model writes anyway.
    Code and math are left exactly as they are."""
    parts = []
    last = 0
    for m in _PROTECTED.finditer(markdown):
        parts.append(re.sub(r"\s*\u2014\s*", ", ", markdown[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(re.sub(r"\s*\u2014\s*", ", ", markdown[last:]))
    return "".join(parts)


_GIST_OPENER = re.compile(
    r"^(?:in )?this (?:lecture|video|session|lesson)\s+(?:(?:introduces|outlines|covers|explains|shows|"
    r"describes|discusses|presents|teaches|explores|walks through|maps out|provides|gives|is about)\s+)?",
    re.IGNORECASE)


def tidy_gist(gist: str) -> str:
    """'This lecture introduces the three pillars...' -> 'The three pillars...':
    in a list of lectures the opener says nothing."""
    trimmed = _GIST_OPENER.sub("", gist, count=1).strip()
    if not trimmed or trimmed == gist:
        return gist
    return trimmed[0].upper() + trimmed[1:]


def is_complete(raw: str) -> bool:
    """The answer reached its GIST block: a note that stops before it was cut
    off (lecture 2's stopped mid-question in Check yourself)."""
    return "GIST" in split_blocks(raw)


def parse_response(raw: str) -> tuple[str, str, list[dict]]:
    blocks = split_blocks(raw)
    note = _strip_fence(blocks.get("NOTE", "")) if "NOTE" in blocks else raw.strip()
    # A stray H1 would duplicate the one added in assemble_note.
    note = re.sub(r"\A\s*# [^\n]*\n", "", note)
    note = tidy_typography(remove_em_dashes(note))
    gist = tidy_gist(tidy_typography(remove_em_dashes(
        re.sub(r"\s+", " ", blocks.get("GIST", "")).strip().strip('"'))))
    cells = parse_percent_cells(blocks.get("CODE", ""))
    return note, gist, cells


# --- the call ----------------------------------------------------------------

def compose(session: Session, previous_gist: str | None = None, progress=None,
            quality: str = "any", choice: dict | None = None) -> Composition:
    meta = session.meta
    transcript = session.transcript_text()
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        # Keep the start and end whole and thin the middle, rather than
        # silently dropping the end of a long lecture.
        head = transcript[: MAX_TRANSCRIPT_CHARS // 2]
        tail = transcript[-MAX_TRANSCRIPT_CHARS // 2:]
        transcript = head + "\n[... middle of the lecture trimmed for length ...]\n" + tail

    slides = select_slides(session.frames)
    pictures = []
    for s in slides:
        data = session.frame_bytes(s["id"])
        if data:
            pictures.append(Picture(label=f"{s['id']} at {format_ts(s['t'])}", data=data))

    if not transcript and not pictures:
        raise ValueError("Nothing captured yet: no transcript and no slides for this lecture.")

    repo_notebook = repo_code.lecture_notebook(meta, session.transcript_text())
    prompt = build_prompt(meta, transcript, session.transcript_source, slides, previous_gist, repo_notebook)
    raw, engine = generate(prompt, pictures, progress=progress, quality=quality, accept=is_complete,
                           choice=choice)
    note, gist, cells = parse_response(raw)
    used = sorted(set(re.findall(r"\{\{slide:(S\d{3})\}\}", note)))
    return Composition(note_body=note, gist=gist, cells=cells, engine=engine, slides_used=used,
                       code_source=repo_notebook["source"] if repo_notebook and cells else None,
                       engine_chosen=bool(choice) and str(choice.get("model")) in engine)


# --- writing the note --------------------------------------------------------

def timestamp_url(meta: dict, seconds: int) -> str | None:
    url = meta.get("url") or ""
    if not url:
        return None
    if meta.get("platform") == "youtube":
        base = re.sub(r"[&?]t=\d+s?", "", url)
        joiner = "&" if "?" in base else "?"
        return f"{base}{joiner}t={seconds}s"
    # Udemy and most players honour a media fragment; worst case the link
    # opens the right lecture at the start.
    return f"{url.split('#')[0]}#t={seconds}"


def link_timestamps(markdown: str, meta: dict) -> str:
    """`[04:12]` -> `[04:12](url&t=252s)`, skipping ones that are already links."""
    def repl(m: re.Match) -> str:
        parts = [int(p) for p in m.group(1).split(":")]
        seconds = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        url = timestamp_url(meta, seconds)
        return f"[{m.group(1)}]({url})" if url else f"[{m.group(1)}]"
    return re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\](?!\()", repl, markdown)


def place_slides(markdown: str, session: Session, vault: Path, meta: dict) -> tuple[str, list[str]]:
    """Copy each referenced slide into the course's assets folder and swap the
    placeholder for an Obsidian embed. Unknown ids are dropped rather than
    left as a broken `{{slide:S999}}` in someone's notes."""
    assets = library.assets_dir(vault, meta)
    prefix = library.asset_prefix(meta)
    frames = {f["id"]: f for f in session.frames}
    written = []

    def repl(m: re.Match) -> str:
        frame = frames.get(m.group(1))
        if not frame:
            return ""
        src = session.root / "frames" / frame["file"]
        if not src.exists():
            return ""
        assets.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}-{frame['id']}.jpg"
        shutil.copyfile(src, assets / name)
        written.append(name)
        return f"![[assets/{name}|720]]"

    return re.sub(r"\{\{slide:(S\d{3})\}\}", repl, markdown), written


def frontmatter(meta: dict, comp: Composition, extra: dict) -> str:
    data = {
        "title": meta.get("lecture_title"),
        "course": meta.get("course_title"),
        "section": meta.get("section_title"),
        "lecture": meta.get("lecture_index"),
        "section_index": meta.get("section_index"),
        "platform": meta.get("platform"),
        "source": meta.get("url"),
        "lecture_id": str(meta.get("lecture_id") or ""),
        "duration_sec": meta.get("duration_sec"),
        "gist": comp.gist,
        "created": date.today().isoformat(),
        "engine": comp.engine,
        "generated_by": "margin",
        "tags": ["margin", meta.get("platform") or "web"],
        **extra,
    }
    data = {k: v for k, v in data.items() if v not in (None, "", [])}
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000) + "---\n"


def heading(meta: dict) -> str:
    index = meta.get("lecture_index")
    title = meta.get("lecture_title") or "Untitled lecture"
    return f"# {index} · {title}" if isinstance(index, int) and index > 0 else f"# {title}"


def nav_line(vault: Path, meta: dict) -> str:
    """Links to the previous lecture and the course index, so a course reads
    as a sequence in Obsidian rather than a pile of files."""
    if not meta.get("course_title"):
        return ""
    index_name = library.index_note_path(vault, meta).stem
    parts = []
    prev = previous_note(vault, meta)
    if prev is not None:
        parts.append(f"← [[{prev.stem}]]")
    parts.append(f"[[{index_name}|Course index]]")
    following = next_note(vault, meta)
    if following is not None:
        parts.append(f"[[{following.stem}]] →")
    return " · ".join(parts) + "\n\n"


# The nav line as nav_line writes it, to find and refresh it in a written note.
_NAV = re.compile(r"^(?:← \[\[[^\]\n]+\]\] · )?\[\[[^\]|\n]+\|Course index\]\](?: · \[\[[^\]\n]+\]\] →)?[ \t]*$",
                  re.MULTILINE)


def replace_nav(text: str, nav: str) -> str | None:
    """The note with its nav line replaced, or None if it has none."""
    m = _NAV.search(text)
    if not m:
        return None
    return text[:m.start()] + nav.strip() + text[m.end():]


def _neighbour(vault: Path, meta: dict, direction: int) -> Path | None:
    """The nearest lecture note before (-1) or after (+1) this one."""
    index = meta.get("lecture_index")
    if not isinstance(index, int):
        return None
    folder = library.course_dir(vault, meta)
    best, best_index = None, None
    for path in folder.glob("*.md"):
        m = re.match(r"^(\d+) - ", path.name)
        if not m or path.name.startswith("00 - "):
            continue
        n = int(m.group(1))
        if (n - index) * direction <= 0:
            continue
        if best_index is None or (n - best_index) * direction < 0:
            best, best_index = path, n
    return best


def previous_note(vault: Path, meta: dict) -> Path | None:
    return _neighbour(vault, meta, -1)


def next_note(vault: Path, meta: dict) -> Path | None:
    return _neighbour(vault, meta, +1)


def read_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text()
    except OSError:
        return {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}


def assemble_note(meta: dict, comp: Composition, body: str, vault: Path, extra_fm: dict) -> str:
    return frontmatter(meta, comp, extra_fm) + "\n" + heading(meta) + "\n\n" + nav_line(vault, meta) + body.strip() + "\n"


def drop_section(note: str, title: str) -> str:
    """Remove a `## title` section (heading through to the next `## `)."""
    m = re.search(rf"^## {re.escape(title)}\b[^\n]*\n", note, re.MULTILINE)
    if not m:
        return note
    nxt = re.search(r"^## ", note[m.end():], re.MULTILINE)
    end = m.end() + nxt.start() if nxt else len(note)
    return (note[:m.start()] + note[end:]).rstrip() + "\n"


def add_figures(note: str, figures: list[str], notebook_name: str | None, extra: str | None = None) -> str:
    """Put the plots the notebook actually produced under "## In code", so the
    note shows what the code does instead of only describing it."""
    if not figures and not notebook_name:
        return note
    addition = ""
    if figures:
        addition += "\n" + "\n".join(f"![[assets/{f}|640]]" for f in figures) + "\n"
    if notebook_name:
        addition += f"\nFull code: `{notebook_name}`, section for this lecture.\n"
    if extra:
        addition += f"\n{extra}\n"
    m = re.search(r"^## In code[^\n]*\n", note, re.MULTILINE)
    if not m:
        # The composer left the section out: put it where the note's layout
        # has it, before the closing sections, rather than after Key terms.
        later = re.search(r"^## (Watch out|Check yourself|Key terms)\b", note, re.MULTILINE)
        section = "## In code\n" + addition + "\n"
        if later:
            return note[:later.start()] + section + note[later.start():]
        return note.rstrip() + "\n\n" + section
    nxt = re.search(r"^## ", note[m.end():], re.MULTILINE)
    insert_at = m.end() + (nxt.start() if nxt else len(note) - m.end())
    return note[:insert_at].rstrip() + "\n" + addition + "\n" + note[insert_at:]
