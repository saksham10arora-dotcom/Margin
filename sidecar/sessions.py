"""Everything captured while a lecture plays, kept on disk until it is composed.

v1 had no memory of a lecture at all. Each 60-second chunk was sent, turned
into a section and forgotten, which is why a finished note could never be
better than its choppiest chunk, and why stopping and coming back later
started from nothing.

A session is one lecture. It accumulates what the browser saw and heard, in
video time, from however many sittings it takes:

    ~/.margin/sessions/<key>/
        meta.json      platform, course, section, lecture, url, duration
        cues.json      transcript cues [{start, end, text, source}]
        frames.json    [{id, t, t_last, file, hash}]
        frames/S001.jpg ...
        watched.json   merged [start, end] ranges actually played
        status.json    compose progress, for the panel to poll

Raw captures live outside the vault on purpose: they are working material,
not notes, and a vault full of half-finished capture folders is exactly the
clutter people install a notes tool to avoid.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

SESSIONS_ROOT = Path(os.environ.get("MARGIN_SESSIONS_PATH") or Path.home() / ".margin" / "sessions")

# "Is this the same slide?" is answered with ink, not with a perceptual hash.
# A phash of a white slide with thin text is dominated by the white, so two
# different slides from one template hash as near-identical and get merged,
# losing a slide. Ink asks the question that actually matters: is everything
# drawn on the earlier capture still there on the later one? If so the later
# one is the same slide with more on it (a bullet appeared, a line of code was
# typed), and it replaces the earlier one. A new slide redraws its title and
# body, so most of the earlier ink is gone.
THUMB_SIZE = (160, 90)
INK_DELTA = 40          # grey levels from the background that count as a mark
KEEP_TO_CONTAIN = 0.92  # share of the earlier ink that must survive
NEW_SPEECH_SECONDS = 3.0  # transcribed speech outside what was heard before that counts as new
BLANK_INK = 0.002       # less ink than this is an empty frame
# A capture is the same slide built further only if (almost) none of the
# earlier one's marks are gone. Measured against the whole image as well as
# the earlier ink: on a slide that is mostly a fixed illustration, a new title
# is a sliver of the ink but must still make a new slide.
LOST_OF_IMAGE = 0.0015

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def session_key(meta: dict) -> str:
    parts = [meta.get("platform") or "web", str(meta.get("course_id") or "solo"),
             str(meta.get("lecture_id") or meta.get("url") or "unknown")]
    raw = "-".join(parts)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)[:120]


def merge_ranges(ranges: list[list[float]], gap: float = 2.0) -> list[list[float]]:
    """Union of watched intervals. Small gaps are bridged: a 1-second hole
    between two samples is not a part of the lecture you skipped."""
    cleaned = sorted([float(a), float(b)] for a, b in ranges if b > a)
    merged: list[list[float]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def covered_seconds(ranges: list[list[float]]) -> float:
    return sum(b - a for a, b in merge_ranges(ranges))


def grey_thumb(image: Image.Image) -> np.ndarray:
    """The small picture slides are compared by. In colour: two slides of the
    same brightness in different colours are different slides."""
    return np.asarray(image.convert("RGB").resize(THUMB_SIZE, Image.BILINEAR), dtype=np.int16)


def _difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = np.abs(a - b)
    return d.max(axis=2) if d.ndim == 3 else d


def ink_mask(thumb: np.ndarray) -> np.ndarray:
    """Pixels that differ clearly from the background. The background is the
    most common colour, so this works for white slides, dark code editors and
    coloured templates alike."""
    if thumb.ndim == 2:  # a grey thumbnail cached by an older Margin
        counts = np.bincount((thumb // 8).ravel(), minlength=32)
        return np.abs(thumb - (int(np.argmax(counts)) * 8 + 4)) > INK_DELTA
    bins = (thumb // 32).reshape(-1, 3)
    codes = bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2]
    top = int(np.argmax(np.bincount(codes, minlength=512)))
    background = np.array([(top // 64) * 32 + 16, ((top // 8) % 8) * 32 + 16, (top % 8) * 32 + 16])
    return np.abs(thumb - background).max(axis=2) > INK_DELTA


def contains(bigger: np.ndarray, smaller: np.ndarray, ignore: np.ndarray | None = None) -> bool:
    """Does `bigger` still show everything drawn on `smaller`? `ignore` marks
    the area that never stops changing (a face bubble), left out of it."""
    if bigger.shape != smaller.shape:
        return False
    ink = ink_mask(smaller)
    if ignore is not None:
        ink &= ~ignore
    marks = int(ink.sum())
    if marks < BLANK_INK * ink.size:
        return True  # an empty frame is contained in anything
    lost = int(((_difference(bigger, smaller) >= INK_DELTA) & ink).sum())
    return lost / marks <= 1 - KEEP_TO_CONTAIN and lost / ink.size <= LOST_OF_IMAGE


def unpack_moving(packed: str | None) -> np.ndarray | None:
    """The browser's moving-area mask (64x36 bits, base64) at thumbnail size."""
    if not packed:
        return None
    try:
        bits = np.unpackbits(np.frombuffer(base64.b64decode(packed), dtype=np.uint8))[:64 * 36]
        if bits.size < 64 * 36:
            return None
        small = Image.fromarray((bits.reshape(36, 64) * 255).astype(np.uint8))
        return np.asarray(small.resize(THUMB_SIZE, Image.NEAREST)) > 127
    except (ValueError, TypeError):
        return None


