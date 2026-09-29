"""One multimodal call, tried against a chain of engines until one answers.

v1 picked a single engine at startup and failed outright when it broke. Its
default, the `claude` CLI, was in fact broken for weeks (an expired headless
login), so every note request 502'd and nothing said why. Here an engine that
fails hands off to the next one, and the note records which engine actually
wrote it.

Default chain: the full Gemini models, then Claude through your subscription
(when a `claude setup-token` token is in keys.env), then OpenRouter, and the
Gemini Lite models last: they stay up when everything else is swamped, and
their notes are rewritten later. Override with MARGIN_ENGINES, e.g.
`MARGIN_ENGINES=claude,gemini` to prefer Claude.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from sidecar import chain, providers, subscriptions
from sidecar.config import load_api_keys

logger = logging.getLogger(__name__)

def _models(env: str, default: str) -> list[str]:
    return [m.strip() for m in os.environ.get(env, default).split(",") if m.strip()]


# Tried in order. A model that is overloaded (503) or retired (404) hands off
# to the next at once: overload is per model, so another key does not help,
# and the lighter models stay up when the main ones are swamped.
GEMINI_MODELS = _models("MARGIN_GEMINI_MODELS", os.environ.get(
    "MARGIN_GEMINI_MODEL",
    "gemini-3.5-flash,gemini-3-flash-preview,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"))
OPENROUTER_MODELS = _models("MARGIN_OPENROUTER_MODELS", os.environ.get(
    "MARGIN_OPENROUTER_MODEL", "google/gemini-3.5-flash,google/gemini-2.5-flash"))
CLAUDE_MODEL = os.environ.get("MARGIN_CLAUDE_MODEL", "sonnet")
# Claude runs on your subscription, the same one your Claude Code sessions use:
# a long Gemini outage with a backfill queued must not eat your coding quota.
CLAUDE_DAILY = int(os.environ.get("MARGIN_CLAUDE_DAILY", "30"))
USAGE_PATH = Path.home() / ".margin" / "usage.json"
TIMEOUT_SEC = 240
DEFAULT_CHAIN = "gemini,claude,openrouter,gemini-lite"
# Below this many output tokens a whole note (plus its code) gets cut off, so a
# nearly empty OpenRouter balance is treated as none.
MIN_OUTPUT_TOKENS = 6000
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
# Gemini 3 models think before answering, and the thinking counts against the
# output limit: at 24k, a long think left a note cut off mid-sentence. Every
# current Flash model takes 65,536.
GEMINI_MAX_OUTPUT = 65536
TEMPORARY = {429, 500, 502, 503, 504}


@dataclass
class Picture:
    """An image the model should look at, with the label it is referred to by."""
    label: str
    data: bytes
    mime: str = "image/jpeg"


class EngineError(RuntimeError):
    pass


class Busy(EngineError):
    """Unavailable for now (overloaded, rate limited, unreachable): the same
    request is worth trying again in a few minutes."""


def engine_chain() -> list:
    """The order engines are tried in: MARGIN_ENGINES if set; else your saved
    order from the menu (chain.py, entries); else the default, with any
    engines added in ~/.margin/engines.toml (names)."""
    raw = os.environ.get("MARGIN_ENGINES")
    if raw:
        return [e.strip() for e in raw.split(",") if e.strip()]
    saved = chain.load()
    if saved:
        return saved
    return providers.chain_with(DEFAULT_CHAIN.split(","))


def chain_entries() -> list[dict]:
    """The order as entries, for the menu, whether saved or default."""
    return [e if isinstance(e, dict) else chain.from_name(e, CLAUDE_MODEL) for e in engine_chain()]


def entry_engine(entry: dict):
    """The engine behind one entry of the order."""
    provider, model = entry.get("provider"), entry.get("model")
    if provider == "google" and model == "auto":
        return _gemini_full
    if provider == "google" and model == "auto-lite":
        return _gemini_lite
    if provider == "openrouter" and model == "auto":
        return _openrouter
    if provider == "custom":
        custom, _ = providers.load()
        if model not in custom:
            raise EngineError(f"no engine named '{model}' in ~/.margin/engines.toml")
        return _compatible_engine(custom[model])
    return choice_engine(entry)


def is_lite(engine: str) -> bool:
    """A fallback model that keeps notes coming when the main ones are
    overloaded, at lower quality; its notes are rewritten later."""
    return "lite" in (engine or "").lower() or (engine or "") in providers.lite_labels()


def choice_engine(choice: dict):
    """The engine for a model picked in the panel's model menu."""
    provider, model = choice.get("provider"), choice.get("model")
    if provider in ("google", "gemini"):
        return lambda p, pics, mt, t, progress=None, quality="any": _gemini(p, pics, mt, t, progress, models=[model])
    if provider == "claude":
        return lambda p, pics, mt, t, progress=None, quality="any": _claude(p, pics, mt, t, progress, quality, model=model)
    if provider in subscriptions.IDS:
        def via_subscription(p, pics, mt, t, progress=None, quality="any"):
            tool = subscriptions.get(provider)["tool"]
            _say(progress, f"Writing with {tool}")
            try:
                return subscriptions.run(provider, p, pics, model), f"{provider}/{model or 'default'}"
            except subscriptions.SubscriptionBusy as e:
                raise Busy(str(e))
            except subscriptions.SubscriptionError as e:
                raise EngineError(str(e))
        return via_subscription
    spec = providers.spec_for_choice(choice)
    if spec is None:
        raise EngineError(f"cannot write with {provider}/{model}")
    return _compatible_engine(spec)


