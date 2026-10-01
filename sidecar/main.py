"""Margin sidecar, v2.

The browser captures, this process thinks and writes. Routes:

  GET  /health                         engines, local ASR, notebook kernel, vault
  POST /session                        open (or resume) a lecture's session
  GET  /session/{key}                  everything the panel needs to draw
  POST /session/{key}/captions         whole caption track, timed
  POST /session/{key}/audio            a recorded audio segment -> local Whisper
  POST /session/{key}/frame            a captured slide
  GET  /session/{key}/frame/{id}       a captured slide, for the panel
  POST /session/{key}/watched          a range of video that actually played
  POST /session/{key}/compose          write the note + notebook section (async)
  GET  /session/{key}/note             the composed note, for the panel
  GET  /session/{key}/code             this lecture's notebook cells + outputs
  GET  /session/{key}/crux             the 80/20 of the lecture (POST: make it for an older note)
  POST /session/{key}/ask              a question, answered from the lecture with its moments
  GET  /session/{key}/cards            flashcards to quiz in the panel (POST: make them)
  GET  /course/{course_id}             every captured lecture in a course
  GET  /transcript?video_id=           YouTube captions (public API)
  POST /export/flashcards              Anki TSV from any note in the vault
  GET  /documents, /documents/search, /documents/content

Everything arrives from the extension's background worker, never straight
from a web page, so CORS is closed to web origins: a random site cannot read
your notes off localhost.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import nbformat
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sidecar import asr, catalog, chain, keys, library, llm, pipeline, providers, study
from sidecar import compose as C
from sidecar import notebook as NB
from sidecar.anki_export import build_flashcard_prompt, cards_to_tsv, parse_flashcards
from sidecar.config import VAULT_PATH
from sidecar.sessions import course_sessions, load_session, open_session, session_key, unpack_moving
from sidecar.transcript_fetcher import fetch_transcript
from sidecar.vault_writer import list_documents, parse_frontmatter, search_documents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("margin")
# httpx logs every request URL at INFO. Margin's no longer carry a key, but a
# library's request log has no place in the sidecar's.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class _WithoutHealthChecks(logging.Filter):
    """The login item asks /health once a minute: 1,440 identical lines a day
    that bury everything else."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_WithoutHealthChecks())

LOG_PATH = Path.home() / ".margin" / "sidecar.log"


def tidy_log(path: Path = LOG_PATH, limit: int = 5_000_000, keep: int = 1_000_000) -> None:
    """The login item appends to one log forever: keep it bounded, and scrub
    any API key an older version let into it. Rewritten in place, because the
    running process holds it open for appending and keeps writing at the end."""
    if not path.exists():
        return
    text = path.read_text(errors="replace")
    cleaned = re.sub(r"(key=)[A-Za-z0-9._-]{16,}", r"\1[redacted]", text)
    if len(cleaned) > limit:
        cleaned = cleaned[-keep:]
    if cleaned != text:
        with path.open("r+") as f:
            f.seek(0)
            f.truncate()
            f.write(cleaned)


@asynccontextmanager
async def lifespan(_app):
    try:
        tidy_log()
    except OSError as e:
        logger.warning("Could not tidy the log: %s", e)
    resumed = pipeline.resume_interrupted(get_vault_path())
    if resumed:
        logger.info("Picking up notes interrupted by a restart: %s", ", ".join(resumed))
    queued = pipeline.adopt_existing_notes(get_vault_path())
    if queued:
        logger.info("Will rewrite with a full model: %s", ", ".join(queued))
    pipeline.start_upgrader(get_vault_path())
    yield

VERSION = "2.10.4"  # 2.6: course repo code; 2.7: any provider (engines.toml); 2.8: model menu (/providers); 2.8.1: presenter area; 2.8.2: installer keeps your notes folder; 2.8.3: Apache-2.0; 2.8.4: second look before dropping a slide; 2.9: crux, ask, quiz; 2.9.1: re-install restarts it; 2.9.2: slides fade in once; 2.9.3: launcher kept across updates; 2.9.4: launcher checks the port, not /health; 2.10: DeepLearning.AI; 2.10.1: handwritten lectures fold, a title change is a new slide, late sound is waited for; 2.10.2: a teacher in front of the slide; 2.10.3: a long lecture keeps what you watched; 2.10.4: closing the tab updates the note

