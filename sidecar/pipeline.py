"""The compose pipeline, end to end, reporting progress as it goes.

    capture (browser) -> compose (model) -> slides + timestamps placed
        -> notebook section executed, repaired if needed, figures pulled out
        -> note written -> course index rebuilt

Runs in a background thread so the browser never waits on a model. The panel
polls the session's status, which is updated at every step, so a slow Gemini
call shows up as "Writing notes" rather than a frozen button.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path

import yaml

from sidecar import compose as C
from sidecar import library
from sidecar import notebook as NB
from sidecar import study
from sidecar.config import AUTOLINK, MAX_AUTOLINKS_PER_SECTION
from sidecar.llm import Busy, EngineError, is_lite
from sidecar.sessions import Session, all_sessions, course_sessions, load_session
from sidecar.vault_linker import link_note_to_vault

logger = logging.getLogger(__name__)

_running: set[str] = set()
_again: set[str] = set()  # asked for again while running
_running_guard = threading.Lock()

# When every model is busy (a Gemini overload spike, say), try again after
# these waits before giving up, so a note asked for at the end of a lecture
# still arrives without anyone pressing anything.
RETRY_WAITS_SEC = (60, 180, 420)

# A note a Lite model wrote (every main model was busy) is rewritten with a
# full model once one answers: first after this long, then backing off.
UPGRADE_FIRST_WAIT_SEC = 10 * 60
UPGRADE_MAX_WAIT_SEC = 60 * 60
UPGRADE_MAX_TRIES = 8
HISTORY_DIR = ".margin-history"  # hidden from Obsidian's file list


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def note_edited(session: Session, vault: Path) -> bool:
    """You changed the note since Margin last wrote it. Notes written before
    Margin kept a fingerprint count as unedited."""
    path = library.lecture_note_path(vault, session.meta)
    written = session.written_sha
    if not written or not path.exists():
        return False
    return _digest(path.read_text()) != written


def _keep_your_edits(session: Session, vault: Path, note_path: Path) -> str | None:
    """Before a rewrite replaces a note you edited, keep your version beside it.
    Returns its vault-relative path."""
    if not note_edited(session, vault):
        return None
    history = note_path.parent / HISTORY_DIR
    history.mkdir(parents=True, exist_ok=True)
    kept = history / f"{note_path.stem} (your edits {time.strftime('%Y-%m-%d %H%M')}).md"
    kept.write_text(note_path.read_text())
    return kept.relative_to(vault).as_posix()


def is_running(key: str) -> bool:
    with _running_guard:
        return key in _running


def start(session: Session, vault: Path, choice: dict | None = None) -> bool:
    """Kick off a compose in the background. False if one is already running
    for this lecture (a double click, or auto-compose racing a manual one); it
    is then run once more afterwards if new material arrives meanwhile."""
    with _running_guard:
        if session.key in _running:
            _again.add(session.key)
            return False
        _running.add(session.key)
    session.set_status("queued", "Queued")
    threading.Thread(target=_run_guarded, args=(session, vault, choice), daemon=True).start()
    return True


# Notes written one after another: a course's earlier lectures, and notes a
# restart interrupted. One at a time, so a dozen notes do not hit the models at
# once and trip their rate limits.
_queue: list[tuple[str, Path, dict | None]] = []
_queue_cv = threading.Condition()
_worker: threading.Thread | None = None


def enqueue(sessions: list[Session], vault: Path, choice: dict | None = None) -> list[str]:
    """Queue these lectures' notes; returns the keys actually added."""
    global _worker
    added = []
    with _queue_cv:
        waiting = {key for key, _, _ in _queue}
        for session in sessions:
            if session.key in waiting or is_running(session.key):
                continue
            _queue.append((session.key, vault, choice))
            waiting.add(session.key)
            added.append(session.key)
            session.set_status("queued", "Waiting its turn")
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work_through_queue, name="margin-queue", daemon=True)
            _worker.start()
        _queue_cv.notify()
    return added


def queued_keys() -> list[str]:
    with _queue_cv:
        return [key for key, _, _ in _queue]


def _work_through_queue() -> None:
    while True:
        with _queue_cv:
            while not _queue:
                _queue_cv.wait()
            key, vault, choice = _queue.pop(0)
        session = load_session(key)
        if session is None:
            continue
        with _running_guard:
            if key in _running:
                continue
            _running.add(key)
        _run_guarded(session, vault, choice)


def _a_model_is_ready() -> bool:
    from sidecar import llm, providers
    try:
        return any(providers.entry_status(e)["ready"] for e in llm.chain_entries())
    except Exception:  # noqa: BLE001 -- only picks the wording of an error
        return True


