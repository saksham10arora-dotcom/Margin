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
FINE_SIZE = (480, 270)  # the sharper second look at every match (_sharp)
INK_DELTA = 40          # grey levels from the background that count as a mark
KEEP_TO_CONTAIN = 0.92  # share of the earlier ink that must survive
NEW_SPEECH_SECONDS = 3.0  # transcribed speech outside what was heard before that counts as new
BLANK_INK = 0.002       # less ink than this is an empty frame
# What a note is written from: about two hours of speech. A longer lecture (a
# nine-hour one-shot) used to keep its first and last hour and drop the rest,
# whatever you had watched. Now what you watched goes in whole and the rest is
# thinned evenly, in stretches long enough to follow.
NOTE_TRANSCRIPT_CHARS = 120_000
STRETCH_SECONDS = 120   # the transcript is kept or left out in stretches this long
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
# A lecture written by hand on paper: the writer's hand is in most captures, a
# different part of the page each time, so every capture had "ink" the next one
# lacked and hid writing the last one had. A real 10 minute lecture kept 47
# captures of 3 pages. The hand is thick and skin-toned and comes in from the
# frame's edge, with a grey shadow beside it; writing is thin strokes. So the
# hand and its shadow are left out of a comparison, in either capture.
HAND_MIN_PIXELS = 60        # thumbnail pixels: smaller is a knuckle or a pen tip, not a hand
HAND_PAD = 6                # room around the hand
HAND_MIN_WRITING = 0.006    # a capture with a hand in it and less writing than this in view adds nothing
PEN_REACH = 20              # thumbnail pixels of ink followed out of the hand: the pen it holds
SHADE_SPREAD = 24           # max - min of R, G, B: grey enough to be the hand's shadow
# The page also slides a little as it is written on, and a moved page is a new
# picture pixel by pixel. When the plain comparison fails, the two captures are
# lined up by their writing and compared again. Writing that left the frame
# still counts as missing: a page scrolled far enough to hide lines keeps both.
MAX_SHIFT = 16              # thumbnail pixels either way (about a sixth of the height)
MORE_WRITING = 0.05         # a capture folded into a kept one replaces it with this much more writing
MORE_WRITING_MIN = 20       # and at least this many more thumbnail pixels of it
LOST_WORD = 20              # FINE_SIZE pixels missing in one cluster: a word, not specks of noise
REGIONS = (6, 4)            # columns and rows of FINE_SIZE regions, each lined up on its own
# A teacher in front of a projected slide: a whole person who walks across it,
# writes on it, and on a see-through board leaves a faint ghost behind the
# text. Where two captures of one slide differ because of them, the difference
# is solid and comes in from the frame's edge; a new slide differs in strokes
# (letters, lines) all over it. So a solid difference from the edge is left
# out, and slides are compared by their strokes: a person, a ghost, a shadow or
# an animated block is thick, and counts neither for nor against a slide.
IN_FRONT_MIN = 0.01         # share of the frame a solid difference must cover to be someone in front
IN_FRONT_MAX = 0.5          # beyond this it is not someone in front of the slide but a new one
MORE_PICTURE = 0.01         # share of the frame of new solid content (a picture appearing) that makes a capture fuller
MIN_STROKES = 0.006         # less text or writing than this and a capture is compared by its picture
REGION_SLACK = 2            # pixels either way a region may move: a page bending, not a new word

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


def subtract_ranges(ranges: list[list[float]], minus: list[list[float]], least: float = 5.0) -> list[list[float]]:
    """The parts of `ranges` outside `minus`, dropping slivers under `least` seconds."""
    out = []
    for start, end in merge_ranges(ranges):
        pieces = [[start, end]]
        for a, b in merge_ranges(minus):
            nxt = []
            for lo, hi in pieces:
                if b <= lo or a >= hi:
                    nxt.append([lo, hi])
                    continue
                if a > lo:
                    nxt.append([lo, a])
                if b < hi:
                    nxt.append([b, hi])
            pieces = nxt
        out.extend(p for p in pieces if p[1] - p[0] >= least)
    return out


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