app = FastAPI(title="Margin", version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://[a-p]{32}",
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)
app.mount("/vault-files", StaticFiles(directory=VAULT_PATH), name="vault-files")


def get_vault_path() -> Path:
    return VAULT_PATH


def _session_or_404(key: str):
    session = load_session(key)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    return session


# --- health ------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "engines": llm.engine_chain(),
        # Something in the model order can write right now; if not, the
        # panel asks you to choose a model instead of failing at the end.
        "setup_ready": _setup_ready(),
        "asr": asr.asr_available(),
        "kernel": NB.kernel_available(),
        "vault": str(VAULT_PATH),
    }


# --- sessions ----------------------------------------------------------------

class Resource(BaseModel):
    title: str
    url: str | None = None
    kind: str = "link"


class LectureMeta(BaseModel):
    platform: str = "web"
    url: str = ""
    course_id: str | None = None
    course_title: str | None = None
    section_title: str | None = None
    section_index: int | None = None
    lecture_id: str | None = None
    lecture_title: str | None = None
    lecture_index: int | None = None
    duration_sec: float | None = None
    author: str | None = None
    resources: list[Resource] = []
    course_links: list[Resource] = []


@app.post("/session")
def create_session(meta: LectureMeta, vault_path: Path = Depends(get_vault_path)):
    session = open_session(meta.model_dump())
    return _summary(session, vault_path)


def _summary(session, vault_path: Path) -> dict:
    # "edited": you changed the note since Margin wrote it, so Margin will not
    # rewrite it on its own (a rewrite you ask for keeps a copy of yours).
    return {**session.summary(), "edited": pipeline.note_edited(session, vault_path)}


@app.post("/session/lookup")
def lookup_session(meta: LectureMeta):
    """The lecture's session if one already exists, without creating one.

    On opt-in sites (YouTube, the open web) nothing may be stored until you
    press Capture, but a lecture you captured before should still open with
    its slides and notes. This answers "have I captured this?" without the
    side effect of opening a session for every video you ever play.
    """
    session = load_session(session_key(meta.model_dump()))
    return {"exists": session is not None, "summary": session.summary() if session else None}


@app.get("/session/{key}")
def get_session(key: str, vault_path: Path = Depends(get_vault_path)):
    return _summary(_session_or_404(key), vault_path)


class CaptionPayload(BaseModel):
    cues: list[dict]
    source: str = "captions"


@app.post("/session/{key}/captions")
def post_captions(key: str, payload: CaptionPayload):
    session = _session_or_404(key)
    count = session.set_caption_cues(payload.cues, payload.source)
    return {"cue_count": count, "stale": session.stale}


class AudioPayload(BaseModel):
    data: str  # base64
    mime: str = "audio/webm"
    video_start: float
    rate: float = 1.0


@app.post("/session/{key}/audio")
def post_audio(key: str, payload: AudioPayload):
    session = _session_or_404(key)
    if session.transcript_source not in (None, "asr"):
        return {"skipped": "captions already cover this lecture", "cues": []}
    audio = base64.b64decode(payload.data)
    if len(audio) < 2000:
        return {"cues": []}  # a sliver of silence between seeks
    suffix = ".ogg" if "ogg" in payload.mime else ".mp4" if "mp4" in payload.mime else ".webm"
    if os.environ.get("MARGIN_KEEP_AUDIO"):
        # Diagnostics: keep what the browser recorded, to replay through Whisper.
        keep = session.root / "audio"
        keep.mkdir(exist_ok=True)
        (keep / f"{payload.video_start:08.2f}-x{payload.rate:g}{suffix}").write_bytes(audio)
    title = session.meta.get("lecture_title") or ""
    course = session.meta.get("course_title") or ""
    try:
        cues = asr.transcribe(audio, rate=payload.rate, prompt=f"{course}. {title}.", suffix=suffix)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    shifted = [{"start": payload.video_start + c["start"], "end": payload.video_start + c["end"],
                "text": c["text"]} for c in cues]
    session.add_asr_cues(shifted)
    return {"cues": shifted, "stale": session.stale}