def generate(prompt: str, pictures: list[Picture] | None = None, max_tokens: int = 24000,
             temperature: float = 0.4, progress=None, quality: str = "any", accept=None,
             choice: dict | None = None) -> tuple[str, str]:
    """Returns (text, engine_label). Raises Busy if every engine failed and at
    least one only for now, EngineError if none can work as configured, each
    listing every engine's failure so the panel can say *why*.

    `progress(message)` is told when a model is skipped, so a slow fallback
    shows up in the panel instead of looking frozen. `quality="full"` leaves
    out the Lite models (for rewriting a note one of them wrote). `accept(text)`
    rejects an answer that came back incomplete, so the next engine is tried
    instead of a cut-off note being saved. `choice` is the model picked in the
    panel's menu: tried first, with the usual chain behind it if it fails."""
    pictures = pictures or []
    failures = []
    temporary = False
    custom, _ = providers.load()
    order = ["chosen"] if choice else []
    for engine in order + engine_chain():
        if engine == "chosen":
            try:
                call = choice_engine(choice)
            except Exception as e:  # noqa: BLE001 -- a bad choice falls back to the chain
                failures.append(f"chosen {choice.get('provider')}/{choice.get('model')}: {e}")
                continue
            _say(progress, f"Writing with {choice.get('provider')} ({choice.get('model')})")
        elif isinstance(engine, dict):
            name = f"{engine.get('provider')}/{engine.get('model')}"
            try:
                call = entry_engine(engine)
            except Exception as e:  # noqa: BLE001 -- one bad entry, the next takes over
                failures.append(f"{name}: {e}")
                continue
            engine = name
        else:
            call = _ENGINES.get(engine)
        if call is None and engine in custom:
            call = _compatible_engine(custom[engine])
        if call is None:
            failures.append(f"{engine}: unknown engine")
            continue
        try:
            text, label = call(prompt, pictures, max_tokens, temperature, progress, quality)
            if not (text and text.strip()):
                failures.append(f"{engine}: empty response")
            elif accept is not None and not accept(text):
                logger.warning("Engine %s (%s) returned an incomplete answer", engine, label)
                failures.append(f"{engine}: incomplete answer from {label}")
            else:
                return text, label
        except Exception as e:  # noqa: BLE001 -- any failure means "try the next one"
            logger.warning("Engine %s failed: %s", engine, e)
            failures.append(f"{engine}: {str(e)[:240]}")
            temporary = temporary or isinstance(e, (Busy, httpx.HTTPError))
    error = Busy if temporary else EngineError
    raise error("Every engine failed. " + " | ".join(failures))