def _run_guarded(session: Session, vault: Path, choice: dict | None = None) -> None:
    try:
        while True:
            _run_with_retries(session, vault, choice)
            with _running_guard:
                again = session.key in _again
                _again.discard(session.key)
            if not (again and session.stale):
                break
            session.set_status("queued", "Adding what was captured while the notes were being written")
    except Busy as e:
        logger.error("Every engine stayed busy for %s: %s", session.key, e)
        session.set_status("error", "Every model stayed busy for over 10 minutes (Gemini is overloaded). "
                                    "Everything captured is kept: press Write notes to try again later.",
                           detail=str(e)[:600])
    except Exception as e:  # noqa: BLE001 -- surfaced to the panel, not swallowed
        logger.exception("Compose failed for %s", session.key)
        if isinstance(e, EngineError) and not _a_model_is_ready():
            # Someone new, before setting up a model: tell them that, not
            # every engine's reason for being skipped.
            session.set_status("error", "Margin needs a model to write this note. Everything is captured: add one "
                                        "in ... then Settings, then press Write notes now.", detail=str(e)[:600])
        else:
            session.set_status("error", str(e)[:600])
    finally:
        with _running_guard:
            _running.discard(session.key)
            _again.discard(session.key)


def _run_with_retries(session: Session, vault: Path, choice: dict | None = None) -> dict:
    for wait in RETRY_WAITS_SEC:
        try:
            return run(session, vault, choice=choice)
        except Busy as e:
            logger.warning("Every engine busy for %s, retrying in %ss: %s", session.key, wait, e)
            at = time.strftime("%H:%M", time.localtime(time.time() + wait))
            session.set_status("queued", f"Every model is busy right now. Trying again at {at}.")
            time.sleep(wait)
    return run(session, vault, choice=choice)


def _relink_neighbours(vault: Path, meta: dict) -> None:
    """The notes either side of a new one point to it: "next →" in the one
    before, "← previous" in the one after (written before it existed). A note
    you have edited is left exactly as you left it."""
    if not meta.get("course_title"):
        return
    owners = {library.lecture_note_path(vault, s.meta): s for s in course_sessions(str(meta.get("course_id")))}
    for path in (C.previous_note(vault, meta), C.next_note(vault, meta)):
        if path is None:
            continue
        fm = C.read_frontmatter(path)
        if fm.get("generated_by") != "margin" or not isinstance(fm.get("lecture"), int):
            continue
        owner = owners.get(path)
        if owner is not None and note_edited(owner, vault):
            continue
        text = path.read_text()
        updated = C.replace_nav(text, C.nav_line(vault, {**meta, "lecture_index": fm["lecture"]}))
        if updated is None or updated == text:
            continue
        path.write_text(updated)
        if owner is not None:
            owner.record_written(_digest(updated))


def _with_edits_notice(note: str, kept: str) -> str:
    """Say, at the top of the rewritten note, where your edited version went."""
    notice = (f"> [!note] Rewritten by Margin with new material\n"
              f"> Your edited version is kept at `{kept}`.\n\n")
    end = note.find("\n---\n", 4) if note.startswith("---\n") else -1
    if end < 0:
        return notice + note
    head, body = note[:end + 5], note[end + 5:]
    title = re.match(r"(\s*# [^\n]*\n)", body)
    cut = title.end() if title else 0
    return head + body[:cut] + "\n" + notice + body[cut:].lstrip("\n")


# --- upgrading notes a Lite model wrote ------------------------------------------

def upgrade_once(vault: Path, now: float | None = None) -> list[str]:
    """One pass: rewrite, with a full model, each note a Lite model wrote whose
    turn has come. Notes you edited or deleted are left alone."""
    now = time.time() if now is None else now
    upgraded = []
    for session in all_sessions():
        due = session.upgrade
        if not due or due[0] > now:
            continue
        if not library.lecture_note_path(vault, session.meta).exists() or note_edited(session, vault):
            session.clear_upgrade()
            continue
        with _running_guard:
            if session.key in _running:
                continue
            _running.add(session.key)
        before = session.status
        try:
            run(session, vault, quality="full")
            upgraded.append(session.key)
        except Busy:
            tries = due[1] + 1
            session.replace_status(before)
            if tries >= UPGRADE_MAX_TRIES:
                session.clear_upgrade()
            else:
                wait = min(UPGRADE_MAX_WAIT_SEC, UPGRADE_FIRST_WAIT_SEC * 2 ** tries)
                session.schedule_upgrade(now + wait, tries)
        except Exception:  # noqa: BLE001 -- an upgrade is a bonus; the note stays
            logger.exception("Upgrade failed for %s", session.key)
            session.replace_status(before)
            session.clear_upgrade()
        finally:
            with _running_guard:
                _running.discard(session.key)
    return upgraded