def _shrink(mask: np.ndarray, steps: int) -> np.ndarray:
    return ~_grow(~mask, steps)


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


def _skin(thumb: np.ndarray) -> np.ndarray:
    """Skin-toned pixels: the usual chroma box in YCbCr, any brightness."""
    r, g, b = (thumb[..., i].astype(np.float32) for i in range(3))
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)


def strokes(thumb: np.ndarray) -> np.ndarray:
    """The thin marks of a capture: writing and text, not a hand, a shadow, a
    filled shape or the table's edge."""
    ink = ink_mask(thumb)
    return ink & ~_grow(_shrink(ink, 2), 3)


def hand_area(thumb: np.ndarray) -> np.ndarray | None:
    """Where a writer's hand is, with room for its pen and the shadow it casts."""
    if thumb.ndim != 3:
        return None
    ink = ink_mask(thumb)
    thick = _grow(_shrink(ink, 2), 2) & ink
    skin = _skin(thumb) & thick
    h, w = skin.shape
    hand = np.zeros(skin.shape, dtype=bool)
    for y0, y1, x0, x1 in _blobs(skin):
        part = skin[y0:y1, x0:x1]
        if part.sum() >= HAND_MIN_PIXELS and (y0 == 0 or x0 == 0 or y1 == h or x1 == w):
            hand[y0:y1, x0:x1] |= part
    if not hand.any():
        return None
    near = _grow(hand, 2)
    shade = ((thumb.max(axis=2) - thumb.min(axis=2)) <= SHADE_SPREAD) & thick & ~hand
    for y0, y1, x0, x1 in _blobs(shade):
        part = shade[y0:y1, x0:x1]
        if (part & near[y0:y1, x0:x1]).any():
            hand[y0:y1, x0:x1] |= part
    # The pen: ink that runs on out of the hand, followed for a short way. Its
    # tip is where the newest writing is, which the next capture shows anyway.
    for _ in range(PEN_REACH):
        hand = hand | (_grow(hand, 1) & ink)
    return _grow(hand, HAND_PAD)


def _either(*masks: np.ndarray | None) -> np.ndarray | None:
    present = [m for m in masks if m is not None]
    return np.logical_or.reduce(present) if present else None


def _shifted(image: np.ndarray, dy: int, dx: int, fill) -> np.ndarray:
    """`image` moved by (dy, dx); what comes in from beyond its edge is `fill`."""
    out = np.full(image.shape, fill, dtype=image.dtype)
    h, w = image.shape[:2]
    src_y, dst_y = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    src_x, dst_x = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[dst_y, dst_x] = image[src_y, src_x]
    return out


def best_shift(moving: np.ndarray, still: np.ndarray) -> tuple[int, int]:
    """The (dy, dx) that lays `moving`'s writing best over `still`'s, found by
    cross-correlating their thin strokes (a table edge or a shadow stays put
    while the page moves, and would pin the answer to no shift at all)."""
    h, w = still.shape[:2]
    size = (2 * h, 2 * w)
    a = np.fft.rfft2(strokes(moving).astype(np.float32), s=size)
    b = np.fft.rfft2(strokes(still).astype(np.float32), s=size)
    corr = np.fft.irfft2(b * np.conj(a), s=size)
    window = np.roll(np.roll(corr, MAX_SHIFT, 0), MAX_SHIFT, 1)[:2 * MAX_SHIFT + 1, :2 * MAX_SHIFT + 1]
    dy, dx = np.unravel_index(int(np.argmax(window)), window.shape)
    return int(dy) - MAX_SHIFT, int(dx) - MAX_SHIFT