def _say(progress, message: str) -> None:
    logger.info(message)
    if progress:
        try:
            progress(message)
        except Exception:  # noqa: BLE001 -- a status update must never cost the note
            pass


def _pretty(model: str) -> str:
    return model.split("/")[-1].replace("-", " ").title()  # gemini-3.5-flash -> Gemini 3.5 Flash


def _reason(resp: httpx.Response) -> str:
    """The API's own one-line message rather than a JSON dump."""
    try:
        return str(resp.json()["error"]["message"])[:200]
    except Exception:  # noqa: BLE001
        return resp.text[:200]


# --- Gemini ------------------------------------------------------------------

_gemini_turn = 0


def _gemini_full(prompt, pictures, max_tokens, temperature, progress=None, quality="any"):
    return _gemini(prompt, pictures, max_tokens, temperature, progress,
                   models=[m for m in GEMINI_MODELS if not is_lite(m)])


def _gemini_lite(prompt, pictures, max_tokens, temperature, progress=None, quality="any"):
    if quality == "full":
        raise EngineError("Lite models are not used for a full-quality note")
    _say(progress, "Every full model is busy, using a Lite model for now")
    return _gemini(prompt, pictures, max_tokens, temperature, progress,
                   models=[m for m in GEMINI_MODELS if is_lite(m)])


def _gemini(prompt, pictures, max_tokens, temperature, progress=None, models=None):
    # Google's own docs name the key three ways; people have whichever one.
    keys = [k for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")
            for k in load_api_keys(name)]
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise EngineError("no Gemini key (GEMINI_API_KEY) set")
    parts: list[dict] = [{"text": prompt}]
    for pic in pictures:
        parts.append({"text": f"[{pic.label}]"})
        parts.append({"inline_data": {"mime_type": pic.mime, "data": base64.b64encode(pic.data).decode()}})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": max(max_tokens, GEMINI_MAX_OUTPUT), "temperature": temperature},
    }

    # Round-robin the starting key across calls so several free-tier keys share
    # the load instead of the first one absorbing every request until it dies.
    global _gemini_turn
    start = _gemini_turn % len(keys)
    _gemini_turn += 1
    order = keys[start:] + keys[:start]

    models = GEMINI_MODELS if models is None else models
    if not models:
        raise EngineError("no Gemini models configured for this tier")
    last = ""
    for i, model in enumerate(models):
        if i:
            _say(progress, f"{_pretty(models[i - 1])} is busy, trying {_pretty(model)}")
        # The preferred model gets one more chance after a short wait: most
        # 503s are brief spikes, and it writes the best notes.
        tries = 2 if i == 0 else 1
        for attempt in range(tries):
            outcome = None
            for key in order:
                try:
                    # The key goes in a header, never the URL: URLs end up in logs.
                    resp = httpx.post(f"{GEMINI_URL}/{model}:generateContent",
                                      headers={"x-goog-api-key": key}, json=body, timeout=TIMEOUT_SEC)
                except httpx.HTTPError as e:
                    last, outcome = f"{model}: {type(e).__name__}", "next-model"
                    break
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        cand = data["candidates"][0]
                        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", [])
                                       if not p.get("thought"))
                    except (KeyError, IndexError):
                        raise EngineError(f"unexpected Gemini response: {json.dumps(data)[:300]}")
                    finish = cand.get("finishReason")
                    if finish == "MAX_TOKENS" or not text.strip():
                        # Cut off, or nothing at all: never saved as a note.
                        logger.warning("%s stopped early (%s); trying the next model", model, finish)
                        last, outcome = f"{model}: stopped early ({finish})", "next-model"
                        break
                    return text, model
                last = f"{model}: HTTP {resp.status_code}: {_reason(resp)}"
                if resp.status_code == 429:
                    continue  # this key's quota is spent; the next key has its own
                if resp.status_code in TEMPORARY or resp.status_code == 404:
                    outcome = "retry" if attempt + 1 < tries and resp.status_code != 404 else "next-model"
                    break
                raise EngineError(last)  # 400/401/403: no other key or model fixes a bad request
            if outcome == "retry":
                time.sleep(4)
                continue
            break
    raise Busy(last or "every Gemini model is unavailable")


