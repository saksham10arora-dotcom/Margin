"""Build a small but real lecture video for the end-to-end test.

Four slides about a portfolio's expected return and risk, each spoken aloud
by macOS `say`, the bullets appearing one at a time the way a real slide
deck builds up, plus a WebVTT caption track timed to the narration. The
result is an mp4 that behaves like a Udemy lecture as far as Margin can tell:
slides that change and build, speech, and captions (or none, with
--no-captions, to exercise the Whisper path).

    python scripts/e2e/make_lecture.py OUT_DIR [--no-captions] [--demo]

--demo builds the README's lecture instead (How RAG works, see DEMO).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720

SLIDES = [
    {
        "title": "Expected return of a portfolio",
        "bullets": ["A portfolio splits money across assets",
                    "Each asset has an expected return",
                    "The portfolio's return is their weighted average"],
        "say": ["Today we calculate the expected return of a portfolio.",
                "A portfolio simply splits our money across several assets, for example two stocks.",
                "Each asset has its own expected return, which we estimate from historical data.",
                "The expected return of the whole portfolio is the weighted average of those returns, "
                "where the weights are the fractions of money in each asset."],
    },
    {
        "title": "The formula",
        "bullets": ["mu_p = w1*mu1 + w2*mu2", "Weights sum to one: w1 + w2 = 1",
                    "Example: 60% at 8%, 40% at 12% gives 9.6%"],
        "say": ["Written as a formula, mu p equals w one times mu one plus w two times mu two.",
                "The weights must sum to one, because together they are all of our money.",
                "For example, sixty percent in an asset returning eight percent, and forty percent in one "
                "returning twelve percent, gives an expected return of nine point six percent."],
    },
    {
        "title": "Risk is not a weighted average",
        "bullets": ["Variance depends on covariance", "Diversification lowers risk",
                    "Returns average, risk does not"],
        "say": ["Be careful: risk does not work the same way.",
                "The variance of the portfolio depends on the covariance between the assets, not just their "
                "own variances.",
                "When assets do not move together, combining them lowers the risk. This is diversification.",
                "So returns average out, but risk does not."],
    },
    {
        "title": "In Python",
        "bullets": ["weights = np.array([0.6, 0.4])", "means = np.array([0.08, 0.12])",
                    "expected = weights @ means"],
        "say": ["In Python we store the weights and the mean returns as numpy arrays.",
                "The weights are zero point six and zero point four, the means eight and twelve percent.",
                "The expected return is simply the dot product of the two arrays."],
    },
]


# --demo: the lecture the README's screenshots and GIF are made from. Our own,
# so nobody's course is in them: an AI-engineering topic with every kind of
# slide Margin handles (bullets that build, a diagram, a formula, code).
DEMO_TITLE = "How RAG works"
DEMO_COURSE = "AI Engineering Basics"
DEMO = [
    {
        "kind": "bullets",
        "title": "Why a model needs your documents",
        "bullets": ["A model only knows what it was trained on",
                    "Your documents are private, or newer than it",
                    "So put the relevant parts in the prompt"],
        "say": ["Let's look at how retrieval augmented generation works, usually called RAG.",
                "A language model only knows what was in its training data.",
                "Your company's documents are private, or newer than the model, so it has never seen them.",
                "The fix is simple. Find the parts of your documents that matter for a question, "
                "and put them in the prompt."],
    },
    {
        "kind": "diagram",
        "title": "The RAG pipeline",
        "say": ["Here is the whole pipeline, in two parts.",
                "Ahead of time, we split the documents into chunks, turn each chunk into an embedding, "
                "and keep them in a vector store.",
                "When a question comes in, we embed it the same way, and search the store for the closest chunks.",
                "Then the top chunks and the question go to the model together, and it answers from them."],
    },
    {
        "kind": "bullets",
        "title": "Chunking",
        "bullets": ["Split documents into pieces of about 500 tokens",
                    "Overlap them a little, so no idea is cut in half",
                    "Keep each chunk's source, to cite it later"],
        "say": ["First, chunking.",
                "We split each document into pieces of around five hundred tokens.",
                "The pieces overlap a little, so a sentence on a boundary is not cut in half.",
                "And we keep a note of where each chunk came from, so the answer can cite its source."],
    },
    {
        "kind": "formula",
        "title": "Finding the closest chunks",
        "formula": "similarity(q, d) = q · d / (|q| |d|)",
        "bullets": ["1 means the same direction, 0 means unrelated",
                    "Compare the question with every chunk",
                    "Keep the top few, usually 3 to 5"],
        "say": ["To find the closest chunks we use cosine similarity: the dot product of the two vectors, "
                "divided by their lengths.",
                "A score of one means they point the same way. Zero means they are unrelated.",
                "We compare the question's embedding with every chunk's embedding.",
                "And we keep the top few, usually three to five, for the prompt."],
    },
    {
        "kind": "code",
        "title": "In Python",
        "code": ['import numpy as np',
                 '',
                 'chunks = ["Refunds take 5 days",',
                 '          "Shipping is free over $50",',
                 '          "Support is open 9 to 5"]',
                 'vectors = np.array([[0.9, 0.1, 0.0],',
                 '                    [0.1, 0.8, 0.2],',
                 '                    [0.0, 0.2, 0.9]])',
                 'question = np.array([0.8, 0.2, 0.1])  # "How long do refunds take?"',
                 '',
                 'norms = np.linalg.norm(vectors, axis=1) * np.linalg.norm(question)',
                 'scores = vectors @ question / norms',
                 'print(chunks[scores.argmax()])'],
        "reveal": [8, 9, 13],  # lines shown after each sentence
        "say": ["Here it is in Python, with three tiny chunks and made up embeddings of three numbers each.",
                "The question, how long do refunds take, gets an embedding too.",
                "We divide the dot products by the lengths, and print the chunk with the highest score. "
                "It finds the one about refunds."],
    },
]


def font(size: int, bold: bool = False):
    names = (["/System/Library/Fonts/Supplemental/Arial Bold.ttf"] if bold else []) + [
        "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_slide(slide: dict, shown: int, path: Path) -> None:
    img = Image.new("RGB", (W, H), (250, 250, 247))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=(214, 72, 98))
    d.text((80, 70), slide["title"], fill=(30, 60, 120), font=font(56, bold=True))
    d.line([80, 150, W - 80, 150], fill=(200, 200, 200), width=2)
    for i, bullet in enumerate(slide["bullets"][:shown]):
        y = 200 + i * 110
        d.ellipse([90, y + 16, 104, y + 30], fill=(214, 72, 98))
        d.text((130, y), bullet, fill=(40, 40, 40), font=font(40))
    d.text((W - 260, H - 50), "Margin test lecture", fill=(170, 170, 170), font=font(22))
    img.save(path)


INK, ACCENT, MUTED = (28, 32, 48), (79, 70, 229), (120, 124, 140)


def mono(size: int):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", size)
    except OSError:
        return ImageFont.load_default()


def _arrow(d: ImageDraw.ImageDraw, a: tuple, b: tuple) -> None:
    d.line([a, b], fill=MUTED, width=3)
    (x0, y0), (x1, y1) = a, b
    if x0 == x1:  # down
        d.polygon([(x1 - 8, y1 - 12), (x1 + 8, y1 - 12), (x1, y1)], fill=MUTED)
    else:  # right
        d.polygon([(x1 - 12, y1 - 8), (x1 - 12, y1 + 8), (x1, y1)], fill=MUTED)


def _code_line(d: ImageDraw.ImageDraw, x: int, y: int, line: str, f) -> None:
    """A little colour, the way an editor would: strings, numbers, comments, keywords."""
    import re as _re
    colours = {"str": (152, 195, 121), "num": (209, 154, 102), "com": (110, 118, 135),
               "kw": (198, 120, 221), "txt": (220, 223, 228)}
    for m in _re.finditer(r'(#.*$)|("[^"]*")|(\b\d+(?:\.\d+)?\b)|(\b(?:import|as|print)\b)|([^"#\d]+?(?=["#\d]|\bimport\b|\bas\b|\bprint\b|$))|(.)', line):
        kind = "com" if m.group(1) else "str" if m.group(2) else "num" if m.group(3) else "kw" if m.group(4) else "txt"
        text = m.group(0)
        d.text((x, y), text, fill=colours[kind], font=f)
        x += d.textlength(text, font=f)


def draw_demo(slide: dict, shown: int, number: int, path: Path) -> None:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((80, 64), slide["title"], fill=INK, font=font(52, bold=True))
    d.rectangle([80, 138, 160, 142], fill=ACCENT)
    kind = slide["kind"]
    if kind == "bullets":
        for i, bullet in enumerate(slide["bullets"][:shown]):
            y = 200 + i * 105
            d.rounded_rectangle([82, y + 14, 96, y + 28], radius=3, fill=ACCENT)
            d.text((124, y), bullet, fill=(52, 56, 72), font=font(38))
    elif kind == "formula":
        f = font(46)
        w = d.textlength(slide["formula"], font=f)
        d.rounded_rectangle([80, 185, W - 80, 295], radius=14, fill=(244, 244, 252))
        d.text(((W - w) / 2, 214), slide["formula"], fill=INK, font=f)
        for i, bullet in enumerate(slide["bullets"][:shown]):
            y = 345 + i * 88
            d.rounded_rectangle([82, y + 13, 96, y + 27], radius=3, fill=ACCENT)
            d.text((124, y), bullet, fill=(52, 56, 72), font=font(34))
    elif kind == "diagram":
        bw, bh, gap, x0 = 190, 78, 45, 75
        col = lambda i: x0 + i * (bw + gap)
        top, bottom = 250, 470
        f, small = font(26, bold=True), font(22)

        def box(i, y, label, sub=None, strong=False):
            d.rounded_rectangle([col(i), y, col(i) + bw, y + bh], radius=12,
                                fill=(238, 237, 253) if strong else (246, 246, 250),
                                outline=ACCENT if strong else (206, 208, 220), width=2)
            tw = d.textlength(label, font=f)
            d.text((col(i) + (bw - tw) / 2, y + (14 if sub else 24)), label, fill=INK, font=f)
            if sub:
                sw = d.textlength(sub, font=small)
                d.text((col(i) + (bw - sw) / 2, y + 46), sub, fill=MUTED, font=small)

        if shown >= 0:
            d.text((x0, top - 44), "Ahead of time", fill=MUTED, font=font(24))
            d.text((x0, bottom - 44), "For each question", fill=MUTED, font=font(24))
        if shown >= 1:
            box(0, top, "Documents")
            _arrow(d, (col(0) + bw, top + bh // 2), (col(1), top + bh // 2))
            box(1, top, "Chunks")
            _arrow(d, (col(1) + bw, top + bh // 2), (col(2), top + bh // 2))
            box(2, top, "Vector store", "embeddings")
        if shown >= 2:
            box(0, bottom, "Question")
            _arrow(d, (col(0) + bw, bottom + bh // 2), (col(1), bottom + bh // 2))
            box(1, bottom, "Embed")
            _arrow(d, (col(1) + bw, bottom + bh // 2), (col(2), bottom + bh // 2))
            box(2, bottom, "Search", "top chunks")
            _arrow(d, (col(2) + bw // 2, top + bh), (col(2) + bw // 2, bottom))
        if shown >= 3:
            _arrow(d, (col(2) + bw, bottom + bh // 2), (col(3), bottom + bh // 2))
            box(3, bottom, "LLM", "chunks + question", strong=True)
            _arrow(d, (col(3) + bw, bottom + bh // 2), (col(4), bottom + bh // 2))
            box(4, bottom, "Answer")
    elif kind == "code":
        d.rounded_rectangle([80, 172, W - 80, 660], radius=16, fill=(30, 33, 41))
        f = mono(21)
        lines = slide["code"][:slide["reveal"][min(shown, len(slide["reveal"]) - 1)]]
        for i, line in enumerate(lines):
            _code_line(d, 116, 200 + i * 33, line, f)
    d.text((80, H - 52), f"{DEMO_COURSE} · {DEMO_TITLE}", fill=(160, 164, 178), font=font(20))
    d.text((W - 118, H - 52), f"{number} / {len(DEMO)}", fill=(160, 164, 178), font=font(20))
    img.save(path)


def say_to_wav(text: str, path: Path) -> float:
    aiff = path.with_suffix(".aiff")
    subprocess.run(["say", "-r", "185", "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "44100", "-ac", "1", str(path)],
                   check=True)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


def vtt_ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def main() -> None:
    out = Path(sys.argv[1])
    with_captions = "--no-captions" not in sys.argv
    demo = "--demo" in sys.argv
    slides = DEMO if demo else SLIDES
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        segments = []  # (image, wav, duration, text)
        n = 0
        for number, slide in enumerate(slides, 1):
            # First line with just the title, then one more bullet per line.
            for k, line in enumerate(slide["say"]):
                img = tmp / f"s{n:03d}.png"
                if demo:
                    draw_demo(slide, k, number, img)
                else:
                    draw_slide(slide, k, img)
                wav = tmp / f"s{n:03d}.wav"
                dur = say_to_wav(line, wav) + 0.6
                segments.append((img, wav, dur, line))
                n += 1

        concat_v = tmp / "v.txt"
        concat_v.write_text("".join(f"file '{img}'\nduration {dur:.3f}\n" for img, _w, dur, _t in segments)
                            + f"file '{segments[-1][0]}'\n")
        # Pad each clip of speech to its slot so audio and slides stay in step.
        padded = []
        for img, wav, dur, _t in segments:
            p = wav.with_name(wav.stem + "_pad.wav")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-af", f"apad=whole_dur={dur:.3f}",
                            str(p)], check=True)
            padded.append(p)
        concat_a = tmp / "a.txt"
        concat_a.write_text("".join(f"file '{p}'\n" for p in padded))

        video = out / "lecture.mp4"
        subprocess.run([
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_v),
            "-f", "concat", "-safe", "0", "-i", str(concat_a),
            "-vf", "fps=25,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(video),
        ], check=True)

    t = 0.0
    cues = []
    for _img, _wav, dur, text in segments:
        cues.append(f"{vtt_ts(t)} --> {vtt_ts(t + dur - 0.3)}\n{text}\n")
        t += dur
    (out / "lecture.vtt").write_text("WEBVTT\n\n" + "\n".join(cues))

    title, og_title = ((f"{DEMO_COURSE} - {DEMO_TITLE}", DEMO_TITLE) if demo
                       else ("Portfolio Theory 101 - Expected return of a portfolio", "Expected return of a portfolio"))
    track = '<track kind="captions" src="lecture.vtt" srclang="en" label="English" default>' if with_captions else ""
    (out / "index.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<meta property="og:title" content="{og_title}">
<style>body{{margin:0;background:#111;font-family:sans-serif}}video{{width:960px;display:block;margin:20px}}</style>
</head><body><video id="v" controls crossorigin="anonymous" src="lecture.mp4">{track}</video></body></html>""")
    print(json.dumps({"video": str(video), "duration": round(t, 1), "slides": len(slides),
                      "segments": len(segments), "captions": with_captions}))


if __name__ == "__main__":
    main()