def adopt_existing_notes(vault: Path) -> list[str]:
    """Notes written before Margin fingerprinted them or scheduled upgrades:
    take each one as Margin wrote it, and queue a Lite-written one for its
    upgrade. Runs at startup; a note already adopted is left as it is."""
    queued = []
    for session in all_sessions():
        path = library.lecture_note_path(vault, session.meta)
        if not path.exists():
            continue
        if not session.written_sha:
            session.record_written(_digest(path.read_text()))
            engine = (session.status.get("result") or {}).get("engine") or ""
            if is_lite(engine) and session.upgrade is None:
                session.schedule_upgrade(time.time() + 60)
                queued.append(session.key)
    return queued


def start_upgrader(vault: Path, every_sec: int = 60) -> threading.Thread:
    def loop():
        while True:
            time.sleep(every_sec)
            try:
                for key in upgrade_once(vault):
                    logger.info("Rewrote %s with a full model", key)
            except Exception:  # noqa: BLE001 -- the loop must outlive one bad pass
                logger.exception("Upgrade pass failed")

    thread = threading.Thread(target=loop, name="margin-upgrader", daemon=True)
    thread.start()
    return thread


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def resume_interrupted(vault: Path) -> list[str]:
    """Notes that were being written when the sidecar stopped (a restart, a
    crash, a logout) are started again, once. A second interruption is left
    as an error to press Retry on, so a crash that the compose itself causes
    cannot loop."""
    resume = []
    for session in all_sessions():
        status = session.status
        if status.get("state") not in ("queued", "composing", "running"):
            continue
        if status.get("resumed"):
            session.set_status("error", "Interrupted twice while writing. Press Write notes to try again.")
            continue
        session.set_status("queued", "Picking up where Margin left off", resumed=True)
        resume.append(session)
    return enqueue(resume, vault) if resume else []


