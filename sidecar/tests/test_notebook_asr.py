"""These tests use the real margin kernel and real whisper.cpp when present:
they are the parts where a mock would prove nothing."""
import shutil
import subprocess

import nbformat
import pytest

from sidecar import asr
from sidecar import notebook as NB

needs_kernel = pytest.mark.skipif(not NB.kernel_available(), reason="margin kernel not installed")


# --- notebook -------------------------------------------------------------------

@needs_kernel
def test_a_working_section_runs_and_yields_its_plot(tmp_path):
    cells = [
        {"type": "markdown", "source": "## 1 · Demo"},
        {"type": "code", "source": "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.show()\nprint('ran')"},
    ]
    result = NB.run_section(cells, "L1", 1, "Demo", cwd=tmp_path)
    assert result.ok, result.errors
    figs = NB.extract_figures(result.cells, tmp_path / "assets", "01")
    assert figs == ["01-fig1.png"]
    assert (tmp_path / "assets" / "01-fig1.png").stat().st_size > 1000


@needs_kernel
def test_a_broken_section_is_repaired_with_the_real_traceback(tmp_path, monkeypatch):
    seen = {}

    def fake_generate(prompt, **kwargs):
        seen["prompt"] = prompt
        return "# %%\nx = 1\nprint(x + 1)", "fake"

    monkeypatch.setattr(NB, "generate", fake_generate)
    cells = [{"type": "code", "source": "print(undefined_name)"}]
    result = NB.run_section(cells, "L2", 2, "Broken", cwd=tmp_path)
    assert result.ok and result.repaired
    assert "NameError" in seen["prompt"]


@needs_kernel
def test_an_unrepairable_section_reports_its_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(NB, "generate", lambda prompt, **kw: ("# %%\nraise ValueError('still')", "fake"))
    result = NB.run_section([{"type": "code", "source": "1/0"}], "L3", 3, "Bad", cwd=tmp_path)
    assert not result.ok
    assert any("ZeroDivisionError" in e for e in result.errors)


def test_missing_module_names_are_extracted():
    errors = ["ModuleNotFoundError: No module named 'sklearn.linear_model'", "NameError: x"]
    assert NB.missing_modules(errors) == ["sklearn"]


def test_markdown_only_sections_need_no_kernel(tmp_path):
    result = NB.run_section([{"type": "markdown", "source": "hi"}], "L4", 4, "T", cwd=tmp_path)
    assert result.ok


def test_sections_are_ordered_by_lecture_and_replaced_not_duplicated(tmp_path):
    path = tmp_path / "Course.ipynb"
    mk = lambda src, lid, idx: NB.to_nb_cells([{"type": "code", "source": src}], lid, idx)
    NB.upsert_section(path, "Course", mk("b = 30", "30", 30), "30", 30)
    NB.upsert_section(path, "Course", mk("a = 28", "28", 28), "28", 28)
    NB.upsert_section(path, "Course", mk("a = 'again'", "28", 28), "28", 28, status_note="note")
    nb = nbformat.read(path, as_version=4)
    sources = [c.source for c in nb.cells]
    assert sources[0].startswith("# Course")
    assert sources[1:] == ["a = 'again'", "note", "b = 30"]
    assert nb.metadata["kernelspec"]["name"] == "margin"


# --- asr ------------------------------------------------------------------------

def test_atempo_undoes_playback_rate():
    assert asr.atempo_chain(1.0) == ""
    assert asr.atempo_chain(1.75) == "atempo=0.5714"
    assert asr.atempo_chain(0.5) == "atempo=2.0000"
    # 3x needs two stages because one atempo stops at 0.5.
    chain = asr.atempo_chain(3.0)
    factors = [float(p.split("=")[1]) for p in chain.split(",")]
    assert abs(factors[0] * factors[1] - 1 / 3) < 1e-3


@pytest.mark.skipif(not asr.asr_available()["available"] or not shutil.which("say"),
                    reason="needs whisper.cpp, a model, ffmpeg and macOS `say`")
def test_real_transcription_of_sped_up_speech(tmp_path):
    aiff = tmp_path / "s.aiff"
    subprocess.run(["say", "-o", str(aiff), "The covariance matrix measures how assets move together."], check=True)
    webm = tmp_path / "fast.webm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-filter:a", "atempo=1.75",
                    "-c:a", "libopus", str(webm)], check=True)
    cues = asr.transcribe(webm.read_bytes(), rate=1.75, prompt="Portfolio theory. Covariance.")
    text = " ".join(c["text"] for c in cues).lower()
    assert "covariance" in text and "together" in text
    # Timestamps are in real (1x) time: the sentence takes ~3s spoken normally.
    assert cues[-1]["end"] > 2.0


# --- code that needs you: kept as taught, never run or "repaired" ---------------

def test_code_needing_keys_or_servers_is_left_for_you(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(NB, "_execute", lambda cells, cwd: ran.append(1) or (cells, []))
    cells = [
        {"type": "markdown", "source": "## 17 · Chat Completions API"},
        {"type": "code", "source": "# margin: run it yourself\nfrom openai import OpenAI\nclient = OpenAI()"},
        {"type": "code", "source": "import ollama\nollama.chat(model='llama3.2', messages=[])"},
    ]
    result = NB.run_section(cells, "17", 17, "Chat Completions API", cwd=tmp_path)
    assert ran == [] and result.ok is False
    assert result.not_run == ["calls the OpenAI API", "needs Ollama running"]
    assert "from openai import OpenAI" in result.cells[1].source  # the lecture's code, untouched


def test_plain_code_is_not_mistaken_for_external():
    cells = [{"type": "code", "source": "from sklearn.pipeline import make_pipeline\nimport numpy as np"}]
    assert NB.needs_you(cells) == []


def test_a_marker_on_plain_python_does_not_stop_it_running(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(NB, "_execute", lambda cells, cwd: seen.append(cells) or (cells, []))
    monkeypatch.setattr(NB, "kernel_available", lambda: True)
    cells = [{"type": "code", "source": "# Margin: run it yourself\n\nprint(sorted({1, 2}))"}]
    result = NB.run_section(cells, "13", 13, "Building blocks", cwd=tmp_path)
    assert result.ok and not result.not_run and seen
    assert seen[0][0].source == "print(sorted({1, 2}))"
