"""Build a small but real lecture video for the end-to-end test.

Four slides about a portfolio's expected return and risk, each spoken aloud
by macOS `say`, the bullets appearing one at a time the way a real slide
deck builds up, plus a WebVTT caption track timed to the narration. The
result is an mp4 that behaves like a Udemy lecture as far as Margin can tell:
slides that change and build, speech, and captions (or none, with
--no-captions, to exercise the Whisper path).

    python scripts/e2e/make_lecture.py OUT_DIR [--no-captions]
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
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        segments = []  # (image, wav, duration, text)
        n = 0
        for slide in SLIDES:
            # First line with just the title, then one more bullet per line.
            for k, line in enumerate(slide["say"]):
                img = tmp / f"s{n:03d}.png"
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

    track = '<track kind="captions" src="lecture.vtt" srclang="en" label="English" default>' if with_captions else ""
    (out / "index.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Portfolio Theory 101 - Expected return of a portfolio</title>
<meta property="og:title" content="Expected return of a portfolio">
<style>body{{margin:0;background:#111;font-family:sans-serif}}video{{width:960px;display:block;margin:20px}}</style>
</head><body><video id="v" controls crossorigin="anonymous" src="lecture.mp4">{track}</video></body></html>""")
    print(json.dumps({"video": str(video), "duration": round(t, 1), "slides": len(SLIDES),
                      "segments": len(segments), "captions": with_captions}))


if __name__ == "__main__":
    main()