class FramePayload(BaseModel):
    t: float
    data: str  # base64 jpeg
    moving: str | None = None  # the area that never stops changing (a face bubble), 64x36 bits


@app.post("/session/{key}/frame")
def post_frame(key: str, payload: FramePayload):
    session = _session_or_404(key)
    raw = payload.data.split(",", 1)[1] if payload.data.startswith("data:") else payload.data
    try:
        record = session.add_frame(payload.t, base64.b64decode(raw), unpack_moving(payload.moving))
    except Exception as e:  # noqa: BLE001 -- a corrupt frame is dropped, not fatal
        raise HTTPException(status_code=400, detail=f"Unreadable frame: {e}")
    return {**record, "stale": session.stale}


@app.get("/session/{key}/frame/{frame_id}")
def get_frame(key: str, frame_id: str):
    if not re.fullmatch(r"S\d{3,4}", frame_id):
        raise HTTPException(status_code=400, detail="Bad frame id")
    data = _session_or_404(key).frame_bytes(frame_id)
    if data is None:
        raise HTTPException(status_code=404, detail="No such frame")
    return Response(content=data, media_type="image/jpeg")


class WatchedPayload(BaseModel):
    start: float
    end: float
    duration_sec: float | None = None


@app.post("/session/{key}/watched")
def post_watched(key: str, payload: WatchedPayload):
    session = _session_or_404(key)
    if payload.duration_sec and not session.meta.get("duration_sec"):
        session.update_meta({"duration_sec": payload.duration_sec})
    session.mark_watched(payload.start, payload.end)
    return {"coverage": round(session.coverage(), 3)}


class EngineChoice(BaseModel):
    """A model picked in the panel's menu. None: your default chain."""
    provider: str
    model: str
    vision: bool | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ComposeRequest(BaseModel):
    auto: bool = False  # Margin's own decision (a lecture ended), not your button
    engine: EngineChoice | None = None
    # The tab closed: the page is gone and cannot judge, so the sidecar does.
    if_needed: bool = False


# Leaving a lecture writes its first note once you watched this much of it,
# the same as moving on to the next lecture in the panel.
FIRST_NOTE_ON_LEAVING = 0.75


def _worth_writing(session, vault_path: Path) -> bool:
    """Margin's rule for writing a note as you leave a lecture: a first note
    once most of it was watched, an update when something new came in since
    (a slide, speech not heard before, a new part of a long lecture)."""
    if not (session.frames or session.cues):
        return False
    if library.lecture_note_path(vault_path, session.meta).exists():
        return session.stale
    return session.coverage() >= FIRST_NOTE_ON_LEAVING


@app.post("/session/{key}/compose")
def post_compose(key: str, request: ComposeRequest | None = None,
                 vault_path: Path = Depends(get_vault_path)):
    session = _session_or_404(key)
    if request and request.auto and pipeline.note_edited(session, vault_path):
        return {"started": False, "skipped": "edited", "status": session.status}
    if request and request.if_needed and not _worth_writing(session, vault_path):
        return {"started": False, "skipped": "nothing new", "status": session.status}
    choice = request.engine.as_dict() if request and request.engine else None
    started = pipeline.start(session, vault_path, choice)
    return {"started": started, "status": session.status}


@app.get("/session/{key}/note")
def get_note(key: str, vault_path: Path = Depends(get_vault_path)):
    session = _session_or_404(key)
    path = library.lecture_note_path(vault_path, session.meta)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not composed yet")
    folder = path.parent.relative_to(vault_path).as_posix()
    return {
        "content": path.read_text(),
        "filename": path.relative_to(vault_path).as_posix(),
        "folder": folder,
        "obsidian_uri": library.obsidian_uri(path),
    }


