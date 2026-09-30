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
import shutil
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
FINE_SIZE = (480, 270)  # the sharper second look before a capture is dropped (Session._part_of)
INK_DELTA = 40          # grey levels from the background that count as a mark
KEEP_TO_CONTAIN = 0.92  # share of the earlier ink that must survive
NEW_SPEECH_SECONDS = 3.0  # transcribed speech outside what was heard before that counts as new
BLANK_INK = 0.002       # less ink than this is an empty frame
# A capture is the same slide built further only if (almost) none of the
# earlier one's marks are gone. Measured against the whole image as well as
# the earlier ink: on a slide that is mostly a fixed illustration, a new title
# is a sliver of the ink but must still make a new slide.
LOST_OF_IMAGE = 0.0015
# The presenter (a face bubble, or a cut-out head that leans and gestures) is
# left out of every comparison. The browser marks the pixels that changed in
# recent samples: a patchy shape of whichever part of the head moved lately.
# Measured on a real lecture it covered 1% of the frame while the head moved
# over 8%, so every gesture looked like a new slide (100 captures of about 20
# screens, 44 of 2 slides). So each moving blob becomes its box with room to
# move in, and the lecture remembers where the presenter has been.
PRESENTER_PAD = 12          # thumbnail pixels of room around a moving blob
PRESENTER_MIN_PIXELS = 10   # fewer moving pixels than this is a stray speck, not a presenter
PRESENTER_MIN_SIDE = 5      # a blob thinner than this is a line being typed, not a presenter
PRESENTER_MAX = 0.3         # a moving area larger than this share of the frame is not a person

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


