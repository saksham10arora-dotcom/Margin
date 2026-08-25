import logging
import subprocess
from pathlib import Path
import httpx
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sidecar.config import (
    VAULT_PATH,
    NOTE_ENGINE,
    AUTOLINK,
    MAX_AUTOLINKS_PER_SECTION,
    load_gemini_api_keys,
)
from sidecar.key_rotation import call_with_key_rotation
from sidecar.engine_dispatch import (
    complete as engine_complete,
    engine_label,
    engine_supports_vision,
    generate_section,
)
from sidecar.vault_linker import link_note_to_vault
from sidecar.anki_export import build_flashcard_prompt, cards_to_tsv, parse_flashcards
from sidecar.vault_writer import (
    create_note,
    append_section,
    list_documents,
    search_documents,
    sanitize_filename,
    parse_frontmatter,
)
from sidecar.markdown_assembly import dedupe_section
from sidecar.haiku_client import load_style_anchors
from sidecar.transcript_fetcher import fetch_transcript
from sidecar.frame_extractor import extract_candidate_frames, seconds_to_timestamp
from sidecar.finalize import extract_video_id, build_regenerate_prompt, call_regenerate

logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://(www\.)?youtube\.com",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
# Serves screenshot images referenced by relative markdown paths in notes
# (e.g. ![...](hover-notes-images/foo.jpg)) so the in-panel viewer can
# actually load them — they're on disk, not reachable over HTTP otherwise.
app.mount("/vault-files", StaticFiles(directory=VAULT_PATH), name="vault-files")


def get_vault_path() -> Path:
    return VAULT_PATH


class NoteChunkRequest(BaseModel):
    video_id: str
    video_title: str
    video_url: str
    author: str
    duration_sec: int
    transcript_chunk: str
    start_ts: str
    end_ts: str
    is_first_chunk: bool
    # Defaulted so an older extension build keeps working unchanged.
    transcript_language: str = "English"


class FinalizeNoteRequest(BaseModel):
    filename: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/transcript")
def transcript(video_id: str):
    try:
        result = fetch_transcript(video_id)
    except Exception as e:
        logger.exception("Transcript fetch failed for video_id=%s", video_id)
        raise HTTPException(status_code=404, detail=f"No transcript available: {e}")
    return result


@app.get("/documents")
def documents(vault_path: Path = Depends(get_vault_path)):
    return list_documents(vault_path)


@app.get("/documents/search")
def documents_search(q: str = "", vault_path: Path = Depends(get_vault_path)):
    return search_documents(vault_path, q)


@app.get("/documents/content")
def document_content(filename: str, vault_path: Path = Depends(get_vault_path)):
    doc_path = (vault_path / filename).resolve()
    if vault_path.resolve() not in doc_path.parents or doc_path.suffix != ".md":
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return {"content": doc_path.read_text()}


def _generate_section(req: "NoteChunkRequest", style_anchors: list[str]) -> str:
    """Engine-agnostic. Frames are only extracted when the configured engine
    can actually look at them -- doing it for a text-only engine spends real
    seconds per chunk building images nothing will ever read."""
    frames = []
    if engine_supports_vision():
        try:
            frames = extract_candidate_frames(req.video_url, req.start_ts, req.end_ts)
        except Exception as e:
            # Frame capture is best-effort -- a broken yt-dlp/ffmpeg setup or a
            # transient network hiccup shouldn't block the note from being
            # written at all, just degrade it to text-only for this chunk.
            logger.warning(
                "Frame extraction failed for %s, continuing text-only: %s", req.video_url, e
            )

    return generate_section(
        transcript_chunk=_with_language_directive(req.transcript_chunk, req.transcript_language),
        video_title=req.video_title,
        video_url=req.video_url,
        start_ts=req.start_ts,
        end_ts=req.end_ts,
        style_anchors=style_anchors,
        frames=frames,
    )


