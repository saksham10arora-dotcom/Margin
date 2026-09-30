"""Speech to text for lectures that ship without captions.

The browser records the lecture's own audio as it plays and posts it here in
short segments. Two things make that audio awkward to transcribe, and both
are handled before Whisper ever hears it:

  Playback speed. Watched at 1.75x, the recording is sped-up speech, which
  Whisper transcribes noticeably worse. ffmpeg's `atempo` stretches it back to
  the speaker's real pace without changing pitch, and as a bonus the
  timestamps Whisper returns are then already in video time.

  Silence and music. Whisper fills them with "[BLANK_AUDIO]" or invented
  words. Bracketed tags are dropped (sessions._clean_cues), and the lecture
  title is passed as Whisper's prompt so domain vocabulary ("Markowitz",
  "covariance") is recognised instead of guessed.

Runs entirely on this machine via whisper.cpp. No audio leaves it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MODEL_SEARCH = [
    Path.home() / ".margin" / "models",
    Path("/opt/homebrew/share/whisper-cpp"),
]
# Best first. small.en is markedly better on technical vocabulary than base.en;
# base.en is what most people already have.
MODEL_PREFERENCE = ["ggml-large-v3-turbo.bin", "ggml-medium.en.bin", "ggml-small.en.bin",
                    "ggml-base.en.bin", "ggml-small.bin", "ggml-base.bin"]


def find_whisper_binary() -> str | None:
    configured = os.environ.get("MARGIN_WHISPER_BIN")
    if configured:
        return configured if Path(configured).exists() else None
    for name in ("whisper-cli", "whisper-cpp", "whisper"):
        found = shutil.which(name)
        if found:
            return found
    return None


def find_whisper_model() -> Path | None:
    configured = os.environ.get("MARGIN_WHISPER_MODEL")
    if configured:
        path = Path(configured).expanduser()
        return path if path.exists() else None
    for name in MODEL_PREFERENCE:
        for folder in MODEL_SEARCH:
            if (folder / name).exists():
                return folder / name
    return None


def asr_available() -> dict:
    binary = find_whisper_binary()
    model = find_whisper_model()
    return {
        "available": bool(binary and model and shutil.which("ffmpeg")),
        "binary": binary,
        "model": model.name if model else None,
    }


def atempo_chain(rate: float) -> str:
    """ffmpeg filter that undoes a playback rate.

    To undo 1.75x the audio is slowed by 1/1.75. A single atempo accepts 0.5
    to 2.0 on older ffmpeg builds, so factors outside that range are split
    into a chain of steps that multiply to the target.
    """
    if rate <= 0 or abs(rate - 1.0) < 0.01:
        return ""
    factor = 1.0 / rate
    steps = []
    while factor < 0.5:
        steps.append(0.5)
        factor /= 0.5
    while factor > 2.0:
        steps.append(2.0)
        factor /= 2.0
    steps.append(factor)
    return ",".join(f"atempo={s:.4f}" for s in steps)


def transcribe(audio: bytes, rate: float = 1.0, prompt: str = "", suffix: str = ".webm") -> list[dict]:
    """Transcribe one recorded segment.

    Returns cues with start/end in seconds *relative to the segment's start*,
    already corrected for playback rate, so the caller only has to add the
    segment's starting video time.
    """
    binary = find_whisper_binary()
    model = find_whisper_model()
    if not binary or not model:
        raise RuntimeError("Local transcription unavailable: install whisper.cpp and a ggml model "
                           "(see README, 'No captions? Margin listens').")

    with tempfile.TemporaryDirectory(prefix="margin-asr-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / f"in{suffix}"
        src.write_bytes(audio)
        wav = tmp_dir / "speech.wav"
        filters = atempo_chain(rate)
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src)]
        if filters:
            cmd += ["-filter:a", filters]
        cmd += ["-ar", "16000", "-ac", "1", str(wav)]
        converted = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if converted.returncode != 0 or not wav.exists():
            raise RuntimeError(f"ffmpeg could not decode the audio segment: {converted.stderr[-300:]}")

        out_base = tmp_dir / "out"
        # -ml/-sow: at most about a sentence per cue, split on word boundaries.
        # Without it Whisper sometimes returns 30-second passages, which is
        # accurate text but useless for lining words up with slides.
        args = [binary, "-m", str(model), "-f", str(wav), "-oj", "-of", str(out_base), "-np",
                "-ml", "120", "-sow"]
        if prompt:
            args += ["--prompt", prompt[:220]]
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        out_json = out_base.with_suffix(".json")
        if result.returncode != 0 or not out_json.exists():
            raise RuntimeError(f"whisper failed: {result.stderr[-300:]}")

        data = json.loads(out_json.read_text(errors="replace"))

    cues = []
    for seg in data.get("transcription", []):
        offsets = seg.get("offsets", {})
        cues.append({
            "start": offsets.get("from", 0) / 1000.0,
            "end": offsets.get("to", 0) / 1000.0,
            "text": seg.get("text", "").strip(),
        })
    return cues
