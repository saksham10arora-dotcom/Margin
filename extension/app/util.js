// Pure helpers: no DOM, no chrome APIs, so every one is unit tested.

export function formatTs(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function vttTime(t) {
  const parts = t.trim().replace(',', '.').split(':').map(Number);
  if (parts.some(Number.isNaN)) return NaN;
  return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
}

/**
 * WebVTT -> [{start, end, text}]. Handles cue ids, settings after the
 * timing line, inline tags (<c>, <v Speaker>, karaoke timestamps) and the
 * rolling duplicate lines auto-captions produce, where each cue repeats the
 * previous cue's text before adding a few words.
 */
export function parseVtt(vtt) {
  const cues = [];
  let lastFull = '';
  const blocks = String(vtt).replace(/\r/g, '').split(/\n{2,}/);
  for (const block of blocks) {
    const lines = block.split('\n');
    const timingIdx = lines.findIndex((l) => l.includes('-->'));
    if (timingIdx === -1) continue;
    const [a, b] = lines[timingIdx].split('-->');
    const start = vttTime(a);
    const end = vttTime(b.trim().split(/\s+/)[0]);
    if (Number.isNaN(start) || Number.isNaN(end)) continue;
    const text = lines
      .slice(timingIdx + 1)
      .join(' ')
      .replace(/<[^>]+>/g, '')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&nbsp;/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    if (!text) continue;
    const prev = cues[cues.length - 1];
    // Compare against the last FULL caption line, not the stored cue: after a
    // rolling line is trimmed to its new words, the stored text is only a tail.
    if (prev && text === lastFull) {
      prev.end = Math.max(prev.end, end);
      continue;
    }
    if (prev && lastFull && text.startsWith(lastFull) && start - prev.end < 1.5) {
      const tail = text.slice(lastFull.length).trim();
      lastFull = text;
      if (tail) cues.push({ start, end, text: tail });
      else prev.end = Math.max(prev.end, end);
      continue;
    }
    lastFull = text;
    cues.push({ start, end, text });
  }
  return cues;
}

/** YouTube sidecar cues come as [{startSec, text}] with no end time. */
export function cuesFromStarts(items) {
  const sorted = [...items].sort((x, y) => x.startSec - y.startSec);
  return sorted.map((c, i) => ({
    start: c.startSec,
    end: i + 1 < sorted.length ? sorted[i + 1].startSec : c.startSec + 4,
    text: String(c.text || '').replace(/\s+/g, ' ').trim(),
  })).filter((c) => c.text);
}

/**
 * Pick the caption track to use from a platform's list:
 * English before other languages, human-written before auto-generated.
 * Each entry: {locale, source: 'manual'|'auto'|..., url}.
 */
export function pickCaption(tracks) {
  if (!tracks || !tracks.length) return null;
  const score = (t) => {
    const loc = String(t.locale || '').toLowerCase();
    const english = loc === 'en' || loc.startsWith('en_') || loc.startsWith('en-');
    const human = t.source && t.source !== 'auto';
    return (english ? 2 : 0) + (human ? 1 : 0);
  };
  return [...tracks].sort((a, b) => score(b) - score(a))[0];
}

/** Text spoken between two video times, for a live slide card. */
export function textBetween(cues, start, end, maxChars = 220) {
  const words = cues.filter((c) => c.end > start && c.start < end).map((c) => c.text).join(' ');
  return words.length > maxChars ? words.slice(0, maxChars).replace(/\s+\S*$/, '') + '…' : words;
}

/** Mean absolute difference of two equal-length grey signatures (0-255). */
export function signatureDiff(a, b) {
  if (!a || !b || a.length !== b.length) return 255;
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;
}

/** RGBA pixel data -> grey signature + mean brightness. */
export function greySignature(rgba) {
  const n = rgba.length / 4;
  const sig = new Uint8Array(n);
  let total = 0;
  for (let i = 0; i < n; i++) {
    const g = (rgba[i * 4] * 299 + rgba[i * 4 + 1] * 587 + rgba[i * 4 + 2] * 114) / 1000;
    sig[i] = g;
    total += g;
  }
  return { sig, brightness: n ? total / n : 0 };
}

/** Standard deviation of a signature: near 0 means a flat frame (black, a fade). */
export function signatureSpread(sig) {
  if (!sig || !sig.length) return 0;
  let mean = 0;
  for (const v of sig) mean += v;
  mean /= sig.length;
  let varSum = 0;
  for (const v of sig) varSum += (v - mean) ** 2;
  return Math.sqrt(varSum / sig.length);
}

/** Merge [start, end] ranges, bridging tiny gaps. */
export function mergeRanges(ranges, gap = 2) {
  const sorted = ranges.filter(([a, b]) => b > a).map(([a, b]) => [a, b]).sort((x, y) => x[0] - y[0]);
  const out = [];
  for (const [a, b] of sorted) {
    const last = out[out.length - 1];
    if (last && a <= last[1] + gap) last[1] = Math.max(last[1], b);
    else out.push([a, b]);
  }
  return out;
}

export function coverage(ranges, duration) {
  if (!duration) return 0;
  const covered = mergeRanges(ranges).reduce((s, [a, b]) => s + (b - a), 0);
  return Math.min(1, covered / duration);
}

/**
 * Should a lecture be composed automatically now?
 * - it ended, having been mostly watched, or
 * - the viewer is leaving it (next lecture) having watched most of it.
 */
export function shouldAutoCompose({ reason, coverage: cov, composed, stale = false, edited = false, hasContent }) {
  if (!hasContent) return false;
  // The note exists: rewrite it only if something new was captured since
  // (a slide, or speech from a part not heard before), so rewatching fills
  // the note in instead of rewriting it for nothing. Never behind your back
  // once you have edited it.
  if (composed) return stale && !edited && (reason === 'ended' || reason === 'leaving');
  if (reason === 'ended') return cov >= 0.6;
  if (reason === 'leaving') return cov >= 0.75;
  return false;
}

/** "gemini-3.5-flash-lite" -> "Gemini 3.5 Flash Lite"; OpenRouter and Claude read naturally too. */
export function engineName(engine) {
  if (!engine) return '';
  const via = engine.startsWith('openrouter/') ? ' via OpenRouter' : '';
  const model = engine.split('/').pop().replace(/^claude-/, 'claude ');
  return model.split(/[-\s]/).filter(Boolean)
    .map((w) => (/^\d/.test(w) ? w : w[0].toUpperCase() + w.slice(1))).join(' ') + via;
}

/**
 * One line on how a note was made, from its frontmatter: which model, how
 * many slides, and whether it is waiting on something that will improve it.
 */
export function noteProvenance(content) {
  const fm = String(content || '').match(/^---\n([\s\S]*?)\n---/)?.[1];
  if (!fm) return '';
  const get = (key) => fm.match(new RegExp(`^${key}: *['"]?([^'"\\n]*)['"]?$`, 'm'))?.[1]?.trim();
  const engine = get('engine');
  const slides = Number(get('slides_captured') || 0);
  const bits = [];
  if (engine) bits.push(engineName(engine));
  if (get('written_from') === 'captions') bits.push('from captions, no slides until you watch it with Margin');
  else bits.push(`${slides} slide${slides === 1 ? '' : 's'} captured`);
  if (/lite/i.test(engine || '')) bits.push('a stronger model will rewrite it');
  return bits.join(' · ');
}

/** Text made safe to put in HTML: markup characters and quotes escaped. */
export function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Escaped text with `backticked` bits shown as code (setup instructions). */
export function escapeWithCode(text) {
  return escapeHtml(text).replace(/`([^`]+)`/g, '<code>$1</code>');
}
