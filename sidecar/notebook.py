"""The course notebook: one section per lecture, and every section actually runs.

Code a model writes from a lecture is plausible far more often than it is
correct. So a section is not added to the notebook until it has been
executed, and a section that fails gets one repair pass with the real
traceback before it is accepted.

Each lecture's section is self-contained (the composer is told to make it
run in a fresh kernel), which is what makes this cheap: only the new section
is executed, never the whole course. It runs in Margin's own kernel
(~/.margin/nbenv, registered as "margin"), so a missing package is installed
there and never into your own Python environments.
"""
from __future__ import annotations

import base64
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError, DeadKernelError

from sidecar.llm import generate

logger = logging.getLogger(__name__)

KERNEL_NAME = "margin"
NBENV_PYTHON = Path.home() / ".margin" / "nbenv" / "bin" / "python"
CELL_TIMEOUT_SEC = 120

# import name -> pip name, for the modules whose names differ.
PIP_NAMES = {
    "sklearn": "scikit-learn", "cv2": "opencv-python", "yaml": "pyyaml", "PIL": "pillow",
    "bs4": "beautifulsoup4", "Crypto": "pycryptodome", "talib": "TA-Lib",
    "pandas_datareader": "pandas-datareader", "dateutil": "python-dateutil",
}


@dataclass
class RunResult:
    cells: list  # nbformat cells, executed, with outputs
    ok: bool
    errors: list[str] = field(default_factory=list)
    repaired: bool = False
    installed: list[str] = field(default_factory=list)
    not_run: list[str] = field(default_factory=list)  # why it was left for you to run


# Code Margin must not run on its own: it needs your keys, a server on your
# machine or gigabytes of downloads, or it never returns (a UI server blocks the
# kernel). Running it anyway fails, and the repair pass would then "fix" the
# lecture's code into fakes. The composer marks such cells; these patterns
# catch the ones it forgets.
RUN_IT_YOURSELF = re.compile(r"^\s*#\s*margin:\s*run it yourself", re.IGNORECASE | re.MULTILINE)
NEEDS_YOU = [
    (re.compile(r"\bopenai\b|\bOpenAI\(|ChatOpenAI"), "calls the OpenAI API"),
    (re.compile(r"\banthropic\b|ChatAnthropic"), "calls the Anthropic API"),
    (re.compile(r"google\.generativeai|from google import genai|\bgenai\.Client|ChatGoogleGenerativeAI"),
     "calls the Gemini API"),
    (re.compile(r"\bollama\b|localhost:11434"), "needs Ollama running"),
    (re.compile(r"\bgradio\b|\bstreamlit\b|uvicorn\.run|\.launch\("), "starts a web app"),
    (re.compile(r"from_pretrained\(|\bpipeline\(|load_dataset\(|snapshot_download\(|hf_hub_download\("),
     "downloads models or data from Hugging Face"),
    (re.compile(r"load_dotenv\(|(?:environ\[|getenv\()\s*['\"]\w*(?:API_KEY|TOKEN)"), "needs your API keys"),
]


def needs_you(cells: list[dict]) -> list[str]:
    """Why this section has to be run by you rather than by Margin, if it does.
    Decided by what the code does. The composer's `# margin: run it yourself`
    marker alone is not enough: it also lands on plain Python that runs fine."""
    reasons: list[str] = []
    for cell in cells:
        if cell["type"] != "code":
            continue
        for pattern, reason in NEEDS_YOU:
            if pattern.search(cell["source"]) and reason not in reasons:
                reasons.append(reason)
    return reasons


def without_stray_markers(cells: list[dict]) -> list[dict]:
    """Drop the run-it-yourself marker from code Margin is going to run, so the
    notebook does not tell you to run by hand what already ran."""
    out = []
    for cell in cells:
        if cell["type"] == "code" and RUN_IT_YOURSELF.search(cell["source"]):
            source = RUN_IT_YOURSELF.sub("", cell["source"]).lstrip("\n")
            cell = {**cell, "source": source}
        out.append(cell)
    return out


def kernel_available() -> bool:
    try:
        from jupyter_client.kernelspec import KernelSpecManager
        return KERNEL_NAME in KernelSpecManager().find_kernel_specs()
    except Exception:  # noqa: BLE001
        return False


def to_nb_cells(cells: list[dict], lecture_id: str, lecture_index) -> list:
    tag = {"margin": {"lecture_id": str(lecture_id), "lecture_index": lecture_index}}
    out = []
    for cell in cells:
        if cell["type"] == "markdown":
            out.append(nbformat.v4.new_markdown_cell(cell["source"], metadata=dict(tag)))
        else:
            out.append(nbformat.v4.new_code_cell(cell["source"], metadata=dict(tag)))
    return out