def _grow(mask: np.ndarray, steps: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(steps):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def _blobs(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (y0, y1, x0, x1) of the connected areas of a mask."""
    seen = np.zeros(mask.shape, dtype=bool)
    h, w = mask.shape
    boxes = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        seen[y, x] = True
        stack = [(y, x)]
        y0 = y1 = y
        x0 = x1 = x
        while stack:
            cy, cx = stack.pop()
            y0, y1, x0, x1 = min(y0, cy), max(y1, cy), min(x0, cx), max(x1, cx)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        boxes.append((int(y0), int(y1) + 1, int(x0), int(x1) + 1))
    return boxes


def presenter_area(moving: np.ndarray | None) -> np.ndarray | None:
    """Where the presenter can be: each moving blob's box, with room to move."""
    if moving is None or not moving.any():
        return None
    area = np.zeros(moving.shape, dtype=bool)
    for y0, y1, x0, x1 in _blobs(_grow(moving, 2)):
        ys, xs = np.nonzero(moving[y0:y1, x0:x1])
        if len(ys) < PRESENTER_MIN_PIXELS or np.ptp(ys) + 1 < PRESENTER_MIN_SIDE or np.ptp(xs) + 1 < PRESENTER_MIN_SIDE:
            continue
        pad = PRESENTER_PAD
        area[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = True
    return area if area.any() else None


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
            moving = self._presenter(frames_dir, moving)
            # Nearest in time first: the slide being built is almost always the last one.
            for frame in sorted(frames, key=lambda f: abs(f["t_last"] - t)):
                if abs(frame["t_last"] - t) > 900:
                    continue
                other = self._thumb(frame)
                if other is None:
                    continue
                inside = contains(other, thumb, moving)
                same = inside and contains(thumb, other, moving)
                if same or (inside and self._part_of(frame, image, moving)):
                    # Nothing the kept image lacks: the same slide seen again
                    # (a rewatch) or part of a build. Only its times can widen.
                    if same and (t < frame["t"] or t > frame["t_last"]):
                        frame["t"] = min(frame["t"], t)
                        frame["t_last"] = max(frame["t_last"], t)
                        frames.sort(key=lambda f: f["t"])
                        self._write("frames.json", frames)
                    return {**frame, "duplicate": True}
                if contains(thumb, other, moving):
                    (frames_dir / frame["file"]).write_bytes(jpeg)
                    np.save(frames_dir / f"{frame['id']}.npy", thumb.astype(np.uint8))
                    frame["t"] = min(frame["t"], t)
                    frame["t_last"] = max(frame["t_last"], t)
                    frame.update(w=image.width, h=image.height)
                    frames.sort(key=lambda f: f["t"])
                    self._write("frames.json", frames)
                    self._bump()  # a fuller version of a kept slide
                    return {**frame, "duplicate": True}
            # One past the highest id, not the count: after a clean-up removed
            # some, the count would hand out an id still in use.
            frame_id = f"S{max((int(f['id'][1:]) for f in frames), default=0) + 1:03d}"
            record = {"id": frame_id, "t": t, "t_last": t, "file": f"{frame_id}.jpg",
                      "w": image.width, "h": image.height}
            (frames_dir / record["file"]).write_bytes(jpeg)
            np.save(frames_dir / f"{frame_id}.npy", thumb.astype(np.uint8))
            frames.append(record)
            frames.sort(key=lambda f: f["t"])
            self._write("frames.json", frames)
            self._bump()
            return {**record, "duplicate": False}

    def _part_of(self, frame: dict, image: Image.Image, moving: np.ndarray | None) -> bool:
        """A second, sharper look before a capture is dropped as part of a
        kept slide. In the small picture text blurs into bars, so a new,
        shorter title over the same illustration can sit inside the old
        title's bar and pass for part of that slide. At three times the size
        the letters are letters. Only asked in that case, so it costs one
        image read now and then."""
        path = self.root / "frames" / frame["file"]
        if not path.exists():
            return True
        kept = np.asarray(Image.open(path).convert("RGB").resize(FINE_SIZE, Image.BILINEAR), dtype=np.int16)
        new = np.asarray(image.convert("RGB").resize(FINE_SIZE, Image.BILINEAR), dtype=np.int16)
        ignore = None
        if moving is not None:
            ignore = np.asarray(Image.fromarray(moving.astype(np.uint8) * 255).resize(FINE_SIZE, Image.NEAREST)) > 127
        return contains(kept, new, ignore)

    def _presenter(self, frames_dir: Path, moving: np.ndarray | None) -> np.ndarray | None:
        """What to leave out of comparisons: where the presenter moves now,
        and everywhere they were seen moving in at least two captures of this
        lecture. Two, so a one-off (a scroll, a menu opening) is not ignored
        for the rest of the lecture while the presenter, in nearly every
        capture, is remembered at once."""
        path = frames_dir / "presenter.npy"
        seen = np.load(path) if path.exists() else None  # per pixel: in how many captures
        if seen is None and (frames_dir / "moving.npy").exists():
            old = presenter_area(np.load(frames_dir / "moving.npy"))  # an older Margin kept the last one only
            seen = None if old is None else old.astype(np.uint8) * 2
        area = presenter_area(moving)
        if area is not None and area.mean() > PRESENTER_MAX:
            area = None  # far bigger than a person: a scroll or a video clip
        if area is not None:
            seen = area.astype(np.uint8) if seen is None else np.minimum(seen.astype(np.int32) + area, 255).astype(np.uint8)
            np.save(path, seen)
        known = None if seen is None else seen >= 2
        if area is not None:
            known = area if known is None else known | area
        return known if known is not None and known.any() else None

    def _thumb(self, frame: dict) -> np.ndarray | None:
        cached = self.root / "frames" / f"{frame['id']}.npy"
        if cached.exists():
            thumb = np.load(cached)
            if thumb.ndim == 3:
                return thumb.astype(np.int16)  # stored as bytes; compared as signed numbers
        path = self.root / "frames" / frame["file"]
        if not path.exists():
            return None
        thumb = grey_thumb(Image.open(path))  # grey from an older Margin: redone in colour
        np.save(cached, thumb.astype(np.uint8))
        return thumb

    def compact_frames(self, apply: bool = False) -> dict:
        """Fold a lecture's captures together again with today's rules, for
        one captured before them. Every kept capture is replayed, in time
        order, into a scratch copy; with `apply` the copy replaces the frames.
        Notes already written keep their pictures: those were copied into the
        vault when the note was written."""
        frames_dir = self.root / "frames"
        scratch_root = self.root / ".compact"
        with _lock_for(self.key):
            frames = sorted(self.frames, key=lambda f: f["t"])
            before = sum(p.stat().st_size for p in frames_dir.glob("S*")) if frames_dir.exists() else 0
            shutil.rmtree(scratch_root, ignore_errors=True)
            (scratch_root / "frames").mkdir(parents=True)
            for name in ("presenter.npy", "moving.npy"):
                if (frames_dir / name).exists():
                    shutil.copyfile(frames_dir / name, scratch_root / "frames" / name)
            scratch = Session(key=f"{self.key}.compact", root=scratch_root)
            for frame in frames:
                path = frames_dir / frame["file"]
                if path.exists():
                    jpeg = path.read_bytes()
                    for t in sorted({frame["t"], frame["t_last"]}):
                        scratch.add_frame(t, jpeg)
            kept = scratch.frames
            after = sum(p.stat().st_size for p in (scratch_root / "frames").glob("S*"))
            if apply and len(kept) < len(frames):
                # Numbered after the old ids, never reusing one: a note already
                # written has its pictures in the vault under the old ids, and a
                # later rewrite must not put a different slide in their place.
                offset = max(int(f["id"][1:]) for f in frames)
                for frame in kept:
                    new = f"S{int(frame['id'][1:]) + offset:03d}"
                    for ext in (".jpg", ".npy"):
                        src = scratch_root / "frames" / f"{frame['id']}{ext}"
                        if src.exists():
                            src.rename(scratch_root / "frames" / f"{new}{ext}")
                    frame.update(id=new, file=f"{new}.jpg")
                old = self.root / ".frames-old"
                shutil.rmtree(old, ignore_errors=True)
                frames_dir.rename(old)
                (scratch_root / "frames").rename(frames_dir)
                self._write("frames.json", kept)
                shutil.rmtree(old)
            shutil.rmtree(scratch_root, ignore_errors=True)
        return {"key": self.key, "title": self.meta.get("lecture_title"), "before": len(frames),
                "after": len(kept), "bytes_before": before, "bytes_after": after}

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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fold captured slides together again with the current rules.")
    parser.add_argument("--apply", action="store_true", help="replace the frames (without it, only report)")
    args = parser.parse_args()
    for session in all_sessions():
        r = session.compact_frames(apply=args.apply)
        if r["before"]:
            print(f"{r['before']:4d} -> {r['after']:3d} slides  {r['bytes_before'] / 1e6:5.1f} -> {r['bytes_after'] / 1e6:4.1f} MB  {r['title']}")
