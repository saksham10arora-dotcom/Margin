// Obsidian-flavoured markdown -> HTML for the panel.
//
// The notes are written for Obsidian, so the panel has to understand what
// Obsidian understands or the in-browser view looks broken next to the real
// one: callouts (`> [!abstract]`), collapsed Q&A callouts (`> [!question]-`),
// embedded images (`![[assets/28-S003.jpg|720]]`), wikilinks, Mermaid blocks
// and KaTeX math.
//
// This module is pure string work so it can be unit tested under Node. The
// DOM half (running KaTeX, drawing Mermaid, loading images through the
// extension) lives in panel.js and runs after this HTML is inserted.
//
// Safety model: every `<` in the source is escaped BEFORE marked sees it, so
// no model output can open a tag. Only `<` is escaped. v1 escaped `>` as well,
// which also destroyed markdown's `>` blockquote marker, so its TL;DR callouts
// rendered as literal "&gt; [!abstract]" text. A lone `>` cannot start markup.
// Everything structural added here (callouts, figures) is added to marked's
// output, not passed through it.

const marked = globalThis.window?.marked ?? globalThis.marked;
import { escapeHtml } from './util.js';

marked.setOptions({ breaks: false, gfm: true });

const MATH_BLOCK = /\$\$[\s\S]+?\$\$|\$[^\n$]+?\$/g;
const SAFE_URL = /^(https?:|#|\/)/i;
const AUDIO = /\.(webm|ogg|m4a|mp3|wav|mp4)$/i; // a voice note, embedded the way Obsidian plays it

const escapeTags = (text) => text.replace(/</g, '&lt;');
const escapeText = (text) => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export { escapeHtml };

export function stripFrontmatter(markdown) {
  return String(markdown).replace(/^---\n[\s\S]*?\n---\n/, '');
}

const CALLOUT_TITLES = {
  abstract: 'Summary', summary: 'Summary', tldr: 'Summary', note: 'Note', info: 'Info',
  tip: 'Tip', question: 'Question', warning: 'Watch out', example: 'Example', quote: 'Quote',
};

/** Turn marked's <blockquote> output for `> [!type]` blocks into callouts. */
function renderCallouts(html) {
  return html.replace(/<blockquote>\s*<p>\[!(\w+)\]([+-]?)\s*([^\n<]*)(?:\n|<br>)?([\s\S]*?)<\/blockquote>/g,
    (_m, type, fold, title, rest) => {
      const kind = type.toLowerCase();
      const heading = title.trim() || CALLOUT_TITLES[kind] || kind;
      const body = `<p>${rest.trim()}`.replace(/<p>\s*<\/p>/g, '');
      if (fold === '-' || fold === '+') {
        return `<details class="callout callout-${kind}"${fold === '+' ? ' open' : ''}>`
          + `<summary>${heading}</summary><div class="callout-body">${body}</div></details>`;
      }
      return `<div class="callout callout-${kind}"><div class="callout-title">${heading}</div>`
        + `<div class="callout-body">${body}</div></div>`;
    });
}

/**
 * Markdown -> HTML. `folder` is the note's folder relative to the vault, used
 * to resolve `![[assets/x.jpg]]` embeds (Obsidian resolves them relative to
 * the note when the path starts with a folder in the same directory).
 */
export function renderMarkdown(markdown, { folder = '' } = {}) {
  // Obsidian comments (`%% ... %%`, like the marker on each note of yours) are not shown.
  let src = stripFrontmatter(markdown).replace(/%%[\s\S]*?%%\n?/g, '');

  // 1. Obsidian embeds -> a placeholder token that survives escaping.
  const embeds = [];
  src = src.replace(/!\[\[([^\]|]+)(?:\|(\d+))?\]\]/g, (_m, path, width) => {
    embeds.push({ path: path.trim(), width: width ? Number(width) : null });
    return `\u0000EMBED${embeds.length - 1}\u0000`;
  });

  // 2. Math out of marked's reach (it mangles `\\` row separators).
  const maths = [];
  src = src.replace(MATH_BLOCK, (m) => {
    maths.push(escapeTags(m));
    return `\u0000MATH${maths.length - 1}\u0000`;
  });

  // 3. Wikilinks -> plain styled text (they point into the vault, not the web).
  src = src.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, target, alias) =>
    `\u0000WIKI${escapeHtml(target)}\u0001${escapeHtml(alias || target)}\u0000`);

  let html = marked.parse(escapeTags(src));

  html = html.replace(/\u0000MATH(\d+)\u0000/g, (_m, i) => maths[Number(i)]);
  html = html.replace(/\u0000WIKI([^\u0001]*)\u0001([^\u0000]*)\u0000/g,
    (_m, target, alias) => `<span class="wikilink" title="${target}">${alias}</span>`);
  html = html.replace(/(?:<p>)?\u0000EMBED(\d+)\u0000(?:<\/p>)?/g, (_m, i) => {
    const e = embeds[Number(i)];
    const full = folder ? `${folder}/${e.path}` : e.path;
    if (AUDIO.test(e.path)) {
      return `<figure class="embed audio"><audio controls preload="none" data-vault-path="${escapeHtml(full)}"></audio></figure>`;
    }
    return `<figure class="embed"><img data-vault-path="${escapeHtml(full)}" alt=""`
      + `${e.width ? ` style="max-width:${Math.min(e.width, 2000)}px"` : ''}></figure>`;
  });

  // Mermaid fences -> a container the panel draws into.
  html = html.replace(/<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g,
    (_m, code) => `<div class="mermaid-block" data-src="${code.replace(/"/g, '&quot;')}"></div>`);

  html = renderCallouts(html);

  // Timestamp links seek the video in place instead of reloading the page.
  html = html.replace(/<a href="([^"]*)">(\d{1,2}:\d{2}(?::\d{2})?)<\/a>/g, (_m, href, label) => {
    const parts = label.split(':').map(Number);
    const secs = parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : parts[0] * 60 + parts[1];
    return `<a class="ts" href="${href}" data-seek="${secs}">${label}</a>`;
  });

  // Neutralise unsafe URL schemes, then make sure ampersands are escaped.
  // (Anchored on a leading space so `data-src=` on the Mermaid block is left alone.)
  html = html.replace(/ (href|src)="([^"]*)"/g, (_m, attr, url) => {
    const trimmed = url.replace(/^[\s\x00-\x1f]+/, '');
    const safe = SAFE_URL.test(trimmed) ? url : '#';
    return ` ${attr}="${safe.replace(/&(?!amp;|lt;|gt;|quot;)/g, '&amp;')}"`;
  });
  return html;
}

/** Decode the entity-escaped source marked produced, for handing to Mermaid. */
export function decodeEntities(text) {
  return String(text)
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, '&');
}

/** Minimal Python highlighting for the Code tab: keywords, strings, comments, numbers. */
export function highlightPython(code) {
  const KW = new Set(('and as assert async await break class continue def del elif else except False finally '
    + 'for from global if import in is lambda None nonlocal not or pass raise return True try while with yield')
    .split(' '));
  const out = [];
  const re = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|([\s\S])/g;
  let m;
  while ((m = re.exec(code))) {
    if (m[1]) out.push(`<span class="c">${escapeText(m[1])}</span>`);
    else if (m[2]) out.push(`<span class="s">${escapeText(m[2])}</span>`);
    else if (m[3]) out.push(`<span class="n">${m[3]}</span>`);
    else if (m[4]) out.push(KW.has(m[4]) ? `<span class="k">${m[4]}</span>` : m[4]);
    else out.push(escapeText(m[5]));
  }
  return out.join('');
}