def format_ts(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


@dataclass
class Session:
    key: str
    root: Path

    # --- small JSON helpers -------------------------------------------------

    def _read(self, name: str, default):
        path = self.root / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default

    def _write(self, name: str, value) -> None:
        path = self.root / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False))
        tmp.replace(path)  # atomic: a crash mid-write never leaves half a file

    # --- meta ---------------------------------------------------------------

    @property
    def meta(self) -> dict:
        return self._read("meta.json", {})

    def update_meta(self, meta: dict) -> None:
        with _lock_for(self.key):
            current = self._read("meta.json", {})
            # A later sighting can know more (the curriculum API answering
            # after the first request), never less: empty values do not
            # overwrite known ones.
            current.update({k: v for k, v in meta.items() if v not in (None, "", [])})
            self._write("meta.json", current)

    # --- transcript ---------------------------------------------------------

    @property
    def cues(self) -> list[dict]:
        return self._read("cues.json", [])

    def set_caption_cues(self, cues: list[dict], source: str) -> int:
        """Captions arrive whole, so they replace everything. They are always
        better than audio transcription of the same speech."""
        clean = _clean_cues(cues, source)
        with _lock_for(self.key):
            if clean != self._read("cues.json", []):
                self._write("cues.json", clean)
                self._bump()
        return len(clean)

    def add_asr_cues(self, cues: list[dict]) -> int:
        """Audio transcription arrives piecemeal and can overlap a range that
        was already transcribed (rewatching a part). The newest transcription
        of a span wins, since it was usually heard at a steadier speed."""
        new = _clean_cues(cues, "asr")
        if not new:
            return 0
        with _lock_for(self.key):
            existing = self._read("cues.json", [])
            if any(c.get("source") != "asr" for c in existing):
                return 0  # captions already cover this lecture
            lo = min(c["start"] for c in new)
            hi = max(c["end"] for c in new)
            kept = [c for c in existing if c["end"] <= lo or c["start"] >= hi]
            merged = sorted(kept + new, key=lambda c: c["start"])
            self._write("cues.json", merged)
            # Hearing a stretch again is not new material; speech from a part
            # never heard before is.
            before = covered_seconds([[c["start"], c["end"]] for c in existing])
            after = covered_seconds([[c["start"], c["end"]] for c in existing + new])
            if after - before >= NEW_SPEECH_SECONDS:
                self._bump()
        return len(new)

    @property
    def transcript_source(self) -> str | None:
        cues = self.cues
        return cues[0]["source"] if cues else None

    def transcript_text(self, every: float = 30.0) -> str:
        """Cue text with a [mm:ss] marker roughly every `every` seconds, so the
        composer can place timestamps without drowning in one per sentence."""
        out: list[str] = []
        next_marker = 0.0
        for cue in self.cues:
            if cue["start"] >= next_marker:
                out.append(f"\n[{format_ts(cue['start'])}] ")
                next_marker = cue["start"] + every
            out.append(cue["text"] + " ")
        return "".join(out).strip()

    # --- frames -------------------------------------------------------------

    @property
    def frames(self) -> list[dict]:
        return self._read("frames.json", [])

    def add_frame(self, t: float, jpeg: bytes, moving: np.ndarray | None = None) -> dict:
        """Store a captured slide, folding captures of the same slide together.

        The browser sends every settled screen, so a slide that builds up in
        three steps arrives three times. Each later step contains the earlier
        one's ink, so it replaces it: one slide, the fullest image, the time
        it first appeared. A capture that is only part of one already kept
        (seeking back into the middle of a build) is dropped.
        """
        image = Image.open(io.BytesIO(jpeg))
        image.load()
        thumb = grey_thumb(image)
        with _lock_for(self.key):
            frames = self._read("frames.json", [])
            frames_dir = self.root / "frames"
            frames_dir.mkdir(exist_ok=True)
            # The moving area the browser saw (a face bubble); remembered for
            # captures that arrive without one.
            if moving is not None:
                np.save(frames_dir / "moving.npy", moving)
            elif (frames_dir / "moving.npy").exists():
                moving = np.load(frames_dir / "moving.npy")
            frames_dir.mkdir(exist_ok=True)
            # Nearest in time first: the slide being built is almost always the last one.
            for frame in sorted(frames, key=lambda f: abs(f["t_last"] - t)):
                if abs(frame["t_last"] - t) > 900:
                    continue
                other = self._thumb(frame)
                if other is None:
                    continue
                if contains(other, thumb, moving):
                    # Nothing the kept image lacks: the same slide seen again
                    # (a rewatch) or part of a build. Only its times can widen.
                    if contains(thumb, other, moving) and (t < frame["t"] or t > frame["t_last"]):
                        frame["t"] = min(frame["t"], t)
                        frame["t_last"] = max(frame["t_last"], t)
                        frames.sort(key=lambda f: f["t"])
                        self._write("frames.json", frames)
                    return {**frame, "duplicate": True}
                if contains(thumb, other, moving):
                    (frames_dir / frame["file"]).write_bytes(jpeg)
                    np.save(frames_dir / f"{frame['id']}.npy", thumb)
                    frame["t"] = min(frame["t"], t)
                    frame["t_last"] = max(frame["t_last"], t)
                    frame.update(w=image.width, h=image.height)
                    frames.sort(key=lambda f: f["t"])
                    self._write("frames.json", frames)
                    self._bump()  # a fuller version of a kept slide
                    return {**frame, "duplicate": True}
            frame_id = f"S{len(frames) + 1:03d}"
            record = {"id": frame_id, "t": t, "t_last": t, "file": f"{frame_id}.jpg",
                      "w": image.width, "h": image.height}
            (frames_dir / record["file"]).write_bytes(jpeg)
            np.save(frames_dir / f"{frame_id}.npy", thumb)
            frames.append(record)
            frames.sort(key=lambda f: f["t"])
            self._write("frames.json", frames)
            self._bump()
            return {**record, "duplicate": False}

    def _thumb(self, frame: dict) -> np.ndarray | None:
        cached = self.root / "frames" / f"{frame['id']}.npy"
        if cached.exists():
            thumb = np.load(cached)
            if thumb.ndim == 3:
                return thumb
        path = self.root / "frames" / frame["file"]
        if not path.exists():
            return None
        thumb = grey_thumb(Image.open(path))  # grey from an older Margin: redone in colour
        np.save(cached, thumb)
        return thumb

    def frame_bytes(self, frame_id: str) -> bytes | None:
        for frame in self.frames:
            if frame["id"] == frame_id:
                path = self.root / "frames" / frame["file"]
                return path.read_bytes() if path.exists() else None
        return None

    # --- watched coverage ---------------------------------------------------

    def mark_watched(self, start: float, end: float) -> list[list[float]]:
        with _lock_for(self.key):
            ranges = self._read("watched.json", [])
            ranges.append([start, end])
            merged = merge_ranges(ranges)
            self._write("watched.json", merged)
            return merged

    @property
    def watched(self) -> list[list[float]]:
        return self._read("watched.json", [])

    def coverage(self) -> float:
        duration = self.meta.get("duration_sec") or 0
        if duration <= 0:
            return 0.0
        return min(1.0, covered_seconds(self.watched) / duration)

    # --- new material since the note -----------------------------------------
    #
    # A lecture keeps being captured after its note is written, so a rewatch
    # can fill in what the first watch missed. `rev` counts additions that
    # would change the note (a new slide, a fuller one, captions, speech from a
    # part not heard before); the note records the count it was written from.
    # Rewatching what is already captured adds nothing, so it never triggers
    # a rewrite.

    def _bump(self) -> None:
        """Count one addition. Callers hold the session lock."""
        material = self._read("material.json", {})
        material["rev"] = material.get("rev", 0) + 1
        self._write("material.json", material)

    @property
    def material_rev(self) -> int:
        return self._read("material.json", {}).get("rev", 0)

    def mark_composed(self, rev: int) -> None:
        with _lock_for(self.key):
            material = self._read("material.json", {})
            material["composed_rev"] = rev
            self._write("material.json", material)

    @property
    def stale(self) -> bool:
        """Material has arrived since the note was last written."""
        material = self._read("material.json", {})
        return material.get("rev", 0) > material.get("composed_rev", 0)

    # --- after a note is written ---------------------------------------------

    def _update_material(self, **changes) -> None:
        with _lock_for(self.key):
            material = self._read("material.json", {})
            for name, value in changes.items():
                if value is None:
                    material.pop(name, None)
                else:
                    material[name] = value
            self._write("material.json", material)

    def record_written(self, digest: str) -> None:
        """Fingerprint of the note as Margin wrote it: a note that no longer
        matches has been edited by you, and is not rewritten behind your back."""
        self._update_material(written_sha=digest)

    @property
    def written_sha(self) -> str | None:
        return self._read("material.json", {}).get("written_sha")

    def schedule_upgrade(self, at: float, tries: int = 0) -> None:
        """A Lite model wrote this note because the main ones were busy; try a
        full model again from `at`."""
        self._update_material(upgrade_at=at, upgrade_tries=tries)

    def clear_upgrade(self) -> None:
        self._update_material(upgrade_at=None, upgrade_tries=None)

    @property
    def upgrade(self) -> tuple[float, int] | None:
        material = self._read("material.json", {})
        if material.get("upgrade_at") is None:
            return None
        return float(material["upgrade_at"]), int(material.get("upgrade_tries", 0))

    # --- compose status -----------------------------------------------------

    @property
    def status(self) -> dict:
        return self._read("status.json", {"state": "idle"})

    def set_status(self, state: str, message: str = "", **extra) -> None:
        with _lock_for(self.key):
            current = self._read("status.json", {})
            current.update({"state": state, "message": message, "updated": time.time(), **extra})
            self._write("status.json", current)

    def replace_status(self, status: dict) -> None:
        """Put back an earlier status whole (an upgrade that could not run
        leaves the lecture showing the note it already has)."""
        with _lock_for(self.key):
            self._write("status.json", status)

    def summary(self) -> dict:
        """What the panel needs to draw its header in one request."""
        meta = self.meta
        cues = self.cues
        return {
            "key": self.key,
            "meta": meta,
            "cue_count": len(cues),
            "transcript_source": cues[0]["source"] if cues else None,
            "frame_count": len(self.frames),
            "frames": self.frames,
            "coverage": round(self.coverage(), 3),
            "watched": self.watched,
            "stale": self.stale,
            "status": self.status,
        }