def _with_language_directive(chunk: str, language: str) -> str:
    """Ask for English notes when the captions are not in English.

    Deliberately applied here rather than threaded through generate_section and
    all three engine clients: it is a one-line hint, and putting it in the chunk
    means every engine (haiku, gemini, every OpenAI-compatible provider) picks it
    up with no per-client change. The bracket makes it read as an instruction
    rather than as transcript content the model might try to write up.
    """
    if language.lower().startswith("english"):
        return chunk
    return (
        f"[The transcript below is in {language}. Write the notes themselves in English. "
        f"Keep technical terms as they were spoken if there is no clean English equivalent.]\n\n"
        + chunk
    )


def _autolink(section: str, vault_path: Path, existing_content: str, filename: str | None) -> str:
    """Best-effort: a linking failure must never cost the user the section
    itself, which is the expensive part to regenerate."""
    if not AUTOLINK:
        return section
    try:
        return link_note_to_vault(
            section,
            vault_path,
            exclude_filename=filename,
            max_links=MAX_AUTOLINKS_PER_SECTION,
            existing_content=existing_content,
        )
    except Exception:
        logger.exception("Auto-linking failed, writing the section unlinked")
        return section


@app.post("/note-chunk")
def note_chunk(req: NoteChunkRequest, vault_path: Path = Depends(get_vault_path)):
    # Match the note this chunk belongs to. Primary key is the video URL
    # (genuinely unique per video, so two different videos that happen to
    # share a title never get merged into the same note). Fall back to the
    # filename derived from the video title for cases where `source` isn't
    # populated (e.g. existing test fixtures that don't set it).
    expected_filename = sanitize_filename(req.video_title) + ".md"
    filename = None
    for doc in list_documents(vault_path):
        if doc["source"] == req.video_url:
            filename = doc["filename"]
            break
    if filename is None:
        for doc in list_documents(vault_path):
            if doc["filename"] == expected_filename:
                filename = doc["filename"]
                break

    if filename is not None:
        note_path = vault_path / filename
    else:
        note_path = create_note(vault_path, {
            "title": req.video_title,
            "source": req.video_url,
            "author": req.author,
            "duration_sec": req.duration_sec,
            "engine": engine_label(),
            "tags": [],
        })

    style_anchors = load_style_anchors(vault_path, count=2)
    try:
        section = _generate_section(req, style_anchors)
    except (RuntimeError, subprocess.TimeoutExpired, httpx.HTTPError, FileNotFoundError, ValueError) as e:
        logger.exception("note-chunk generation failed (engine=%s)", NOTE_ENGINE)
        raise HTTPException(status_code=502, detail=str(e))

    existing_content = note_path.read_text()
    final_section = dedupe_section(existing_content, section)
    if final_section is not None:
        rel_name = note_path.relative_to(vault_path).as_posix()
        final_section = _autolink(final_section, vault_path, existing_content, rel_name)
        append_section(note_path, final_section)

    return {
        "note_path": str(note_path),
        "filename": note_path.relative_to(vault_path).as_posix(),
        "rendered_section": final_section,
    }