def _execute(cells: list, cwd: Path) -> tuple[list, list[str]]:
    """Run cells in a fresh margin kernel. Returns (executed cells, errors)."""
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": KERNEL_NAME, "display_name": "Margin", "language": "python"}
    nb.cells = [nbformat.from_dict(c) for c in cells]
    client = NotebookClient(nb, kernel_name=KERNEL_NAME, timeout=CELL_TIMEOUT_SEC,
                            allow_errors=True, resources={"metadata": {"path": str(cwd)}})
    # Non-interactive backend: plt.show() must produce an image/png output,
    # not try to open a window from a background process.
    setup = nbformat.v4.new_code_cell("%matplotlib inline")
    nb.cells.insert(0, setup)
    try:
        client.execute()
    except (CellExecutionError, DeadKernelError, TimeoutError, RuntimeError) as e:
        return nb.cells[1:], [f"kernel: {str(e)[:400]}"]
    finally:
        try:
            client.km and client.km.shutdown_kernel(now=True)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    executed = nb.cells[1:]
    errors = []
    for cell in executed:
        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                errors.append(f"{output.get('ename')}: {output.get('evalue')}")
    return executed, errors


def missing_modules(errors: list[str]) -> list[str]:
    found = []
    for err in errors:
        m = re.search(r"ModuleNotFoundError: No module named '([\w.]+)'", err)
        if m:
            found.append(m.group(1).split(".")[0])
    return sorted(set(found))


def install_into_nbenv(modules: list[str]) -> list[str]:
    """pip-install into Margin's own kernel env only. Returns what installed."""
    if not modules or not NBENV_PYTHON.exists():
        return []
    packages = [PIP_NAMES.get(m, m) for m in modules]
    uv = shutil.which("uv")
    cmd = ([uv, "pip", "install", "--python", str(NBENV_PYTHON), *packages] if uv
           else [str(NBENV_PYTHON), "-m", "pip", "install", *packages])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.warning("Could not install %s into the margin kernel: %s", packages, result.stderr[-300:])
        return []
    return packages


def _cells_to_percent(cells: list) -> str:
    chunks = []
    for cell in cells:
        if cell["cell_type"] == "markdown":
            body = "\n".join("# " + ln if ln else "#" for ln in cell["source"].splitlines())
            chunks.append("# %% [markdown]\n" + body)
        else:
            chunks.append("# %%\n" + cell["source"])
    return "\n\n".join(chunks)


def _error_report(cells: list) -> str:
    lines = []
    for i, cell in enumerate(cells):
        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                tb = "\n".join(output.get("traceback", []))
                tb = re.sub(r"\x1b\[[0-9;]*m", "", tb)  # strip ANSI colours
                lines.append(f"Cell {i} raised:\n{tb[-2500:]}")
    return "\n\n".join(lines)


def repair(cells: list, lecture_title: str) -> list[dict] | None:
    """One pass: show a model the code and its real tracebacks, ask for the
    whole section back fixed. Text only, so it is a cheap call."""
    from sidecar.compose import parse_percent_cells  # local: avoids an import cycle

    prompt = f"""This notebook section for the lecture "{lecture_title}" fails when run in a fresh
Python kernel. Fix it so every cell runs top to bottom. Keep the teaching intent, the markdown,
and the lecture's own code as close to the original as possible; change only what is broken.
Never replace real calls (APIs, downloads, services) with fakes or mocks.
If a data download fails, fall back to realistic synthetic data and say so in a comment.
Return the complete fixed section in jupytext percent format (`# %%` for code,
`# %% [markdown]` for markdown with every line prefixed `# `), and nothing else.

The section:
{_cells_to_percent(cells)}

The errors:
{_error_report(cells)}
"""
    try:
        raw, _engine = generate(prompt, max_tokens=12000, temperature=0.2)
    except Exception as e:  # noqa: BLE001
        logger.warning("Repair call failed: %s", e)
        return None
    fixed = parse_percent_cells(raw)
    return fixed or None