def _clean_cues(cues: list[dict], source: str) -> list[dict]:
    out = []
    for cue in cues:
        text = re.sub(r"\s+", " ", str(cue.get("text", ""))).strip()
        # Whisper's "[BLANK_AUDIO]" / "(music)" and caption sound tags carry no
        # lecture content, and a note built from them reads like a subtitle file.
        if not text or re.fullmatch(r"[\[(].*[\])]", text):
            continue
        start = float(cue.get("start", 0))
        end = float(cue.get("end", start))
        out.append({"start": round(start, 2), "end": round(max(end, start), 2),
                    "text": text, "source": source})
    out.sort(key=lambda c: c["start"])
    return out


def open_session(meta: dict, root: Path | None = None) -> Session:
    root = root or SESSIONS_ROOT
    key = session_key(meta)
    folder = root / key
    folder.mkdir(parents=True, exist_ok=True)
    session = Session(key=key, root=folder)
    session.update_meta(meta)
    return session


def load_session(key: str, root: Path | None = None) -> Session | None:
    root = root or SESSIONS_ROOT
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        return None
    folder = root / key
    return Session(key=key, root=folder) if folder.is_dir() else None


def all_sessions(root: Path | None = None) -> list[Session]:
    root = root or SESSIONS_ROOT
    if not root.exists():
        return []
    return [Session(key=f.name, root=f) for f in sorted(root.iterdir()) if f.is_dir()]


def course_sessions(course_id: str, root: Path | None = None) -> list[Session]:
    root = root or SESSIONS_ROOT
    if not root.exists():
        return []
    found = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        session = Session(key=folder.name, root=folder)
        if str(session.meta.get("course_id")) == str(course_id):
            found.append(session)
    return found