def _note_or_404(session, vault_path: Path) -> tuple[Path, str]:
    path = library.lecture_note_path(vault_path, session.meta)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Write the notes first")
    return path, path.read_text()


@app.get("/session/{key}/crux")
def get_crux(key: str, vault_path: Path = Depends(get_vault_path)):
    session = _session_or_404(key)
    made = session.study("crux")
    if made:
        return made
    path = library.lecture_note_path(vault_path, session.meta)
    crux = study.crux_in_note(path.read_text()) if path.exists() else None
    if not crux:
        raise HTTPException(status_code=404, detail="No crux yet")
    return {"crux": crux}


@app.post("/session/{key}/crux")
def make_crux(key: str, vault_path: Path = Depends(get_vault_path)):
    """The crux for a note written before notes had one. Added to the note too,
    unless you have edited it: then it stays here, in the panel."""
    session = _session_or_404(key)
    path, note = _note_or_404(session, vault_path)
    crux = study.crux_in_note(note)
    engine = None
    if not crux:
        try:
            crux, engine = study.make_crux(note, session.meta.get("lecture_title") or path.stem)
        except llm.EngineError as e:
            raise HTTPException(status_code=502, detail=str(e))
        crux = C.link_timestamps(crux, session.meta)
        if not pipeline.note_edited(session, vault_path):
            updated = study.with_crux(note, crux)
            path.write_text(updated)
            session.record_written(pipeline._digest(updated))
    session.save_study("crux", {"crux": crux, "engine": engine})
    return session.study("crux")


class Question(BaseModel):
    question: str


@app.post("/session/{key}/ask")
def ask_lecture(key: str, q: Question, vault_path: Path = Depends(get_vault_path)):
    session = _session_or_404(key)
    question = q.question.strip()
    if not 2 <= len(question) <= 600:
        raise HTTPException(status_code=422, detail="Ask a question of up to 600 characters")
    path = library.lecture_note_path(vault_path, session.meta)
    note = path.read_text() if path.exists() else None
    try:
        answer, engine = study.ask(session, note, question)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except llm.EngineError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"question": question, "answer": answer, "engine": engine}


@app.get("/session/{key}/cards")
def get_cards(key: str):
    session = _session_or_404(key)
    made = session.study("cards")
    if not made:
        raise HTTPException(status_code=404, detail="No flashcards yet")
    # Made from an earlier version of the note: still shown, with a way to remake them.
    return {**made, "stale": made.get("note") != session.written_sha}


@app.post("/session/{key}/cards")
def make_cards(key: str, vault_path: Path = Depends(get_vault_path)):
    session = _session_or_404(key)
    path, note = _note_or_404(session, vault_path)
    try:
        made = study.make_cards(note, session.meta.get("lecture_title") or path.stem)
    except llm.EngineError as e:
        raise HTTPException(status_code=502, detail=str(e))
    session.save_study("cards", {**made, "note": session.written_sha})
    return {**session.study("cards"), "stale": False}


@app.get("/session/{key}/code")
def get_code(key: str, vault_path: Path = Depends(get_vault_path)):
    session = _session_or_404(key)
    path = library.notebook_path(vault_path, session.meta)
    if not path.exists():
        return {"cells": [], "notebook": None}
    nb = nbformat.read(path, as_version=4)
    lecture_id = str(session.meta.get("lecture_id") or session.key)
    cells = []
    for cell in nb.cells:
        if str(cell.metadata.get("margin", {}).get("lecture_id")) != lecture_id:
            continue
        outputs = []
        for out in cell.get("outputs", []):
            kind = out.get("output_type")
            if kind == "stream":
                outputs.append({"type": "text", "text": out.get("text", "")[-4000:]})
            elif kind in ("execute_result", "display_data"):
                data = out.get("data", {})
                if "image/png" in data:
                    outputs.append({"type": "image", "png": data["image/png"]})
                elif "text/html" in data and "<table" in data["text/html"]:
                    outputs.append({"type": "html", "html": data["text/html"][:20000]})
                elif "text/plain" in data:
                    outputs.append({"type": "text", "text": data["text/plain"][-4000:]})
            elif kind == "error":
                outputs.append({"type": "error", "text": f"{out.get('ename')}: {out.get('evalue')}"})
        cells.append({"type": cell.cell_type, "source": cell.source, "outputs": outputs})
    return {"cells": cells, "notebook": path.relative_to(vault_path).as_posix(),
            "notebook_path": str(path)}