def run(session: Session, vault: Path, quality: str = "any", choice: dict | None = None) -> dict:
    meta = session.meta
    note_path = library.lecture_note_path(vault, meta)

    previous = C.previous_note(vault, meta)
    previous_gist = C.read_frontmatter(previous).get("gist") if previous else None

    # What this note is written from. Anything captured while it is being
    # written leaves the lecture marked as having something new.
    rev = session.material_rev
    slides = len(session.frames)
    session.set_status("composing", f"Writing notes from {len(session.cues)} transcript lines"
                       f" and {slides} slide{'s' if slides != 1 else ''}")
    comp = C.compose(session, previous_gist=previous_gist, quality=quality, choice=choice,
                     progress=lambda message: session.set_status("composing", message))

    body, slide_files = C.place_slides(comp.note_body, session, vault, meta)
    body = C.link_timestamps(body, meta)
    if AUTOLINK:
        try:
            rel = note_path.relative_to(vault).as_posix()
            body = link_note_to_vault(body, vault, exclude_filename=rel,
                                      max_links=MAX_AUTOLINKS_PER_SECTION * 2)
        except Exception:  # noqa: BLE001 -- a linking failure must not cost the note
            logger.exception("Auto-linking failed; writing the note unlinked")

    run_info: dict = {"code": False}
    figures: list[str] = []
    nb_path = library.notebook_path(vault, meta)
    if comp.cells:
        session.set_status("running", "Running the code in a fresh kernel")
        result = NB.run_section(comp.cells, str(meta.get("lecture_id") or session.key),
                                meta.get("lecture_index"), meta.get("lecture_title") or "",
                                cwd=library.course_dir(vault, meta))
        figures = NB.extract_figures(result.cells, library.assets_dir(vault, meta),
                                     library.asset_prefix(meta))
        status_note = None
        if result.not_run:
            status_note = (f"> **Run this section yourself.** Margin left it unrun because it "
                           f"{_and(result.not_run)}.")
        elif not result.ok:
            status_note = ("> **Margin could not make this section run.** Errors:\n"
                           + "\n".join(f"> - `{e[:200]}`" for e in result.errors[:5]))
        NB.upsert_section(nb_path, library.course_dir(vault, meta).name, result.cells,
                          str(meta.get("lecture_id") or session.key), meta.get("lecture_index"),
                          status_note=status_note)
        run_info = {"code": True, "ok": result.ok, "errors": result.errors[:5],
                    "repaired": result.repaired, "installed": result.installed,
                    "not_run": result.not_run, "cells": len(result.cells)}

    if comp.cells:
        yourself = (f"Margin did not run it: it {_and(run_info['not_run'])}. Run it yourself."
                    if run_info.get("not_run") else None)
        body = C.add_figures(body, figures, nb_path.name, extra=yourself)
    else:
        body = C.drop_section(body, "In code")  # nothing to describe
        # An earlier version of this note may have had code; it no longer applies.
        NB.remove_section(nb_path, str(meta.get("lecture_id") or session.key))

    extra_fm = {
        # Written from the platform's captions for a lecture you watched
        # before Margin: no slides until you watch it with Margin.
        "written_from": "captions" if not session.frames and not session.watched else None,
        "transcript": session.transcript_source or "none",
        "slides_captured": slides,
        "coverage": round(session.coverage(), 2),
    }
    if comp.cells:
        extra_fm["notebook"] = nb_path.name
        extra_fm["code_from"] = comp.code_source
        extra_fm["code_runs"] = "yourself" if run_info.get("not_run") else run_info.get("ok", False)
    note = C.assemble_note(meta, comp, body, vault, extra_fm)
    if comp.crux:
        crux = C.link_timestamps(comp.crux, meta)
        note = study.with_crux(note, crux)
        session.save_study("crux", {"crux": crux, "engine": comp.engine})

    note_path.parent.mkdir(parents=True, exist_ok=True)
    kept = _keep_your_edits(session, vault, note_path)
    if kept:
        note = _with_edits_notice(note, kept)
    note_path.write_text(note)
    session.record_written(_digest(note))
    session.mark_composed(rev)
    _relink_neighbours(vault, meta)
    if is_lite(comp.engine) and not (choice and comp.engine_chosen):
        # A fallback, not your pick: a full model rewrites it later. A model you
        # picked in the menu is respected, even a lite or local one.
        session.schedule_upgrade(time.time() + UPGRADE_FIRST_WAIT_SEC)
    else:
        session.clear_upgrade()
    if meta.get("course_title"):
        write_course_index(vault, meta)

    result = {
        "note_path": str(note_path),
        "filename": note_path.relative_to(vault).as_posix(),
        "obsidian_uri": library.obsidian_uri(note_path),
        "notebook": nb_path.relative_to(vault).as_posix() if comp.cells else None,
        "figures": figures,
        "slides_embedded": slide_files,
        "engine": comp.engine,
        "gist": comp.gist,
        "run": run_info,
    }
    session.set_status("done", "Notes ready", result=result, resumed=False)
    return result


# --- course index --------------------------------------------------------------

def write_course_index(vault: Path, meta: dict) -> Path:
    """`00 - <Course>.md`: every composed lecture, grouped by section, each
    with its one-line gist. Rebuilt from the notes' own frontmatter every
    time, so it can never drift from what is actually in the folder."""
    folder = library.course_dir(vault, meta)
    index_path = library.index_note_path(vault, meta)
    lectures = []
    for path in folder.glob("*.md"):
        if path == index_path:
            continue
        fm = C.read_frontmatter(path)
        if fm.get("generated_by") != "margin":
            continue
        lectures.append((fm, path))

    def sort_key(item):
        fm, path = item
        m = re.match(r"^(\d+)", path.name)
        return (fm.get("section_index") or 0, fm.get("lecture") or (int(m.group(1)) if m else 0), path.name)

    lectures.sort(key=sort_key)
    notebook = library.notebook_path(vault, meta)
    lines = [
        "---",
        "title: " + yaml.safe_dump(folder.name, allow_unicode=True).strip().splitlines()[0],
        "generated_by: margin-index",
        f"platform: {meta.get('platform') or 'web'}",
        "tags: [margin, course]",
        "---",
        "",
        f"# {folder.name}",
        "",
        f"{len(lectures)} lecture note{'s' if len(lectures) != 1 else ''}"
        + (f" · code in `{notebook.name}`" if notebook.exists() else ""),
        "",
    ]
    current_section = object()
    for fm, path in lectures:
        section = fm.get("section") or "Lectures"
        if section != current_section:
            lines += ["", f"## {section}", ""]
            current_section = section
        gist = C.tidy_gist(fm.get("gist") or "")
        flag = "" if fm.get("code_runs", True) else " ⚠️"
        if fm.get("written_from") == "captions":
            flag += " *(from captions, no slides yet)*"
        lines.append(f"- [[{path.stem}]]{flag}" + (f" · {gist}" if gist else ""))
    index_path.write_text("\n".join(lines).rstrip() + "\n")
    return index_path
