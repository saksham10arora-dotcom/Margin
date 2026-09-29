"""The course's own code, from the repository it links to.

Many courses keep their notebooks in a public GitHub repo and attach it as a
resource (the LLM Engineering course attaches ed-donner/llm_engineering to its
first lecture). Code read off screen captures is a transcription; the repo has
the instructor's exact code. When a lecture maps to a notebook there, the
composer gets that notebook and reproduces from it.

Matching is deliberately narrow: a "Week N" section and a "Day M" lecture title
map to weekN/dayM*.ipynb, the layout such courses use. No match, no code from
the repo: a wrong notebook would be worse than none.

Everything is cached on disk (~/.margin/cache), the repo listing for a day,
because GitHub allows 60 unauthenticated API calls an hour.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".margin" / "cache" / "repos"
TREE_TTL_SEC = 24 * 3600
MAX_CODE_CHARS = 40_000
TIMEOUT_SEC = 15
_GITHUB = re.compile(r"^https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$")


def course_repo(meta: dict) -> tuple[str, str] | None:
    """(owner, repo) of the first GitHub repo among the lecture's resources or
    the course's links."""
    for item in [*(meta.get("resources") or []), *(meta.get("course_links") or [])]:
        m = _GITHUB.match((item or {}).get("url") or "")
        if m:
            return m.group(1), m.group(2)
    return None


def match_notebook(paths: list[str], meta: dict) -> str | None:
    """The notebook for this lecture: weekN/dayM*.ipynb from a "Week N" section
    and a "Day M" title. Solutions and community contributions are skipped."""
    week = re.search(r"\bweek\s*(\d+)\b", meta.get("section_title") or "", re.IGNORECASE)
    day = re.search(r"\bday\s*(\d+)\b", meta.get("lecture_title") or "", re.IGNORECASE)
    if not week or not day:
        return None
    w, d = int(week.group(1)), int(day.group(1))
    candidates = []
    for path in paths:
        low = path.lower()
        if not low.endswith(".ipynb") or re.search(r"solution|community|contributions?/", low):
            continue
        parts = low.split("/")
        if len(parts) != 2 or not re.fullmatch(rf"week\s*0*{w}", parts[0]):
            continue
        # day2.ipynb, day 2.ipynb, "day3 and 4.ipynb" (covers day 4 too)
        days = [int(n) for n in re.findall(r"\d+", parts[1].split(".")[0])]
        if parts[1].startswith("day") and d in days:
            candidates.append(path)
    return sorted(candidates, key=len)[0] if candidates else None


def _cached(name: str, ttl: float | None) -> str | None:
    path = CACHE_DIR / name
    if path.exists() and (ttl is None or time.time() - path.stat().st_mtime < ttl):
        return path.read_text()
    return None


def _store(name: str, text: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / name).write_text(text)


def repo_paths(owner: str, repo: str) -> list[str]:
    name = f"{owner}__{repo}__tree.json"
    raw = _cached(name, TREE_TTL_SEC)
    if raw is None:
        resp = httpx.get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
                         headers={"Accept": "application/vnd.github+json"}, timeout=TIMEOUT_SEC)
        resp.raise_for_status()
        raw = json.dumps([t["path"] for t in resp.json().get("tree", []) if t.get("type") == "blob"])
        _store(name, raw)
    return json.loads(raw)


# Words too common in code or speech to say which cells a lecture covers.
_COMMON = set("""import from print return true false none with self else elif while class lambda this that your
have what will just then them they there here into about when where which would could should also some more
than only very make sure like want need know right okay yeah going code cell cells note list dict string
value data text name file type result results over using used need back first second""".split())
SPOKEN_SHARE = 0.35  # of a cell's telling words heard in the lecture


def _words(text: str) -> set[str]:
    parts = re.findall(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|[a-z]+", re.sub(r"[_\d]+", " ", text))
    return {w.lower() for w in parts if len(w) >= 4 and w.lower() not in _COMMON}


def spoken_cells(nb: dict, transcript: str) -> dict:
    """Only the code cells this lecture talks through (most of each cell's
    telling words are said in it), each with the markdown just before it. A
    notebook spans a day's lectures; lecture 15, a talk about frontier models,
    was given the day's code and wrote it into its notes."""
    heard = _words(transcript)
    cells = nb.get("cells", [])
    keep: set[int] = set()
    for i, cell in enumerate(cells):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", [])) if isinstance(cell.get("source"), list) else cell.get("source", "")
        telling = _words(source)
        hits = telling & heard
        if len(hits) >= 2 and len(hits) >= SPOKEN_SHARE * len(telling):
            keep.add(i)
            if i and cells[i - 1].get("cell_type") == "markdown":
                keep.add(i - 1)
    return {**nb, "cells": [c for i, c in enumerate(cells) if i in keep]}


def notebook_as_percent(nb: dict) -> str:
    """A notebook's cells as jupytext percent text, without outputs."""
    chunks = []
    for cell in nb.get("cells", []):
        source = "".join(cell.get("source", [])) if isinstance(cell.get("source"), list) else cell.get("source", "")
        if not source.strip():
            continue
        if cell.get("cell_type") == "markdown":
            chunks.append("# %% [markdown]\n" + "\n".join("# " + ln if ln else "#" for ln in source.splitlines()))
        elif cell.get("cell_type") == "code":
            chunks.append("# %%\n" + source)
    return "\n\n".join(chunks)


def lecture_notebook(meta: dict, transcript: str = "") -> dict | None:
    """{"source": "github.com/owner/repo/path", "code": percent text} for this
    lecture, or None. Never raises: the repo is a bonus, not a dependency."""
    repo = course_repo(meta)
    if not repo:
        return None
    try:
        path = match_notebook(repo_paths(*repo), meta)
        if not path:
            return None
        name = f"{repo[0]}__{repo[1]}__{re.sub(r'[^A-Za-z0-9_.-]+', '_', path)}"
        raw = _cached(name, TREE_TTL_SEC)
        if raw is None:
            resp = httpx.get(f"https://raw.githubusercontent.com/{repo[0]}/{repo[1]}/HEAD/{path}",
                             timeout=TIMEOUT_SEC)
            resp.raise_for_status()
            raw = resp.text
            _store(name, raw)
        nb = json.loads(raw)
        if transcript:
            nb = spoken_cells(nb, transcript)
            if not any(c.get("cell_type") == "code" for c in nb["cells"]):
                return None  # this lecture talks through none of the day's code
        code = notebook_as_percent(nb)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read the course repo for %s: %s", meta.get("lecture_title"), e)
        return None
    if len(code) > MAX_CODE_CHARS:
        code = code[:MAX_CODE_CHARS] + "\n# [... the rest of the notebook is cut for length ...]"
    return {"source": f"github.com/{repo[0]}/{repo[1]}/{path}", "code": code}