# --- OpenRouter (any vision model, OpenAI-compatible) -------------------------

def _openrouter(prompt, pictures, max_tokens, temperature, progress=None, quality="any"):
    keys = load_api_keys("OPENROUTER_API_KEY")
    if not keys:
        raise EngineError("no OPENROUTER_API_KEY in ~/.config/keys.env")
    content: list[dict] = [{"type": "text", "text": prompt}]
    for pic in pictures:
        content.append({"type": "text", "text": f"[{pic.label}]"})
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{pic.mime};base64,{base64.b64encode(pic.data).decode()}"}})
    _say(progress, "Trying OpenRouter")
    last = ""
    for model in [m for m in OPENROUTER_MODELS if quality != "full" or not is_lite(m)]:
        budget = max_tokens
        for _ in range(2):
            try:
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {keys[0]}",
                             "HTTP-Referer": "https://github.com/saksham10arora-dotcom/Margin",
                             "X-Title": "Margin"},
                    json={"model": model, "max_tokens": budget, "temperature": temperature,
                          "messages": [{"role": "user", "content": content}]},
                    timeout=TIMEOUT_SEC,
                )
            except httpx.HTTPError as e:
                raise Busy(f"OpenRouter unreachable: {type(e).__name__}")
            if resp.status_code == 200:
                data = resp.json()
                try:
                    choice = data["choices"][0]
                    answer = choice["message"]["content"] or ""
                except (KeyError, IndexError):
                    raise EngineError(f"unexpected OpenRouter response: {json.dumps(data)[:300]}")
                if choice.get("finish_reason") == "length":
                    last = f"{model}: stopped at its output limit"
                    break  # cut off: the next model, not a truncated note
                return answer, f"openrouter/{model}"
            last = f"{model}: HTTP {resp.status_code}: {_reason(resp)}"
            if resp.status_code == 402:
                # Credit is reserved for the full max_tokens up front. Ask for
                # what the balance covers, if that is still enough for a note.
                afford = re.search(r"can only afford (\d+)", resp.text)
                cap = int(afford.group(1)) if afford else 0
                if MIN_OUTPUT_TOKENS <= cap < budget:
                    budget = cap
                    continue
                raise EngineError("OpenRouter has too little credit left for a note "
                                  "(top up at openrouter.ai/settings/credits)")
            break  # this model failed: try the next one
    raise Busy(last or "OpenRouter unavailable")


# --- any OpenAI-compatible provider or local server (engines.toml) -------------

TEXT_ONLY = ("\n\nThis model cannot see images, so the slide captures are not attached. Place a "
             "slide with {{slide:S003}} only where its timestamp fits the topic, and never describe "
             "what a slide shows.")
_TRANSCRIPT = "Transcript (timestamps in [mm:ss]):\n"
_NOTEBOOK = re.compile(r"<<<COURSE NOTEBOOK\n.*?\nCOURSE NOTEBOOK>>>", re.DOTALL)