@app.post("/finalize-note")
def finalize_note(req: FinalizeNoteRequest, vault_path: Path = Depends(get_vault_path)):
    """Called once, when the user stops recording -- discards the rough,
    chunk-by-chunk live note body and regenerates it from scratch: refetches
    the full transcript, re-extracts candidate frames across the whole video,
    and makes one Gemini call with all of it. This is deliberately the same
    whole-video-context approach as the batch pipeline, not an incremental
    touch-up of what live already wrote -- live processing is chunk-blind (no
    section can see or reorganize around content from other chunks), which is
    exactly the gap a live-only run can't close no matter how it's tuned."""
    note_path = (vault_path / req.filename).resolve()
    if vault_path.resolve() not in note_path.parents or note_path.suffix != ".md":
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not note_path.is_file():
        raise HTTPException(status_code=404, detail="Note not found")

    existing_content = note_path.read_text()
    if "[!abstract] TL;DR" in existing_content:
        # Already finalized -- e.g. a retried/duplicate stop-recording call.
        return {"note_path": str(note_path), "content": existing_content}
    if "\n## " not in existing_content:
        raise HTTPException(status_code=400, detail="Note has no sections to finalize")

    fm = parse_frontmatter(existing_content) or {}
    video_url = fm.get("source")
    video_title = fm.get("title")
    duration_sec = fm.get("duration_sec")
    if not video_url or not video_title or not duration_sec:
        raise HTTPException(status_code=400, detail="Note frontmatter missing source/title/duration_sec")

    api_keys = load_gemini_api_keys()
    if not api_keys:
        raise HTTPException(status_code=502, detail="No GEMINI_API_KEY found in ~/.config/keys.env")

    try:
        video_id = extract_video_id(video_url)
        cues = fetch_transcript(video_id)
    except Exception as e:
        logger.exception("Transcript refetch failed during finalize for video_url=%s", video_url)
        raise HTTPException(status_code=502, detail=f"Could not refetch transcript: {e}")
    transcript_text = " ".join(c["text"] for c in cues)

    frames = []
    try:
        frames = extract_candidate_frames(
            video_url, "00:00:00", seconds_to_timestamp(duration_sec),
            interval=5.0, max_frames=10,
        )
    except Exception as e:
        # Best-effort, same degradation as the live path: a broken/expired
        # CDN URL shouldn't block finalize, just drop to text-only.
        logger.warning("Full-video frame extraction failed for %s, continuing text-only: %s", video_url, e)

    frame_timestamps = [seconds_to_timestamp(ts) for ts, _ in frames]
    style_anchors = load_style_anchors(vault_path, count=2)
    prompt = build_regenerate_prompt(transcript_text, video_title, video_url, frame_timestamps, style_anchors)

    try:
        body = call_with_key_rotation(call_regenerate, api_keys, prompt, frames)
    except (RuntimeError, httpx.HTTPError, ValueError) as e:
        logger.exception("finalize-note generation failed")
        raise HTTPException(status_code=502, detail=str(e))

    # Finalize replaces the whole body, so the links the live chunks added are
    # gone with it -- re-link against the vault from scratch, with no
    # already-linked filter for exactly that reason.
    body = _autolink(body.strip(), vault_path, "", req.filename)

    fm_end = existing_content.index("\n---", 4) + len("\n---\n")
    finalized = existing_content[:fm_end] + f"\n# {video_title}\n\n" + body.strip() + "\n"

    note_path.write_text(finalized)

    return {"note_path": str(note_path), "content": finalized}


class FlashcardRequest(BaseModel):
    filename: str
    tags: str = "margin"


@app.post("/export/flashcards")
def export_flashcards(req: FlashcardRequest, vault_path: Path = Depends(get_vault_path)):
    """Turn a finished note into Anki-importable flashcards.

    Returns TSV rather than .apkg deliberately: Anki imports TSV natively, so
    this needs no deck-building library and cannot break when Anki changes its
    package format.
    """
    note_path = (vault_path / req.filename).resolve()
    if vault_path.resolve() not in note_path.parents or note_path.suffix != ".md":
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not note_path.is_file():
        raise HTTPException(status_code=404, detail="Note not found")

    content = note_path.read_text()
    fm = parse_frontmatter(content) or {}
    video_title = fm.get("title", note_path.stem)

    try:
        raw = engine_complete(build_flashcard_prompt(content, video_title))
    except (RuntimeError, subprocess.TimeoutExpired, httpx.HTTPError, FileNotFoundError, ValueError) as e:
        logger.exception("Flashcard generation failed (engine=%s)", NOTE_ENGINE)
        raise HTTPException(status_code=502, detail=str(e))

    cards = parse_flashcards(raw)
    if not cards:
        raise HTTPException(
            status_code=422,
            detail="The model returned no usable flashcards for this note.",
        )

    return {
        "count": len(cards),
        "tsv": cards_to_tsv(cards, tags=req.tags),
        "cards": [{"front": c.front, "back": c.back} for c in cards],
    }