def in_front(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    """Where something stands in front of the slide in one capture and not the
    other: solid differences coming in from the frame's edge, with what they
    hold or point with and their faint edges."""
    if a.shape != b.shape or a.ndim != 3:
        return None
    diff = _difference(a, b) >= INK_DELTA
    solid = _grow(_shrink(diff, 2), 2) & diff
    h, w = solid.shape
    area = np.zeros(solid.shape, dtype=bool)
    for y0, y1, x0, x1 in _blobs(solid):
        part = solid[y0:y1, x0:x1]
        if part.sum() >= IN_FRONT_MIN * solid.size and (y0 == 0 or x0 == 0 or y1 == h or x1 == w):
            area[y0:y1, x0:x1] |= part
    if not area.any() or area.mean() > IN_FRONT_MAX:
        return None
    for _ in range(PEN_REACH):
        area = area | (_grow(area, 1) & diff)
    return _grow(area, HAND_PAD)


def adds_picture(new: np.ndarray, kept: np.ndarray, front: np.ndarray | None) -> bool:
    """Does `new` show solid content `kept` lacks (a picture that appeared),
    not counting what stands in front? Slides are compared by their strokes,
    so this is what keeps a revealed picture in the slide's picture."""
    if new.shape != kept.shape or new.ndim != 3:
        return False
    ink = ink_mask(new)
    extra = ink & ~strokes(new) & (_difference(kept, new) >= INK_DELTA)
    if front is not None:
        extra &= ~front
    return extra.sum() > MORE_PICTURE * extra.size


def clearer(new: np.ndarray, kept: np.ndarray, front: np.ndarray | None) -> bool:
    """Is less of the slide covered in `new` where someone stood in front? The
    person is drawn in the capture they stand in; the other shows the slide."""
    if front is None or new.shape != kept.shape:
        return False
    covered_new = int((ink_mask(new) & front).sum())
    covered_kept = int((ink_mask(kept) & front).sum())
    return covered_new * 2 < covered_kept


def covers(bigger: np.ndarray, smaller: np.ndarray, moving: np.ndarray | None = None):
    """Does `bigger` show every stroke on `smaller` (its text, writing and
    lines), leaving out the presenter (`moving`), a writer's hand and whoever
    stands in front? None if not; else how the two were compared: (dy, dx,
    what stood in front), to look again at the same alignment."""
    hand_b = hand_area(bigger)
    front = _either(moving, hand_b, hand_area(smaller), in_front(bigger, smaller))
    if front is not None:
        # Not blank but hidden, by a hand, a face or a person: this says
        # nothing. Counted in writing, as what stays in view besides (a table's
        # edge, a shadow) is in every capture and would make any two pages alike.
        marks = strokes(smaller)
        if (marks & ~front).sum() < BLANK_INK * marks.size <= marks.sum():
            return None
    # Compared by its strokes when it has text or writing; a slide that is a
    # picture only is compared by its picture.
    marks = strokes(smaller)
    thick = ink_mask(smaller) & ~marks if marks.sum() >= MIN_STROKES * marks.size else None
    if contains(bigger, smaller, _either(front, thick)):
        return 0, 0, front
    if bigger.shape != smaller.shape or bigger.ndim != 3:
        return None
    dy, dx = best_shift(bigger, smaller)
    if (dy, dx) == (0, 0):
        return None
    moved = _shifted(bigger, dy, dx, -1000)  # beyond its edge nothing is shown, so nothing is contained
    ink = ink_mask(smaller)
    # A moved page: only its writing has to survive. What is thick (the table's
    # edge, a shadow, a black bar) stays in place while the page moves.
    front = _either(moving, hand_area(smaller), None if hand_b is None else _shifted(hand_b, dy, dx, False))
    skip = _either(front, thick)
    # Too little writing left to line up on: that says nothing, and an almost
    # empty capture must not pass for part of whatever page came next.
    if (ink if skip is None else ink & ~skip).sum() < BLANK_INK * ink.size:
        return None
    return (dy, dx, front) if contains(moved, smaller, skip) else None


def writing(thumb: np.ndarray) -> int:
    """How much of the page a capture shows: its thin marks, not under a hand."""
    if thumb.ndim != 3:
        return 0
    hand = hand_area(thumb)
    marks = strokes(thumb)
    return int((marks & ~hand).sum() if hand is not None else marks.sum())


def shows_more(new: np.ndarray, kept: np.ndarray) -> bool:
    """Clearly more writing, not the few pixels two captures of one slide
    differ by: a rewatch must not swap the picture and call the note stale."""
    a, b = writing(new), writing(kept)
    return a > b * (1 + MORE_WRITING) + MORE_WRITING_MIN


def _fine(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB").resize(FINE_SIZE, Image.BILINEAR), dtype=np.int16)


def _missing_by_region(strokes_: np.ndarray, there: np.ndarray) -> np.ndarray:
    """Which of `strokes_` have no ink of the other capture under them, each
    region of the picture lined up on its own within a couple of pixels:
    filmed paper bends and a camera refocuses, so one shift never fits the
    whole frame, while a slide lines up everywhere at once. Exact within a
    region, so a different word is still missing."""
    h, w = strokes_.shape
    r = REGION_SLACK
    padded = np.pad(there, r, constant_values=False)
    missing = np.zeros((h, w), dtype=bool)
    ys = np.linspace(0, h, REGIONS[1] + 1, dtype=int)
    xs = np.linspace(0, w, REGIONS[0] + 1, dtype=int)
    for y0, y1 in zip(ys[:-1], ys[1:]):
        for x0, x1 in zip(xs[:-1], xs[1:]):
            marks = strokes_[y0:y1, x0:x1]
            if not marks.any():
                continue
            best = None
            for a in range(-r, r + 1):
                for b in range(-r, r + 1):
                    lost = marks & ~padded[y0 + r - a:y1 + r - a, x0 + r - b:x1 + r - b]
                    n = int(lost.sum())
                    if best is None or n < best[0]:
                        best = (n, lost)
            missing[y0:y1, x0:x1] = best[1]
    return missing


def _word_sized(lost: np.ndarray) -> bool:
    """A word's worth missing in one place, not specks: noise is scattered, a
    title that changed is a cluster."""
    if lost.sum() < LOST_WORD:
        return False
    return any(lost[y0:y1, x0:x1].sum() >= LOST_WORD for y0, y1, x0, x1 in _blobs(_grow(lost, 1)))


def _sharp(bigger: np.ndarray, smaller: np.ndarray, match: tuple) -> bool:
    """`covers`, looked at again at FINE_SIZE, where letters are letters: is
    there ink of `bigger` under every stroke of `smaller` (each against its own
    background, so a ghost or a lit presenter that changes the brightness of
    text still there does not count), with what stood in front left out, each
    region lined up on its own, and a word-sized loss counted as a loss."""
    dy, dx, front = match
    marks = strokes(smaller)
    if marks.sum() < MIN_STROKES * marks.size:
        marks = ink_mask(smaller)  # a picture-only slide: its picture is what must be there
    if front is not None:
        marks &= ~(np.asarray(Image.fromarray(front.astype(np.uint8) * 255).resize(FINE_SIZE, Image.NEAREST)) > 127)
    total = int(marks.sum())
    if total < BLANK_INK * marks.size:
        return True
    scale = FINE_SIZE[0] // THUMB_SIZE[0]
    there = ink_mask(bigger)
    if dy or dx:
        there = _shifted(there, dy * scale, dx * scale, False)
    missing = _missing_by_region(marks, there)
    lost = int(missing.sum())
    return lost <= (1 - KEEP_TO_CONTAIN) * total and lost <= LOST_OF_IMAGE * marks.size and not _word_sized(missing)


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

    def transcript_text(self, every: float = 30.0, cues: list[dict] | None = None) -> str:
        """Cue text with a [mm:ss] marker roughly every `every` seconds, so the
        composer can place timestamps without drowning in one per sentence."""
        out: list[str] = []
        next_marker = 0.0
        for cue in self.cues if cues is None else cues:
            if cue["start"] >= next_marker:
                out.append(f"\n[{format_ts(cue['start'])}] ")
                next_marker = cue["start"] + every
            out.append(cue["text"] + " ")
        return "".join(out).strip()

    def _stretches(self) -> list[tuple[float, list[dict], bool]]:
        """The transcript in STRETCH_SECONDS pieces: (start, cues, watched)."""
        watched = self.watched
        pieces: dict[int, list[dict]] = {}
        for cue in self.cues:
            pieces.setdefault(int(cue["start"] // STRETCH_SECONDS), []).append(cue)
        out = []
        for i in sorted(pieces):
            a, b = i * STRETCH_SECONDS, (i + 1) * STRETCH_SECONDS
            seen = any(lo < b and hi > a for lo, hi in watched)
            out.append((a, pieces[i], seen))
        return out

    def transcript_for_note(self, limit: int = NOTE_TRANSCRIPT_CHARS) -> str:
        """The transcript a note is written from, about `limit` characters at
        most. Whole when it fits. When it does not (a nine-hour one-shot), what
        you watched goes in whole and the rest is thinned evenly: whole
        stretches spread across the lecture, so the note covers what you
        watched and still sees how the lecture fits together. More than
        `limit` watched: that is thinned evenly too."""
        full = self.transcript_text()
        if len(full) <= limit:
            return full
        pieces = [(a, cues, seen, len(self.transcript_text(cues=cues))) for a, cues, seen in self._stretches()]
        seen_chars = sum(n for *_, seen, n in pieces if seen)
        keep = set()
        if seen_chars >= limit:
            step = -(-seen_chars // limit)  # ceil
            keep = {i for j, i in enumerate(i for i, p in enumerate(pieces) if p[2]) if j % step == 0}
        else:
            keep = {i for i, p in enumerate(pieces) if p[2]}
            rest = [i for i, p in enumerate(pieces) if not p[2]]
            rest_chars = sum(pieces[i][3] for i in rest)
            if rest:
                step = max(1, -(-rest_chars // (limit - seen_chars)))
                keep |= {i for j, i in enumerate(rest) if j % step == step // 2}
        out, gap_from = [], None
        for i, (a, cues, seen, _) in enumerate(pieces):
            if i in keep:
                if gap_from is not None:
                    out.append(f"[... {format_ts(gap_from)} to {format_ts(a)} left out ...]")
                    gap_from = None
                out.append(self.transcript_text(cues=cues))
            elif gap_from is None:
                gap_from = a
        if gap_from is not None:
            out.append(f"[... {format_ts(gap_from)} to the end left out ...]")
        return "\n".join(out)

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
            if frames and hand_area(thumb) is not None and writing(thumb) < HAND_MIN_WRITING * thumb[..., 0].size:
                # The hand over the page and hardly anything written to see: adds nothing.
                nearest = min(frames, key=lambda f: abs(f["t_last"] - t))
                return {**nearest, "duplicate": True, "kept": False}
            new_fine = None
            # Nearest in time first: the slide being built is almost always the last one.
            for frame in sorted(frames, key=lambda f: abs(f["t_last"] - t)):
                if abs(frame["t_last"] - t) > 900:
                    continue
                other = self._thumb(frame)
                if other is None:
                    continue
                inside = covers(other, thumb, moving)   # the kept picture shows all of this one
                outside = covers(thumb, other, moving)  # this one shows all of the kept picture
                if inside is None and outside is None:
                    continue
                # In the small picture text blurs into bars, and a different
                # line of about the same length covers another's bar: "Risk"
                # passed for "Rain", and a title-only slide for the next one.
                # So what it says is checked at three times the size, where
                # letters are letters. Only for a capture that matched.
                kept_fine = self._fine(frame)
                if kept_fine is not None:
                    if new_fine is None:
                        new_fine = _fine(image)
                    inside = inside if inside is not None and _sharp(kept_fine, new_fine, inside) else None
                    outside = outside if outside is not None and _sharp(new_fine, kept_fine, outside) else None
                if inside is not None:
                    # Nothing the kept picture lacks: the same slide seen again
                    # (a rewatch) or part of a build. Unless this one shows more
                    # of it: around a writer's hand nothing counts, so the newest
                    # line, still under the pen, or a page no longer behind the
                    # hand, arrives this way. Then it is the better picture.
                    if shows_more(thumb, other) or adds_picture(thumb, other, inside[2]) or clearer(thumb, other, inside[2]):
                        return self._replace(frames, frame, t, jpeg, image, thumb)
                    if outside is not None and (t < frame["t"] or t > frame["t_last"]):
                        frame["t"] = min(frame["t"], t)
                        frame["t_last"] = max(frame["t_last"], t)
                        frames.sort(key=lambda f: f["t"])
                        self._write("frames.json", frames)
                    return {**frame, "duplicate": True, "kept": False}
                if outside is not None:
                    if shows_more(other, thumb):
                        # Everything the kept picture shows, but less of the page:
                        # the hand is in the way. The kept picture stays.
                        return {**frame, "duplicate": True, "kept": False}
                    return self._replace(frames, frame, t, jpeg, image, thumb)
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
            return {**record, "duplicate": False, "kept": True}

    def _replace(self, frames: list[dict], frame: dict, t: float, jpeg: bytes,
                 image: Image.Image, thumb: np.ndarray) -> dict:
        """A fuller picture of a kept slide takes its place (and its id)."""
        frames_dir = self.root / "frames"
        (frames_dir / frame["file"]).write_bytes(jpeg)
        np.save(frames_dir / f"{frame['id']}.npy", thumb.astype(np.uint8))
        frame["t"] = min(frame["t"], t)
        frame["t_last"] = max(frame["t_last"], t)
        frame.update(w=image.width, h=image.height)
        frames.sort(key=lambda f: f["t"])
        self._write("frames.json", frames)
        self._bump()  # a fuller version of a kept slide
        return {**frame, "duplicate": True, "kept": True}

    def _fine(self, frame: dict) -> np.ndarray | None:
        path = self.root / "frames" / frame["file"]
        return _fine(Image.open(path)) if path.exists() else None

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

    # --- study tools: the crux and flashcards, kept with the lecture -------------

    def study(self, name: str) -> dict | None:
        """What was made to study this lecture ("crux", "cards"), or None."""
        return self._read(f"{name}.json", None)

    def save_study(self, name: str, value: dict) -> None:
        with _lock_for(self.key):
            self._write(f"{name}.json", {**value, "made": time.time()})

    # --- your own notes (see mine.py) ------------------------------------------

    @property
    def mine(self) -> list[dict]:
        """What you typed, pasted or said yourself, oldest first."""
        return self._read("mine.json", {}).get("entries", [])

    def add_mine(self, text: str = "", quote: str | None = None, where: str | None = None,
                 t: float | None = None, images: list[tuple[bytes, str]] = (),
                 audio: tuple[bytes, str] | None = None, transcript: str | None = None) -> dict:
        """Keep a note of yours, with its pictures and voice. Its id is the
        moment it was made: never reused, and never the same as one from
        another page whose notes share a note file (same title, same site)."""
        with _lock_for(self.key):
            store = self._read("mine.json", {})
            entries = store.get("entries", [])
            stamp = max(int(time.time() * 1000), store.get("last", 0) + 1)
            entry_id = f"M{stamp:x}"
            folder = self.root / "mine"
            folder.mkdir(exist_ok=True)
            names = []
            for i, (data, ext) in enumerate(images, 1):
                names.append(f"{entry_id}-{i}.{ext}")
                (folder / names[-1]).write_bytes(data)
            audio_name = None
            if audio:
                audio_name = f"{entry_id}.{audio[1]}"
                (folder / audio_name).write_bytes(audio[0])
            entry = {"id": entry_id, "created": time.time(), "text": text, "quote": quote, "where": where,
                     "t": t, "images": names, "audio": audio_name, "transcript": transcript}
            self._write("mine.json", {"last": stamp, "entries": [*entries, entry]})
        return entry

    def remove_mine(self, entry_id: str) -> dict | None:
        with _lock_for(self.key):
            store = self._read("mine.json", {})
            entries = store.get("entries", [])
            gone = next((e for e in entries if e["id"] == entry_id), None)
            if gone is None:
                return None
            self._write("mine.json", {**store, "entries": [e for e in entries if e["id"] != entry_id]})
            for name in gone["images"] + ([gone["audio"]] if gone["audio"] else []):
                (self.root / "mine" / name).unlink(missing_ok=True)
        return gone

    def mine_file(self, name: str) -> bytes | None:
        if not re.fullmatch(r"M[0-9a-f]{3,16}(-\d+)?\.(png|jpg|gif|webp|webm|ogg|m4a|mp4)", name):
            return None
        path = self.root / "mine" / name
        return path.read_bytes() if path.exists() else None

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
            before = merge_ranges(ranges)
            ranges.append([start, end])
            merged = merge_ranges(ranges)
            self._write("watched.json", merged)
            # A lecture too long for its note to hold whole is written from what
            # you watched: a part watched for the first time makes it out of date.
            if self._new_stretch(before, merged) and len(self.transcript_text()) > NOTE_TRANSCRIPT_CHARS:
                self._bump()
            return merged

    @staticmethod
    def _new_stretch(before: list[list[float]], after: list[list[float]]) -> bool:
        def stretches(ranges):
            return {i for lo, hi in ranges for i in range(int(lo // STRETCH_SECONDS), int(hi // STRETCH_SECONDS) + 1)
                    if min(hi, (i + 1) * STRETCH_SECONDS) - max(lo, i * STRETCH_SECONDS) >= 30}
        return bool(stretches(after) - stretches(before))

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

    def mark_composed(self, rev: int, watched: list[list[float]] | None = None,
                      frame_ids: list[str] | None = None) -> None:
        """The note now covers everything up to `rev`. What it was written from
        (the parts watched, the slides) is kept, so adding what's new later
        means writing only the parts and slides since."""
        with _lock_for(self.key):
            material = self._read("material.json", {})
            material["composed_rev"] = rev
            material["composed_watched"] = self.watched if watched is None else watched
            material["composed_frames"] = [f["id"] for f in self.frames] if frame_ids is None else frame_ids
            self._write("material.json", material)

    def new_since_note(self) -> tuple[list[list[float]], list[dict]]:
        """What the note does not have yet: parts watched since it was written,
        and slides kept since. ([], []) when nothing, or when Margin does not know
        what the note was written from (a note from before 2.10.5)."""
        material = self._read("material.json", {})
        if "composed_watched" not in material:
            return [], []
        known = set(material.get("composed_frames") or [])
        new_frames = [f for f in self.frames if f["id"] not in known]
        # Newly watched minutes are new material only where the note could not
        # have them: speech Margin heard itself (no captions), or a lecture too
        # long for its note to hold whole. A shorter captioned lecture's note
        # was written from all of its captions already.
        heard = self.transcript_source == "asr"
        if not (heard or len(self.transcript_text()) > NOTE_TRANSCRIPT_CHARS):
            return [], new_frames
        return subtract_ranges(self.watched, material["composed_watched"]), new_frames

    @property
    def knows_what_note_covers(self) -> bool:
        return "composed_watched" in self._read("material.json", {})

    @property
    def stale(self) -> bool:
        """The note lacks something: material arrived since it was written, or
        (when Margin knows what it was written from) parts or slides it does
        not cover."""
        material = self._read("material.json", {})
        if material.get("rev", 0) > material.get("composed_rev", 0):
            return True
        if "composed_watched" in material:
            ranges, frames = self.new_since_note()
            return bool(ranges or frames)
        return False

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
