"""Subscriptions: plans you pay for monthly, used through the vendor's own
command-line tool you are signed in to, the way Claude Code uses a Claude
Pro or Max plan. Margin runs the tool headless (one prompt in, the note out)
in a throwaway folder, with read-only permissions where the tool has them.

Each counts against the same plan you use in that tool yourself, so each has
a daily limit (MARGIN_SUBSCRIPTION_DAILY, default 30 notes a tool).

Checked on a real Mac: Claude Code, Codex (ChatGPT), OpenCode. Gemini CLI,
Cursor Agent, Copilot CLI and Qwen Code follow their documented headless
modes; if one changes, its error is shown in the menu's Test.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT_SEC = 600
DAILY = int(os.environ.get("MARGIN_SUBSCRIPTION_DAILY", "30"))

SUBSCRIPTIONS: list[dict] = [
    {"id": "claude", "name": "Claude Pro / Max", "tool": "Claude Code", "bin": "claude", "images": True,
     "models": ["sonnet", "opus", "haiku"], "install": "npm install -g @anthropic-ai/claude-code",
     "login": "Run `claude setup-token` and save the token below as CLAUDE_CODE_OAUTH_TOKEN",
     "key": "CLAUDE_CODE_OAUTH_TOKEN"},
    {"id": "codex", "name": "ChatGPT Plus / Pro", "tool": "Codex CLI", "bin": "codex", "images": True,
     "models": ["default"], "install": "npm install -g @openai/codex", "login": "Run `codex login` and sign in with ChatGPT"},
    # Google AI Pro and Ultra: Gemini CLI stopped working for them on 18 June
    # 2026 and Antigravity's agy took its place (Gemini 3.x Flash and Pro,
    # Claude Sonnet and Opus on the same plan). Checked on a real Mac.
    {"id": "antigravity", "name": "Google AI Pro / Ultra", "tool": "Antigravity CLI", "bin": "agy", "images": True,
     "models": [], "install": "Install Antigravity from antigravity.google (it adds `agy`)",
     "login": "Open Antigravity once and sign in with your Google account"},
    {"id": "gemini-cli", "name": "Google Cloud / Code Assist (Gemini CLI)", "tool": "Gemini CLI", "bin": "gemini",
     "images": True, "models": ["default"], "install": "npm install -g @google/gemini-cli",
     "login": "Run `gemini` once and sign in (AI Pro and Ultra use Antigravity instead)"},
    {"id": "opencode", "name": "OpenCode (any provider signed in there)", "tool": "OpenCode", "bin": "opencode",
     "images": True, "models": [], "install": "brew install sst/tap/opencode", "login": "Run `opencode auth login`"},
    {"id": "cursor", "name": "Cursor", "tool": "Cursor Agent", "bin": "cursor-agent", "images": False,
     "models": ["default"], "install": "curl https://cursor.com/install -fsS | bash", "login": "Run `cursor-agent login`"},
    {"id": "copilot", "name": "GitHub Copilot", "tool": "Copilot CLI", "bin": "copilot", "images": False,
     "models": ["default"], "install": "npm install -g @github/copilot", "login": "Run `copilot` and sign in with /login"},
    {"id": "qwen", "name": "Qwen (qwen.ai account)", "tool": "Qwen Code", "bin": "qwen", "images": True,
     "models": ["default"], "install": "npm install -g @qwen-code/qwen-code", "login": "Run `qwen` once and sign in"},
]
IDS = {s["id"] for s in SUBSCRIPTIONS}
# SuperGrok has no official command-line tool to sign in with yet: Grok is
# reachable with an xAI API key instead (it is in the provider list).
NOT_YET = {"grok": "SuperGrok has no official command-line tool yet; use an xAI API key for Grok"}


class SubscriptionError(RuntimeError):
    pass


class SubscriptionBusy(SubscriptionError):
    pass


def get(sub_id: str) -> dict:
    sub = next((s for s in SUBSCRIPTIONS if s["id"] == sub_id), None)
    if sub is None:
        raise SubscriptionError(f"unknown subscription '{sub_id}'")
    return sub


def find(binary: str) -> str | None:
    """The tool on PATH, or where npm, bun, Homebrew and installers usually
    put it (the sidecar starts with a short PATH at login)."""
    home = Path.home()
    extra = [home / ".local/bin", home / ".npm-global/bin", home / ".bun/bin", home / ".opencode/bin",
             home / ".cursor/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
             *map(Path, glob.glob(str(home / ".nvm/versions/node/*/bin")))]
    path = os.pathsep.join([os.environ.get("PATH", ""), *map(str, extra)])
    return shutil.which(binary, path=path)


def status() -> list[dict]:
    rows = []
    for s in SUBSCRIPTIONS:
        rows.append({"id": s["id"], "name": s["name"], "tool": s["tool"], "installed": bool(find(s["bin"])),
                     "install": s["install"], "login": s["login"], "images": s["images"], "key": s.get("key")})
    return rows


def models(sub_id: str) -> list[str]:
    sub = get(sub_id)
    if sub_id == "antigravity":
        exe = find("agy")
        if not exe:
            return []
        out = subprocess.run([exe, "models"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        return [ln.split("\t")[0].strip() for ln in out.stdout.splitlines() if "\t" in ln]
    if sub_id != "opencode":
        return list(sub["models"])
    exe = find("opencode")
    if not exe:
        return []
    out = subprocess.run([exe, "models"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return [ln.strip() for ln in out.stdout.splitlines() if re.match(r"^[\w.-]+/[\w.:/-]+$", ln.strip())]


_BUSY = re.compile(r"rate.?limit|usage limit|quota|too many requests|429|overloaded|capacity|try again later",
                   re.IGNORECASE)


def _fail(tool: str, text: str):
    reason = (text or "no output").strip().splitlines()[-1][:240] if (text or "").strip() else "no output"
    raise (SubscriptionBusy if _BUSY.search(text or "") else SubscriptionError)(f"{tool}: {reason}")


def run(sub_id: str, prompt: str, pictures: list, model: str | None = None) -> str:
    """The tool's answer to one prompt, with the slides attached as images
    when the tool can take them."""
    from sidecar.llm import _count_use, _used_today

    sub = get(sub_id)
    exe = find(sub["bin"])
    if not exe:
        raise SubscriptionError(f"{sub['tool']} is not installed ({sub['install']})")
    if _used_today(sub_id) >= DAILY:
        raise SubscriptionError(f"{sub['tool']} has written {DAILY} notes today, Margin's daily limit "
                                f"(MARGIN_SUBSCRIPTION_DAILY); it resumes tomorrow")
    model = None if model in (None, "", "default") else model
    with tempfile.TemporaryDirectory(prefix=f"margin-{sub_id}-") as tmp:
        files = []
        if sub["images"]:
            for i, pic in enumerate(pictures):
                path = Path(tmp) / f"{re.sub(r'[^A-Za-z0-9]+', '_', pic.label)[:24]}_{i:02d}.jpg"
                path.write_bytes(pic.data)
                files.append((pic.label, path))
        text = _invoke(sub_id, exe, prompt, files, model, Path(tmp))
    if not text.strip():
        raise SubscriptionError(f"{sub['tool']}: empty answer")
    _count_use(sub_id)
    return text


def _invoke(sub_id: str, exe: str, prompt: str, files: list, model: str | None, tmp: Path) -> str:
    listing = "\n".join(f"- {label}: {path.name}" for label, path in files)

    def call(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC, cwd=tmp,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise SubscriptionBusy(f"{sub_id}: no answer within {TIMEOUT_SEC // 60} minutes")

    if sub_id == "codex":
        out = tmp / "answer.md"
        full = prompt + (f"\n\nThe slide images are attached in this order:\n{listing}" if files else "")
        cmd = [exe, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only", "--color", "never",
               *(["-m", model] if model else []), *[a for _, p in files for a in ("-i", str(p))],
               "-o", str(out), "--", full]
        r = call(cmd)
        if out.exists() and out.read_text().strip():
            return out.read_text()
        _fail("Codex", r.stderr + r.stdout)
    if sub_id in ("gemini-cli", "qwen"):
        refs = "\n".join(f"- {label}: @{path.name}" for label, path in files)
        full = prompt + (f"\n\nThe slide images:\n{refs}" if files else "")
        r = call([exe, "-p", full, "-o", "json", *(["-m", model] if model else []), "--include-directories", str(tmp)])
        try:
            data = json.loads(r.stdout)
        except ValueError:
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            _fail(get(sub_id)["tool"], r.stderr + r.stdout)
        if data.get("error"):
            _fail(get(sub_id)["tool"], str(data["error"].get("message") or data["error"]))
        return str(data.get("response") or "")
    if sub_id == "opencode":
        if not model:
            raise SubscriptionError("OpenCode needs a model: pick one (provider/model) in the menu")
        r = call([exe, "run", "-m", model, "--format", "json", *[a for _, p in files for a in ("-f", str(p))],
                  prompt + (f"\n\nThe attached images are, in order:\n{listing}" if files else "")])
        parts, error = [], None
        for line in r.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            part = event.get("part") or {}
            if part.get("type") == "text" and part.get("text"):
                parts.append(part["text"])
            if event.get("type") == "error":
                error = json.dumps(event.get("error"))[:400]
        if parts:
            return "".join(parts)
        _fail("OpenCode", error or (r.stderr + r.stdout))
    if sub_id == "cursor":
        r = call([exe, "-p", "--output-format", "text", *(["-m", model] if model else []), prompt])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        _fail("Cursor Agent", r.stderr + r.stdout)
    if sub_id == "antigravity":
        full = prompt + (f"\n\nThe slide images are files in this folder, in this order:\n{listing}" if files else "")
        r = call([exe, "-p", full, "--output-format", "text", "--print-timeout", f"{TIMEOUT_SEC // 60}m",
                  "--sandbox", "--add-dir", str(tmp), *(["--model", model] if model else [])])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        _fail("Antigravity CLI", r.stderr + r.stdout)
    if sub_id == "copilot":
        r = call([exe, "-p", prompt, *(["--model", model] if model else [])])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        _fail("Copilot CLI", r.stderr + r.stdout)
    raise SubscriptionError(f"no way to run {sub_id}")