def run_section(cells: list[dict], lecture_id: str, lecture_index, lecture_title: str,
                cwd: Path) -> RunResult:
    """Execute a lecture's cells; install missing modules and repair once."""
    reasons = needs_you(cells)
    if not reasons:
        cells = without_stray_markers(cells)
    nb_cells = to_nb_cells(cells, lecture_id, lecture_index)
    if not any(c.cell_type == "code" for c in nb_cells):
        return RunResult(cells=nb_cells, ok=True)
    if reasons:
        return RunResult(cells=nb_cells, ok=False, not_run=reasons)
    if not kernel_available():
        return RunResult(cells=nb_cells, ok=False,
                         errors=["The 'margin' Jupyter kernel is not installed; run scripts/setup.sh"])

    # The course folder is the natural working directory (relative data
    # paths resolve next to the notebook), but on a course's first lecture it
    # does not exist yet.
    cwd.mkdir(parents=True, exist_ok=True)
    executed, errors = _execute(nb_cells, cwd)
    installed: list[str] = []
    missing = missing_modules(errors)
    if missing:
        installed = install_into_nbenv(missing)
        if installed:
            executed, errors = _execute(nb_cells, cwd)
    if not errors:
        return RunResult(cells=executed, ok=True, installed=installed)

    fixed = repair(executed, lecture_title)
    if fixed:
        fixed_cells = to_nb_cells(fixed, lecture_id, lecture_index)
        re_executed, re_errors = _execute(fixed_cells, cwd)
        if len(re_errors) < len(errors):
            return RunResult(cells=re_executed, ok=not re_errors, errors=re_errors,
                             repaired=True, installed=installed)
    return RunResult(cells=executed, ok=False, errors=errors, installed=installed)


def extract_figures(cells: list, assets: Path, prefix: str, limit: int = 4) -> list[str]:
    """Save the PNGs the section produced, for embedding in the note."""
    names = []
    for cell in cells:
        for output in cell.get("outputs", []):
            data = output.get("data", {})
            png = data.get("image/png")
            if not png or len(names) >= limit:
                continue
            assets.mkdir(parents=True, exist_ok=True)
            name = f"{prefix}-fig{len(names) + 1}.png"
            (assets / name).write_bytes(base64.b64decode(png))
            names.append(name)
    return names


def remove_section(path: Path, lecture_id: str) -> None:
    """Take a lecture's section out of the course notebook, if it has one."""
    if not path.exists():
        return
    nb = nbformat.read(path, as_version=4)
    keep = [c for c in nb.cells if str(c.metadata.get("margin", {}).get("lecture_id")) != str(lecture_id)
            or c.metadata.get("margin", {}).get("header")]
    if len(keep) != len(nb.cells):
        nb.cells = keep
        nbformat.write(nb, path)


def upsert_section(path: Path, course_title: str, section_cells: list, lecture_id: str,
                   lecture_index, status_note: str | None = None) -> None:
    """Replace this lecture's section in the course notebook (or add it), keeping
    sections ordered by lecture number however out of order they were watched."""
    if path.exists():
        nb = nbformat.read(path, as_version=4)
    else:
        nb = nbformat.v4.new_notebook()
        nb.cells = [nbformat.v4.new_markdown_cell(
            f"# {course_title}\n\nCode for every lecture, written and run by Margin while you "
            f"watched. Each section runs on its own in a fresh kernel.",
            metadata={"margin": {"header": True}})]
    nb.metadata["kernelspec"] = {"name": KERNEL_NAME, "display_name": "Margin (course notebooks)",
                                 "language": "python"}
    nb.metadata.setdefault("language_info", {"name": "python"})

    header = [c for c in nb.cells if c.metadata.get("margin", {}).get("header")]
    others = [c for c in nb.cells
              if not c.metadata.get("margin", {}).get("header")
              and str(c.metadata.get("margin", {}).get("lecture_id")) != str(lecture_id)]

    new_cells = list(section_cells)
    if status_note:
        tag = {"margin": {"lecture_id": str(lecture_id), "lecture_index": lecture_index}}
        new_cells.insert(1 if new_cells else 0, nbformat.v4.new_markdown_cell(status_note, metadata=tag))

    def order(cell) -> float:
        idx = cell.metadata.get("margin", {}).get("lecture_index")
        return float(idx) if isinstance(idx, (int, float)) else 1e9

    # Group existing cells by lecture, keeping each group's internal order.
    groups: dict[str, list] = {}
    for cell in others:
        groups.setdefault(str(cell.metadata.get("margin", {}).get("lecture_id")), []).append(cell)
    groups[str(lecture_id)] = new_cells
    ordered = sorted(groups.values(), key=lambda g: order(g[0]) if g else 1e9)
    nb.cells = header + [c for g in ordered for c in g]
    path.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nb, path)