@app.get("/course/{course_id}")
def get_course(course_id: str, vault_path: Path = Depends(get_vault_path)):
    lectures = []
    for session in course_sessions(course_id):
        meta = session.meta
        note = library.lecture_note_path(vault_path, meta)
        lectures.append({
            "key": session.key,
            "lecture_id": meta.get("lecture_id"),
            "lecture_index": meta.get("lecture_index"),
            "lecture_title": meta.get("lecture_title"),
            "section_title": meta.get("section_title"),
            "section_index": meta.get("section_index"),
            "url": meta.get("url"),
            "composed": note.exists(),
            "coverage": round(session.coverage(), 2),
            "status": session.status.get("state"),
            "message": (session.status.get("message") or "")[:200],
        })
    lectures.sort(key=lambda l: (l.get("lecture_index") or 10**6, l.get("lecture_title") or ""))
    return {"lectures": lectures}


class BackfillRequest(BaseModel):
    keys: list[str]
    engine: EngineChoice | None = None


@app.post("/course/{course_id}/backfill")
def backfill_course(course_id: str, request: BackfillRequest, vault_path: Path = Depends(get_vault_path)):
    """Write notes for lectures of this course that were never watched with
    Margin (their captions were posted), one after another in the background."""
    todo = []
    for key in request.keys:
        session = load_session(key)
        if session is None or str(session.meta.get("course_id")) != str(course_id):
            continue
        if library.lecture_note_path(vault_path, session.meta).exists() or not session.cues:
            continue
        todo.append(session)
    return {"queued": pipeline.enqueue(todo, vault_path, request.engine.as_dict() if request.engine else None)}


# --- the model menu ---------------------------------------------------------------

def _from_margin(request: Request) -> None:
    """Changes to your setup come only from Margin's own pages: they send this
    header, and a web page cannot add it to a request to localhost without the
    browser first asking the sidecar, which refuses web origins."""
    if request.headers.get("x-margin") != "1":
        raise HTTPException(status_code=403, detail="Only Margin's own pages can change its settings")


def _order() -> list[dict]:
    return [providers.entry_status(e) for e in llm.chain_entries()]


def _setup_ready() -> bool:
    try:
        return any(e["ready"] for e in _order())
    except Exception:  # noqa: BLE001 -- health must answer
        return True


@app.get("/providers")
def list_providers():
    """Everything for the model menu: your order (1st, 2nd, ...), your
    subscriptions, and every provider with whether it is ready. Never a key."""
    return {"order": _order(), "custom_order": chain.load() is not None, "setup_ready": _setup_ready(),
            "default": llm.engine_chain(), "subscriptions": providers.subscription_rows(),
            "not_yet": providers.subscriptions.NOT_YET, "providers": providers.menu(),
            "popular": catalog.POPULAR}


@app.get("/providers/search")
def search_providers(q: str = ""):
    ready = {p["id"] for p in providers.menu() if p["ready"]}
    hits = catalog.search(q, ready)
    for sub in providers.subscription_rows():
        if not sub["installed"]:
            continue
        for m in providers.subscriptions.models(sub["id"]):
            text = f"{sub['name']} {sub['tool']} {sub['id']} {m}".lower()
            if all(w in text for w in q.lower().split()):
                # OpenCode can pass images, but only a model that reads them sees slides.
                sees = sub["images"] and (sub["id"] != "opencode" or providers.sees_images("", m.split("/", 1)[-1]))
                hits.insert(0, {"id": m, "name": m, "vision": sees, "provider": sub["id"],
                                "provider_name": sub["name"], "ready": sub["ready"], "subscription": True})
    return {"results": hits[:80]}


