"""Your own notes, beside Margin's: what you type, paste or say while you
watch a lecture or read a page.

Each one is kept in its session (mine.json, with its pictures and voice in
mine/) and written into the note's "## My notes" section as a block that
starts with a hidden marker:

    %% mine M19a3f5c2e4b %%
    **[12:34]** what you typed
    > the text you had selected
    ![[assets/28-M19a3f5c2e4b-1.png|480]]
    ![[assets/28-M19a3f5c2e4b.webm]]
    *Said:* what Whisper heard

Margin only ever adds a block that is missing, or takes out one you deleted
in the panel; it never writes one again, so whatever you change in Obsidian
stays. Margin's own updates leave the section alone, and a rewrite of the
note carries it over.
"""
from __future__ import annotations

import re
from datetime import date

import yaml

from sidecar import compose as C
from sidecar.sessions import format_ts

SECTION = "My notes"
HEADING = f"## {SECTION}"
_MARK = re.compile(r"^%% mine (M[0-9a-z]+) %%$", re.MULTILINE)
_HEADING = re.compile(r"^## .+$", re.MULTILINE)


def render(entry: dict, prefix: str, meta: dict) -> str:
    """One note of yours as the block that goes into the note."""
    lines = [f"%% mine {entry['id']} %%"]
    lead = []
    if entry.get("t") is not None:
        lead.append(C.link_timestamps(f"**[{format_ts(entry['t'])}]**", meta))
    elif entry.get("where"):
        lead.append(f"**{_one_line(entry['where'])}:**")
    text = (entry.get("text") or "").strip()
    first, _, rest = text.partition("\n")
    lines.append(" ".join(lead + ([first] if first else [])) or "")
    if rest.strip():
        lines.append(rest.strip())
    if entry.get("quote"):
        lines.extend(f"> {line}" if line.strip() else ">" for line in entry["quote"].strip().splitlines())
    for name in entry.get("images") or []:
        lines.append(f"![[assets/{prefix}-{name}|480]]")
    if entry.get("audio"):
        lines.append(f"![[assets/{prefix}-{entry['audio']}]]")
        if entry.get("transcript"):
            lines.append(f"*Said:* {entry['transcript'].strip()}")
    return "\n".join(line for line in lines if line != "")


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:120]


def _section_span(note: str) -> tuple[int, int] | None:
    """Where the "My notes" section (heading included) starts and ends."""
    start = next((m.start() for m in _HEADING.finditer(note) if m.group(0).strip() == HEADING), None)
    if start is None:
        return None
    after = _HEADING.search(note, start + len(HEADING))
    return start, after.start() if after else len(note)


def section(note: str) -> str | None:
    span = _section_span(note)
    return note[span[0]:span[1]].rstrip() if span else None


def ids_in(note: str) -> set[str]:
    span = _section_span(note)
    return set(_MARK.findall(note[span[0]:span[1]])) if span else set()


def insert(note: str, blocks: list[str]) -> str:
    """The note with each block not already in it added at the end of its
    "My notes" section, which goes at the end of the note if it has none."""
    have = ids_in(note)
    fresh = [b for b in blocks if _MARK.match(b) and _MARK.match(b).group(1) not in have]
    if not fresh:
        return note
    span = _section_span(note)
    if span is None:
        return note.rstrip("\n") + f"\n\n{HEADING}\n\n" + "\n\n".join(fresh) + "\n"
    start, end = span
    body = note[start:end].rstrip("\n")
    tail = note[end:]
    return note[:start] + body + "\n\n" + "\n\n".join(fresh) + "\n" + ("\n" + tail if tail else "")


def remove(note: str, entry_id: str) -> str:
    """The note without your block `entry_id`; without the section, once it is empty."""
    span = _section_span(note)
    if span is None:
        return note
    start, end = span
    part = note[start:end]
    marks = list(_MARK.finditer(part))
    at = next((i for i, m in enumerate(marks) if m.group(1) == entry_id), None)
    if at is None:
        return note
    cut_end = marks[at + 1].start() if at + 1 < len(marks) else len(part)
    part = part[:marks[at].start()] + part[cut_end:]
    tail = note[end:]
    if not _MARK.search(part):
        return note[:start].rstrip("\n") + "\n" + ("\n" + tail if tail else "")
    return note[:start] + part.rstrip("\n") + "\n" + ("\n" + tail if tail else "")


def carry(source: str, note: str) -> str:
    """`note` with the "My notes" section of `source` (the note as it is in
    your vault): kept as it is there, edits and all, at the end."""
    kept = section(source)
    if not kept:
        return note
    span = _section_span(note)
    if span:
        note = note[:span[0]].rstrip("\n") + "\n" + ("\n" + note[span[1]:] if note[span[1]:].strip() else "")
    return note.rstrip("\n") + "\n\n" + kept + "\n"


def page_note(meta: dict) -> str:
    """The note for a page you read: where it is, and then your notes."""
    title = meta.get("lecture_title") or "Untitled page"
    url = meta.get("url") or ""
    front = yaml.safe_dump({"title": title, "source": url, "site": meta.get("author") or "",
                            "saved": date.today().isoformat(), "tags": ["reading", "margin"]},
                           sort_keys=False, allow_unicode=True, width=1000)
    lines = ["---", front.rstrip(), "---", "", f"# {title}", ""]
    if url:
        lines += [f"Read at <{url}>.", ""]
    return "\n".join(lines) + f"\n{HEADING}\n"
