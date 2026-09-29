"""Where every artifact lives in the vault.

A course is a folder. Inside it, one note per lecture, one notebook for the
whole course, one index note that ties them together, and an assets folder
for the slides and figures the notes embed:

    <vault>/<Course Title>/
        00 - <Course Title>.md          index: sections, lectures, one-line gists
        <Course Title>.ipynb            every lecture's code, one section each
        28 - Expected return of the portfolio.md
        assets/28-S03.jpg               slides captured while you watched
        assets/28-fig1.png              plots the notebook produced

A standalone video with no course (most of YouTube) is filed under a folder
named for its platform, so it still has somewhere sensible to live.

Filenames keep their spaces and capitals. v1 slugified everything into
`expected-return-of-the-portfolio.md`, which is safe but reads like a URL in
Obsidian's file tree, and the tree is where these notes are actually browsed.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

# Characters Obsidian (or the filesystem) refuses in a filename, plus the ones
# that break wikilinks: # | ^ [ ] would all be read as link syntax.
_UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]')

PLATFORM_FOLDERS = {
    "youtube": "YouTube",
    "udemy": "Udemy",
    "coursera": "Coursera",
    "local": "Local videos",
}


def clean_name(text: str, limit: int = 90) -> str:
    """A human-readable name that is safe as a file or folder name."""
    cleaned = _UNSAFE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rsplit(" ", 1)[0].rstrip(" .,-")
    return cleaned or "Untitled"


def course_dir(vault: Path, meta: dict) -> Path:
    """The folder a lecture's artifacts belong in."""
    if meta.get("course_title"):
        return vault / clean_name(meta["course_title"], limit=70)
    platform = meta.get("platform") or "web"
    return vault / PLATFORM_FOLDERS.get(platform, platform.capitalize())


def lecture_stem(meta: dict) -> str:
    """`28 - Expected return of the portfolio`, or just the title when the
    platform gives no ordering."""
    title = clean_name(meta.get("lecture_title") or "Untitled lecture")
    index = meta.get("lecture_index")
    if isinstance(index, int) and index > 0:
        return f"{index:02d} - {title}"
    return title


def asset_prefix(meta: dict) -> str:
    """Short, stable prefix for this lecture's asset files. The lecture index
    when there is one, since `28-S03.jpg` is far easier to recognise in a
    folder listing than a slug of the whole title."""
    index = meta.get("lecture_index")
    if isinstance(index, int) and index > 0:
        return f"{index:02d}"
    return re.sub(r"[^a-z0-9]+", "-", lecture_stem(meta).lower()).strip("-")[:40] or "lecture"


def lecture_note_path(vault: Path, meta: dict) -> Path:
    return course_dir(vault, meta) / f"{lecture_stem(meta)}.md"


def assets_dir(vault: Path, meta: dict) -> Path:
    return course_dir(vault, meta) / "assets"


def notebook_path(vault: Path, meta: dict) -> Path:
    folder = course_dir(vault, meta)
    return folder / f"{folder.name}.ipynb"


def index_note_path(vault: Path, meta: dict) -> Path:
    folder = course_dir(vault, meta)
    return folder / f"00 - {folder.name}.md"


def find_obsidian_root(path: Path) -> Path | None:
    """The nearest ancestor that is an Obsidian vault (has a .obsidian folder).

    v1 hardcoded the vault name as "obs" and the subfolder as "notesyt", so
    "Open in Obsidian" only ever worked on one machine. Obsidian needs the
    vault's *name* and a path relative to its root; both come from here.
    """
    for candidate in [path, *path.parents]:
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def obsidian_uri(file_path: Path) -> str | None:
    root = find_obsidian_root(file_path.parent)
    if root is None:
        return None
    rel = file_path.relative_to(root).as_posix()
    if rel.endswith(".md"):
        rel = rel[:-3]
    return f"obsidian://open?vault={quote(root.name)}&file={quote(rel)}"