class OrderPayload(BaseModel):
    entries: list[dict]


@app.get("/chain")
def get_chain():
    return {"order": _order(), "custom_order": chain.load() is not None}


@app.put("/chain", dependencies=[Depends(_from_margin)])
def put_chain(payload: OrderPayload):
    try:
        chain.save(payload.entries)
    except chain.ChainProblem as e:
        raise HTTPException(status_code=400, detail=str(e))
    return get_chain()


@app.delete("/chain", dependencies=[Depends(_from_margin)])
def reset_chain():
    """Back to Margin's default order."""
    chain.reset()
    return get_chain()


class KeyPayload(BaseModel):
    name: str
    value: str


@app.get("/keys")
def key_status(names: str = ""):
    """Where each named key is set, never its value."""
    return {"keys": keys.status([n.strip() for n in names.split(",") if n.strip()][:300])}


@app.post("/keys", dependencies=[Depends(_from_margin)])
def save_key(payload: KeyPayload):
    try:
        keys.set_key(payload.name, payload.value)
    except keys.KeyProblem as e:
        raise HTTPException(status_code=400, detail=str(e))
    providers._live_cache.clear()
    return {"name": payload.name, "set_in": "margin"}


@app.delete("/keys/{name}", dependencies=[Depends(_from_margin)])
def delete_key(name: str):
    try:
        removed = keys.remove_key(name)
    except keys.KeyProblem as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": name, "removed": removed, "still_set_in": keys.status([name]).get(name)}


@app.get("/providers/{provider}/models")
def provider_models(provider: str):
    try:
        return {"models": providers.models_for(provider)}
    except providers.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001 -- the provider's own error, shown in the menu
        raise HTTPException(status_code=502, detail=f"{provider} did not list its models: {str(e)[:200]}")


@app.post("/providers/test")
def test_provider(choice: EngineChoice):
    return llm.test_choice(choice.as_dict())


# --- v1 routes kept: YouTube captions, flashcards, documents -------------------

@app.get("/transcript")
def transcript(video_id: str):
    try:
        return fetch_transcript(video_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"No transcript available: {e}")


class FlashcardRequest(BaseModel):
    filename: str
    tags: str = "margin"


def _vault_file(vault_path: Path, filename: str, suffix: str = ".md") -> Path:
    path = (vault_path / filename).resolve()
    if vault_path.resolve() not in path.parents or path.suffix != suffix:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return path


@app.post("/export/flashcards")
def export_flashcards(req: FlashcardRequest, vault_path: Path = Depends(get_vault_path)):
    path = _vault_file(vault_path, req.filename)
    content = path.read_text()
    fm = parse_frontmatter(content) or {}
    try:
        raw, _engine = llm.generate(build_flashcard_prompt(content, fm.get("title", path.stem)),
                                    max_tokens=8000, temperature=0.3)
    except llm.EngineError as e:
        raise HTTPException(status_code=502, detail=str(e))
    cards = parse_flashcards(raw)
    if not cards:
        raise HTTPException(status_code=422, detail="The model returned no usable flashcards.")
    return {"count": len(cards), "tsv": cards_to_tsv(cards, tags=req.tags),
            "cards": [{"front": c.front, "back": c.back} for c in cards]}


@app.get("/documents")
def documents(vault_path: Path = Depends(get_vault_path)):
    return list_documents(vault_path)


@app.get("/documents/search")
def documents_search(q: str = "", vault_path: Path = Depends(get_vault_path)):
    return search_documents(vault_path, q)


@app.get("/documents/content")
def document_content(filename: str, vault_path: Path = Depends(get_vault_path)):
    return {"content": _vault_file(vault_path, filename).read_text()}

