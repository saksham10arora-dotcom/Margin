"""Study tools on top of a written note: the crux, answers from the lecture,
and flashcards.

A note is for understanding a lecture once. These are for the weeks after:
the crux is the 20% worth rereading, a question is answered from what was
actually said (with the moment to jump back to), and the flashcards are
quizzed in the panel before they ever need Anki.
"""
from __future__ import annotations

import re

from sidecar import compose as C
from sidecar import llm
from sidecar.anki_export import build_flashcard_prompt, cards_to_tsv, parse_flashcards, strip_note_chrome
from sidecar.sessions import Session

CRUX_TITLE = "The crux (80/20)"
# Our callout, and the blank line after it, so a new crux replaces an old one.
_CRUX_CALLOUT = re.compile(r"^> \[!tldr\]-? The crux[^\n]*\n(?:>[^\n]*\n)*\n?", re.MULTILINE)
_BREATH = re.compile(r"^> \[!abstract\][^\n]*\n(?:>[^\n]*\n)*", re.MULTILINE)
MAX_ASK_TRANSCRIPT = 100_000

# --- the crux --------------------------------------------------------------------------


def crux_callout(crux: str) -> str:
    """The crux as a folded Obsidian callout: one click away, never in the way."""
    lines = [f"> {line}" if line.strip() else ">" for line in crux.strip().splitlines()]
    return f"> [!tldr]- {CRUX_TITLE}\n" + "\n".join(lines) + "\n"


def with_crux(note: str, crux: str) -> str:
    """The note with its crux right under "In one breath" (or before the first
    section), replacing a crux it already has."""
    note = _CRUX_CALLOUT.sub("", note)
    block = crux_callout(crux)
    breath = _BREATH.search(note)
    if breath:
        return note[:breath.end()] + "\n" + block + note[breath.end():]
    first = re.search(r"^## ", note, re.MULTILINE)
    if first:
        return note[:first.start()] + block + "\n" + note[first.start():]
    return note.rstrip() + "\n\n" + block


def crux_in_note(note: str) -> str | None:
    """The crux written into a note, if it has one."""
    m = _CRUX_CALLOUT.search(note)
    if not m:
        return None
    lines = m.group(0).splitlines()[1:]
    return "\n".join(re.sub(r"^> ?", "", line) for line in lines).strip() or None


def make_crux(note: str, title: str) -> tuple[str, str]:
    """A crux for a note written before notes had one. Returns (crux, engine)."""
    prompt = (f'You are Margin. Below is a study note for the lecture "{title}". Write its crux.\n\n'
              f"{C.CRUX_RULES}\nWrite only the crux: no preamble, no heading, no closing remark. "
              f"Never use em dashes.\n\n<<<NOTE\n{strip_note_chrome(note)}\nNOTE>>>")
    raw, engine = llm.generate(prompt, max_tokens=3000, temperature=0.3)
    crux = C.parse_crux(f"<<<CRUX>>>\n{raw}\n<<<END>>>")
    if not crux:
        raise llm.EngineError("The model returned no crux.")
    return crux, engine


# --- asking the lecture ------------------------------------------------------------------

ASK_RULES = """You are Margin, answering a student's question about one lecture they watched,
"{title}", from the lecture itself: its transcript (times in [mm:ss]) and the study note written from it.
- Answer directly, in 30 to 150 words, in plain language. Formulas in KaTeX.
- Say where the lecture explains it with the time, written exactly like [04:12], so they can jump back.
- If the lecture does not cover it, say so in one sentence first. Then you may add at most two
  sentences of general help, starting with "Not from the lecture:".
- No preamble ("Great question"), no heading, no em dashes."""


def ask(session: Session, note: str | None, question: str) -> tuple[str, str]:
    """An answer from the lecture, with its moments linked. Returns (answer, engine)."""
    meta = session.meta
    transcript = session.transcript_text()
    if len(transcript) > MAX_ASK_TRANSCRIPT:
        half = MAX_ASK_TRANSCRIPT // 2
        transcript = transcript[:half] + "\n[... middle trimmed ...]\n" + transcript[-half:]
    if not transcript and not note:
        raise ValueError("Nothing to answer from yet: no transcript and no note for this lecture.")
    prompt = (ASK_RULES.format(title=meta.get("lecture_title") or "this lecture")
              + f"\n\nQuestion: {question.strip()}\n\n<<<TRANSCRIPT\n{transcript or '(none)'}\nTRANSCRIPT>>>\n"
              + (f"\n<<<NOTE\n{strip_note_chrome(note)}\nNOTE>>>\n" if note else ""))
    raw, engine = llm.generate(prompt, max_tokens=1500, temperature=0.2)
    answer = C.tidy_typography(C.remove_em_dashes(C._strip_fence(raw))).strip()
    return C.link_timestamps(answer, meta), engine


# --- flashcards -----------------------------------------------------------------------------


def make_cards(note: str, title: str, tags: str = "margin") -> dict:
    """Flashcards from a note, as cards to quiz in the panel and a file for Anki."""
    raw, engine = llm.generate(build_flashcard_prompt(note, title), max_tokens=8000, temperature=0.3)
    cards = parse_flashcards(raw)
    if not cards:
        raise llm.EngineError("The model returned no usable flashcards.")
    return {"cards": [{"front": c.front, "back": c.back} for c in cards],
            "tsv": cards_to_tsv(cards, tags=tags), "engine": engine}