def fit_prompt(prompt: str, max_chars: int) -> str:
    """Make a prompt fit a small context: the course notebook goes first, then
    the middle of the transcript, keeping its start and end whole."""
    if len(prompt) <= max_chars:
        return prompt
    prompt = _NOTEBOOK.sub("(The course notebook is left out: it does not fit this model.)", prompt)
    at = prompt.find(_TRANSCRIPT)
    if at < 0 or len(prompt) <= max_chars:
        return prompt[:max_chars] if at < 0 else prompt
    head, transcript = prompt[:at + len(_TRANSCRIPT)], prompt[at + len(_TRANSCRIPT):]
    room = max(4000, max_chars - len(head))
    if len(transcript) > room:
        transcript = (transcript[:room // 2] + "\n[... middle of the lecture trimmed to fit this model ...]\n"
                      + transcript[-room // 2:])
    return head + transcript


def _compatible_engine(spec: providers.EngineSpec):
    def call(prompt, pictures, max_tokens, temperature, progress=None, quality="any"):
        if quality == "full" and spec.tier == "lite":
            raise EngineError(f"{spec.name} is a lite engine, not used for a full-quality note")
        headers = {}
        if spec.key:
            keys = load_api_keys(spec.key)
            if not keys:
                raise EngineError(f"no {spec.key} in ~/.config/keys.env")
            headers["Authorization"] = f"Bearer {keys[0]}"
        output = min(max_tokens, spec.max_output)
        text = prompt if (spec.vision or not pictures) else prompt + TEXT_ONLY
        if spec.context:
            # ~3.5 characters a token, leaving room for the answer
            text = fit_prompt(text, max(8000, int((spec.context - output) * 3.5)))
        if spec.vision and pictures:
            content: list[dict] | str = [{"type": "text", "text": text}]
            for pic in pictures:
                content.append({"type": "text", "text": f"[{pic.label}]"})
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:{pic.mime};base64,{base64.b64encode(pic.data).decode()}"}})
        else:
            content = text
        _say(progress, f"Writing with {spec.name} ({spec.model})")
        try:
            resp = httpx.post(f"{spec.base_url}/chat/completions", headers=headers, timeout=spec.timeout,
                              json={"model": spec.model, "max_tokens": output, "temperature": temperature,
                                    "messages": [{"role": "user", "content": content}]})
        except httpx.ConnectError:
            if spec.local:
                raise EngineError(f"{spec.name}: nothing is listening at {spec.base_url} (is the server running?)")
            raise Busy(f"{spec.name}: unreachable")
        except httpx.HTTPError as e:
            raise Busy(f"{spec.name}: {type(e).__name__}")
        if resp.status_code != 200:
            reason = f"{spec.name}: HTTP {resp.status_code}: {_reason(resp)}"
            # Free tiers meter tokens per minute and some (Groq) answer 413 when a
            # request would go over: that passes in a minute, like a 429.
            per_minute = resp.status_code == 413 and re.search(r"per minute|TPM|rate", reason, re.IGNORECASE)
            raise (Busy if resp.status_code in TEMPORARY or per_minute else EngineError)(reason)
        try:
            choice = resp.json()["choices"][0]
            answer = choice["message"].get("content") or ""
        except (KeyError, IndexError, ValueError):
            raise EngineError(f"{spec.name}: unexpected response: {resp.text[:200]}")
        if choice.get("finish_reason") == "length":
            raise EngineError(f"{spec.name}: stopped at its output limit ({output} tokens); raise max_output")
        return answer, spec.label

    return call


def test_choice(choice: dict) -> dict:
    """One word from a model picked in the menu: does it answer, how fast."""
    started = time.time()
    try:
        text, label = entry_engine(choice)("Reply with the single word: ok", [], 400, 0.0)
        return {"ok": bool(text.strip()), "model": label, "seconds": round(time.time() - started, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200], "seconds": round(time.time() - started, 1)}


def test_engines() -> list[dict]:
    """Ask every engine in the chain for one word: which answer, how fast."""
    custom, _ = providers.load()
    results = []
    for engine in engine_chain():
        started = time.time()
        try:
            if isinstance(engine, dict):
                call, engine = entry_engine(engine), f"{engine['provider']}/{engine['model']}"
            else:
                call = _ENGINES.get(engine) or (_compatible_engine(custom[engine]) if engine in custom else None)
            if call is None:
                raise EngineError("unknown engine")
            text, label = call("Reply with the single word: ok", [], 400, 0.0)
            results.append({"engine": engine, "ok": bool(text.strip()), "model": label,
                            "seconds": round(time.time() - started, 1)})
        except Exception as e:  # noqa: BLE001
            results.append({"engine": engine, "ok": False, "error": str(e)[:160],
                            "seconds": round(time.time() - started, 1)})
    return results


# --- Claude via the local CLI (your Pro/Max login) ----------------------------

def _claude(prompt, pictures, max_tokens, temperature, progress=None, quality="any", model=None):
    """The CLI has no image argument, but Claude Code reads image files with
    its Read tool. So the pictures are written to a temp folder and the prompt
    names them; the model opens each one itself.

    Headless use needs a long-lived token (`claude setup-token`, then
    CLAUDE_CODE_OAUTH_TOKEN=... in ~/.config/keys.env): the CLI's own login
    expires and cannot be refreshed without you. No token, no attempt."""
    tokens = load_api_keys("CLAUDE_CODE_OAUTH_TOKEN")
    if not tokens:
        raise EngineError("no CLAUDE_CODE_OAUTH_TOKEN in ~/.config/keys.env (run `claude setup-token`)")
    if _used_today("claude") >= CLAUDE_DAILY:
        raise EngineError(f"Claude has written {CLAUDE_DAILY} notes today, Margin's daily limit "
                          f"(MARGIN_CLAUDE_DAILY); it resumes tomorrow")
    _say(progress, "Gemini is busy, writing with Claude")
    with tempfile.TemporaryDirectory(prefix="margin-claude-") as tmp:
        tmp_dir = Path(tmp)
        listing = []
        for i, pic in enumerate(pictures):
            ext = ".png" if "png" in pic.mime else ".jpg"
            path = tmp_dir / f"{i:03d}{ext}"
            path.write_bytes(pic.data)
            listing.append(f"- {pic.label}: {path}")
        full = prompt
        if listing:
            full += ("\n\nThe images referred to above are these files. Open every one with the "
                     "Read tool before writing anything:\n" + "\n".join(listing))
        # Isolated from your own Claude Code setup: no MCP servers, no settings
        # files (so none of your hooks), no skills, and Read as the only tool.
        # That also cuts each call from ~50k tokens of setup to ~10k.
        result = subprocess.run(
            ["claude", "-p", "--model", model or CLAUDE_MODEL, "--output-format", "json",
             "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
             "--disable-slash-commands", "--tools", "Read", "--allowedTools", "Read",
             "--add-dir", str(tmp_dir), "--", full],
            capture_output=True, text=True, timeout=TIMEOUT_SEC * 2, cwd=tmp,
            env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": tokens[0]}, stdin=subprocess.DEVNULL,
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise EngineError(f"claude CLI exited {result.returncode}: {(result.stderr or result.stdout)[:200]}")
    if payload.get("is_error") or result.returncode != 0:
        reason = str(payload.get("result"))[:200]
        if re.search(r"rate.?limit|overloaded|usage limit|529|503", reason, re.IGNORECASE):
            raise Busy(f"claude CLI: {reason}")
        raise EngineError(f"claude CLI: {reason}")
    _count_use("claude")
    return payload["result"], f"claude-{model or CLAUDE_MODEL}"


_usage_lock = __import__("threading").Lock()


def _used_today(engine: str) -> int:
    try:
        data = json.loads(USAGE_PATH.read_text())
    except (OSError, ValueError):
        return 0
    return int(data.get(engine, {}).get(time.strftime("%Y-%m-%d"), 0))


def _count_use(engine: str) -> None:
    with _usage_lock:
        try:
            data = json.loads(USAGE_PATH.read_text())
        except (OSError, ValueError):
            data = {}
        today = time.strftime("%Y-%m-%d")
        days = {today: int(data.get(engine, {}).get(today, 0)) + 1}  # older days are dropped
        data[engine] = days
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(data))


_ENGINES = {"gemini": _gemini_full, "gemini-lite": _gemini_lite, "openrouter": _openrouter, "claude": _claude}


if __name__ == "__main__":
    # venv/bin/python -m sidecar.llm --test : which engines answer right now
    import sys

    if "--test" in sys.argv:
        for row in test_engines():
            mark = "ok  " if row["ok"] else "FAIL"
            print(f"{mark} {row['engine']:<14} {row['seconds']:>5}s  {row.get('model') or row.get('error')}")
