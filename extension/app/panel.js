// The side panel: a view onto what Margin is sensing, and what it wrote.
//
// Four tabs, one per stage of a lecture's life:
//   Live    slides captured so far, each with the words spoken over it
//   Notes   the composed note, rendered the way Obsidian renders it
//   Code    this lecture's notebook section, with its real outputs
//   Course  every lecture of the course and whether it has notes yet
//
// It lives in a shadow root so the page's CSS cannot restyle it and its CSS
// cannot restyle the page. It owns no state about the lecture: app.js tells
// it what to show and it reports clicks back through `handlers`.

import { decodeEntities, escapeHtml, highlightPython, renderMarkdown } from './render.js';
import { formatTs, noteProvenance } from './util.js';

const ICON = {
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  collapse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>',
  rec: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg>',
  spark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/></svg>',
  obsidian: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7"/><path d="M10 14 21 3"/><path d="M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>',
  cards: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="13" height="13" rx="2"/><path d="M7 4h11a2 2 0 0 1 2 2v11"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  model: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 6-6 6 6 6"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  redo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  code: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/></svg>',
};

const STEPS = [
  { id: 'queued', label: 'Queued' },
  { id: 'composing', label: 'Writing the notes' },
  { id: 'running', label: 'Running the code' },
  { id: 'done', label: 'Saved to your vault' },
];

export class Panel {
  constructor(bridge, handlers) {
    this.bridge = bridge;
    this.h = handlers;
    this.imageCache = new Map();
    this.mermaidReady = null;
    this.view = 'live';
    this.cards = new Map();
  }

  async mount() {
    this.host = document.createElement('margin-panel');
    this.host.style.all = 'initial';
    document.documentElement.appendChild(this.host);
    const root = this.host.attachShadow({ mode: 'open' });
    this.root = root;

    const [css, katexCss, modelsCss] = await Promise.all([
      fetch(this.bridge.url('app/panel.css')).then((r) => r.text()),
      fetch(this.bridge.url('vendor/katex.min.css')).then((r) => r.text()),
      fetch(this.bridge.url('app/models-ui.css')).then((r) => r.text()),
    ]);
    installKatexFonts(katexCss, this.bridge.url('vendor/fonts/'));
    const style = document.createElement('style');
    style.textContent = `${katexCss}\n${css}\n${modelsCss}`;
    root.appendChild(style);

    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <aside class="panel" role="complementary" aria-label="Margin notes">
        <header class="head">
          <div class="brand"><span class="dot" id="live-dot"></span>Margin</div>
          <div class="head-actions">
            <button class="icon-btn" id="collapse" aria-label="Collapse panel" title="Collapse (Alt+Shift+M)">${ICON.collapse}</button>
            <button class="icon-btn" id="close" aria-label="Close panel" title="Close">${ICON.close}</button>
          </div>
          <div class="lecture">
            <div class="crumbs" id="crumbs">Looking for a lecture…</div>
            <h1 class="title" id="title">&nbsp;</h1>
          </div>
        </header>
        <section class="signals" aria-label="What Margin is capturing">
          <div class="signal"><div class="signal-label">Speech</div><div class="signal-value" id="sig-speech">Checking…</div></div>
          <div class="signal"><div class="signal-label">Slides</div><div class="signal-value" id="sig-slides">0</div></div>
          <div class="signal"><div class="signal-label">Watched</div><div class="signal-value" id="sig-watched">0%</div><div class="meter"><i id="meter"></i></div></div>
        </section>
        <nav class="tabs" role="tablist">
          <button class="tab" role="tab" data-view="live" aria-selected="true">Live<span class="count" id="count-live"></span></button>
          <button class="tab" role="tab" data-view="note" aria-selected="false">Notes</button>
          <button class="tab" role="tab" data-view="code" aria-selected="false">Code</button>
          <button class="tab" role="tab" data-view="course" aria-selected="false">Course</button>
        </nav>
        <div class="body" id="body">
          <section class="view" data-view="live">
            <div class="setup" id="setup" hidden>
              <strong>Margin needs a model to write your notes.</strong>
              <span>Add an API key, sign in to a subscription you already pay for, or use a model on this Mac.</span>
              <button class="primary" id="setup-go">Set up models</button>
            </div>
            <div class="empty" id="live-empty"><strong>Press play.</strong>Margin keeps a picture of each slide once it has finished building, and follows along with what is said.</div>
            <div class="timeline" id="timeline"></div>
            <div class="now" id="now" hidden><b id="now-t"></b><span id="now-text"></span></div>
          </section>
          <section class="view" data-view="note" hidden><div id="note-area"></div></section>
          <section class="view" data-view="code" hidden><div id="code-area"></div></section>
          <section class="view" data-view="course" hidden>
            <div class="backfill" id="backfill" hidden>
              <p id="backfill-text"></p>
              <button class="primary" id="backfill-go" data-kind="quiet"></button>
            </div>
            <div id="course-area"></div>
          </section>
          <section class="view" data-view="models" hidden><div id="models-area"></div></section>
        </div>
        <footer class="foot">
          <button class="primary" id="primary"></button>
          <button class="auto" id="auto" aria-pressed="true" title="Write the notes automatically when a lecture ends">
            <span class="switch" aria-hidden="true"></span>Auto
          </button>
          <button class="icon-btn" id="more" aria-label="More actions" aria-haspopup="menu">${ICON.more}</button>
          <div class="menu" id="menu" role="menu" hidden>
            <button role="menuitem" data-act="obsidian">${ICON.obsidian}Open note in Obsidian</button>
            <button role="menuitem" data-act="notebook">${ICON.code}Open the course notebook</button>
            <button role="menuitem" data-act="copy">${ICON.copy}Copy note as markdown</button>
            <button role="menuitem" data-act="cards">${ICON.cards}Export Anki flashcards</button>
            <button role="menuitem" data-act="recompose">${ICON.redo}Rewrite the notes</button>
            <button role="menuitem" data-act="model">${ICON.model}<span class="menu-model">Models: <b id="model-label">…</b></span></button>
            <button role="menuitem" data-act="settings">${ICON.gear}Settings: keys and subscriptions</button>
          </div>
        </footer>
        <div class="toast" id="toast" role="status" aria-live="polite"></div>
      </aside>
      <button class="tab-pill" id="pill" hidden aria-label="Open Margin"><span class="dot" id="pill-dot"></span><span id="pill-text">Margin</span></button>`;
    root.appendChild(wrap);
    this.$ = (id) => root.getElementById(id);
    this.panel = root.querySelector('.panel');

    // Keys typed into the panel must not reach YouTube's / Udemy's shortcut
    // handlers (space = pause, k, f, m, arrows...). Shadow DOM retargets the
    // event to the host, so the page cannot tell it came from inside.
    for (const type of ['keydown', 'keyup', 'keypress']) {
      this.host.addEventListener(type, (e) => e.stopPropagation());
    }

    root.querySelectorAll('.tab').forEach((tab) =>
      tab.addEventListener('click', () => this.show(tab.dataset.view)));
    this.$('close').addEventListener('click', () => this.h.onClose());
    this.$('collapse').addEventListener('click', () => this.collapse(true));
    this.$('pill').addEventListener('click', () => this.collapse(false));
    this.$('backfill-go').addEventListener('click', () => this.h.onBackfill?.());
    this.$('setup-go').addEventListener('click', () => this.h.onMenu('model'));
    this.$('primary').addEventListener('click', () => this.h.onPrimary(this.primaryAction));
    this.$('auto').addEventListener('click', () => {
      const on = this.$('auto').getAttribute('aria-pressed') !== 'true';
      this.setAuto(on);
      this.h.onAuto(on);
    });
    this.$('more').addEventListener('click', (e) => {
      e.stopPropagation();
      this.$('menu').hidden = !this.$('menu').hidden;
    });
    root.addEventListener('click', (e) => {
      if (!e.composedPath().includes(this.$('menu'))) this.$('menu').hidden = true;
    });
    this.$('menu').addEventListener('click', (e) => {
      const act = e.target.closest('button')?.dataset.act;
      if (!act) return;
      this.$('menu').hidden = true;
      this.h.onMenu(act);
    });
    this.$('body').addEventListener('click', (e) => {
      const seek = e.target.closest('[data-seek]');
      if (seek) {
        e.preventDefault();
        this.h.onSeek(Number(seek.dataset.seek));
        return;
      }
      const lec = e.target.closest('[data-lecture-url]');
      if (lec) this.h.onOpenLecture(lec.dataset.lectureUrl);
    });
  }

  destroy() {
    this.host?.remove();
  }

  // --- layout -----------------------------------------------------------------

  collapse(yes) {
    this.panel.hidden = yes;
    this.$('pill').hidden = !yes;
    this.h.onLayout(!yes);
  }

  /** Start collapsed without recording it as the user's choice. */
  collapseQuietly() {
    this.panel.hidden = true;
    this.$('pill').hidden = false;
  }

  get expanded() {
    return this.panel && !this.panel.hidden;
  }

  rect() {
    return this.expanded ? this.panel.getBoundingClientRect() : null;
  }

  show(view) {
    this.view = view;
    this.root.querySelectorAll('.tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.view === view)));
    this.root.querySelectorAll('.view').forEach((v) => { v.hidden = v.dataset.view !== view; });
    this.h.onView(view);
  }

  toast(message, ms = 3200) {
    const el = this.$('toast');
    el.textContent = message;
    el.dataset.on = 'true';
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { el.dataset.on = 'false'; }, ms);
  }

  // --- header + signals -----------------------------------------------------------

  setLecture(meta) {
    const crumbs = [meta.course_title, meta.section_title && (meta.section_index ? `§${meta.section_index} ${meta.section_title}` : meta.section_title)]
      .filter(Boolean).join('  ·  ');
    this.$('crumbs').textContent = crumbs || platformName(meta.platform);
    const n = Number.isInteger(meta.lecture_index) ? `${meta.lecture_index} · ` : '';
    this.$('title').textContent = `${n}${meta.lecture_title || 'Untitled lecture'}`;
    this.cards.clear();
    this.$('timeline').innerHTML = '';
    this.$('live-empty').hidden = false;
    this.$('now').hidden = true;
    this.$('note-area').innerHTML = '';
    this.$('code-area').innerHTML = '';
    this.setSlides(0);
    this.setWatched(0);
  }

  setLive(state) {
    // state: 'live' (capturing), 'ok' (idle, sidecar up), 'off' (sidecar down)
    this.$('live-dot').dataset.state = state;
    this.$('pill-dot').dataset.state = state;
  }

  setSpeech(text, state = 'ok') {
    this.$('sig-speech').innerHTML = `<span class="dot" data-state="${state}"></span><span class="clip">${escapeHtml(text)}</span>`;
    this.$('sig-speech').title = text;
  }

  setSlides(n) {
    this.$('sig-slides').textContent = String(n);
    this.$('count-live').textContent = n ? String(n) : '';
    this.$('pill-text').textContent = n ? `Margin · ${n}` : 'Margin';
  }

  setWatched(fraction) {
    const p = Math.max(0, Math.min(1, fraction || 0));
    this.$('sig-watched').textContent = `${Math.round(p * 100)}%`;
    this.$('meter').style.setProperty('--p', String(p));
  }

  setAuto(on) {
    this.$('auto').setAttribute('aria-pressed', String(on));
  }

  setPrimary(action, label, { quiet = false, disabled = false } = {}) {
    this.primaryAction = action;
    const icon = action === 'capture' ? ICON.rec : action === 'compose' || action === 'recompose' ? ICON.spark : '';
    this.$('primary').innerHTML = `${icon}<span>${escapeHtml(label)}</span>`;
    this.$('primary').dataset.kind = quiet ? 'quiet' : 'accent';
    this.$('primary').disabled = disabled;
  }

  setMenuState({ note = false, notebook = false } = {}) {
    for (const act of ['obsidian', 'copy', 'cards', 'recompose']) {
      this.root.querySelector(`[data-act="${act}"]`).disabled = !note;
    }
    this.root.querySelector('[data-act="notebook"]').disabled = !notebook;
  }

  // --- live ------------------------------------------------------------------------

  addCard({ id, t, img, text }) {
    this.$('live-empty').hidden = true;
    let card = this.cards.get(id);
    if (!card) {
      card = document.createElement('article');
      card.className = 'card';
      card.innerHTML = `<div class="card-img"><img alt="Slide at ${formatTs(t)}" hidden><button class="ts-chip" data-seek="${Math.floor(t)}">${formatTs(t)}</button></div><p class="card-text"></p>`;
      // Keep cards in video order even when frames arrive out of order (seeking back).
      const after = [...this.cards.values()].find((c) => Number(c.dataset.t) > t);
      card.dataset.t = String(t);
      this.$('timeline').insertBefore(card, after || null);
      this.cards.set(id, card);
    }
    if (img) {
      const el = card.querySelector('img');
      el.src = img;
      el.hidden = false;
    }
    if (text !== undefined) card.querySelector('.card-text').textContent = text;
    const nearBottom = this.$('body').scrollHeight - this.$('body').scrollTop - this.$('body').clientHeight < 160;
    if (this.view === 'live' && nearBottom) card.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }

  updateCardTexts(textFor) {
    for (const [, card] of this.cards) {
      const text = textFor(Number(card.dataset.t));
      card.querySelector('.card-text').textContent = text;
    }
  }

  setNow(t, text) {
    if (!text) { this.$('now').hidden = true; return; }
    this.$('now').hidden = false;
    this.$('now-t').textContent = formatTs(t);
    this.$('now-text').textContent = text;
  }

  // --- notes -------------------------------------------------------------------------

  showProgress(status) {
    const area = this.$('note-area');
    const idx = STEPS.findIndex((s) => s.id === status.state);
    const started = status.started || Date.now() / 1000;
    const secs = Math.max(0, Math.round(Date.now() / 1000 - started));
    area.innerHTML = `
      <ol class="steps">${STEPS.map((s, i) => {
        const state = i < idx || status.state === 'done' ? 'done' : i === idx ? 'now' : '';
        return `<li class="step" data-s="${state}"><i></i>${s.label}</li>`;
      }).join('')}</ol>
      <p class="progress-msg">${escapeHtml(status.message || '')}${secs > 2 ? ` · ${secs}s` : ''}</p>`;
  }

  showError(message) {
    this.$('note-area').innerHTML = `<div class="empty"><strong>The notes could not be written.</strong></div>`
      + `<div class="error-box">${escapeHtml(message)}</div>`;
  }

  showNoteEmpty(message) {
    this.$('note-area').innerHTML = `<div class="empty"><strong>No notes yet.</strong>${escapeHtml(message)}</div>`;
  }

  async showNote(note) {
    const area = this.$('note-area');
    const html = renderMarkdown(note.content, { folder: note.folder });
    const made = noteProvenance(note.content);
    area.innerHTML = (made ? `<p class="note-meta">${escapeHtml(made)}</p>` : '')
      + `<article class="note">${html}</article>`;
    const article = area.querySelector('.note');
    if (typeof window.renderMathInElement === 'function') {
      window.renderMathInElement(article, {
        delimiters: [{ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false }],
        throwOnError: false,
      });
    }
    await Promise.all([this.loadImages(article), this.drawMermaid(article)]);
  }

  async loadImages(scope) {
    const imgs = [...scope.querySelectorAll('img[data-vault-path]')];
    await Promise.all(imgs.map(async (img) => {
      const path = img.dataset.vaultPath;
      if (!this.imageCache.has(path)) {
        this.imageCache.set(path, this.bridge.send({ type: 'vault-file', path }));
      }
      const res = await this.imageCache.get(path);
      if (res?.ok) img.src = res.dataUrl;
      else img.closest('figure')?.remove();
    }));
  }

  async drawMermaid(scope) {
    const blocks = [...scope.querySelectorAll('.mermaid-block')];
    if (!blocks.length) return;
    if (!this.mermaidReady) {
      this.mermaidReady = this.bridge.send({ type: 'load-mermaid' }).then(() => {
        if (!window.mermaid) throw new Error('mermaid did not load');
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          theme: 'base',
          fontFamily: '-apple-system, BlinkMacSystemFont, Inter, sans-serif',
          themeVariables: {
            darkMode: true,
            background: '#1f1c1d',
            primaryColor: '#2c2728',
            primaryBorderColor: '#e0607a',
            primaryTextColor: '#efe9ea',
            lineColor: '#9d9194',
            secondaryColor: '#262223',
            tertiaryColor: '#221f20',
            fontSize: '13px',
          },
        });
      });
    }
    try {
      await this.mermaidReady;
    } catch {
      blocks.forEach((b) => { b.classList.add('failed'); b.textContent = decodeEntities(b.dataset.src); });
      return;
    }
    let n = 0;
    for (const block of blocks) {
      const src = decodeEntities(block.dataset.src);
      try {
        const { svg } = await window.mermaid.render(`margin-mmd-${Date.now()}-${n++}`, src);
        block.innerHTML = svg;
      } catch {
        // A diagram with a syntax error still shows its source rather than vanishing.
        block.classList.add('failed');
        block.textContent = src;
      }
    }
  }

  // --- code --------------------------------------------------------------------------

  showCode(code, runInfo) {
    const area = this.$('code-area');
    if (!code?.cells?.length) {
      area.innerHTML = `<div class="empty"><strong>No code yet.</strong>Once the notes are written, the code for this lecture appears here, already run.</div>`;
      return;
    }
    let badge = '';
    if (runInfo?.not_run?.length) {
      // Needs keys, a local server or big downloads: kept exactly as taught, left for you.
      const why = runInfo.not_run.length > 1
        ? `${runInfo.not_run.slice(0, -1).join(', ')} and ${runInfo.not_run.at(-1)}`
        : runInfo.not_run[0];
      badge = `<div class="run-badge"><span class="dot" data-state="ok"></span>Not run by Margin: it ${escapeHtml(why)}. Run it yourself.</div>`;
    } else if (runInfo?.code) {
      badge = runInfo.ok
        ? `<div class="run-badge"><span class="dot" data-state="ok"></span>Ran top to bottom in a fresh kernel${runInfo.repaired ? ' (fixed once)' : ''}</div>`
        : `<div class="run-badge"><span class="dot" data-state="off"></span>Some cells fail. Errors are shown below.</div>`;
    }
    const cells = code.cells.map((cell) => {
      if (cell.type === 'markdown') {
        return `<div class="cell cell-md note">${renderMarkdown(cell.source)}</div>`;
      }
      const outs = (cell.outputs || []).map((o) => {
        if (o.type === 'image') return `<img alt="Plot" src="data:image/png;base64,${o.png}">`;
        if (o.type === 'error') return `<span class="err">${escapeHtml(o.text)}</span>`;
        if (o.type === 'html') return sanitizeTable(o.html);
        return escapeHtml(o.text);
      }).join('\n');
      return `<div class="cell"><pre class="cell-code">${highlightPython(cell.source)}</pre>`
        + (outs ? `<div class="cell-out">${outs}</div>` : '') + '</div>';
    }).join('');
    area.innerHTML = badge + cells;
    if (typeof window.renderMathInElement === 'function') {
      window.renderMathInElement(area, {
        delimiters: [{ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false }],
        throwOnError: false,
      });
    }
  }

  // --- course -----------------------------------------------------------------------

  /**
   * Lectures finished on the platform that have no notes: offer to write them
   * from their captions. `busy` replaces the offer with what is happening.
   */
  showBackfill(count, busy = null) {
    const box = this.$('backfill');
    if (!count && !busy) { box.hidden = true; return; }
    box.hidden = false;
    const s = count === 1 ? '' : 's';
    this.$('backfill-text').textContent = busy
      || `${count} lecture${s} you finished on Udemy ${count === 1 ? 'has' : 'have'} no notes. Margin can write `
       + `${count === 1 ? 'it' : 'them'} from the captions, one at a time. Slides come in if you watch one with Margin later.`;
    const go = this.$('backfill-go');
    go.hidden = Boolean(busy);
    go.textContent = `Write ${count} note${s} from captions`;
  }

  setModelLabel(text) {
    this.$('model-label').textContent = text;
  }

  showSetup(needed) {
    this.$('setup').hidden = !needed;
  }

  showCourse(lectures, currentUrl) {
    const area = this.$('course-area');
    if (!lectures.length) {
      area.innerHTML = `<div class="empty"><strong>Nothing here yet.</strong>Every lecture you watch in this course is listed here, with its notes.</div>`;
      return;
    }
    const sections = new Map();
    for (const lec of lectures) {
      const key = lec.section_title || 'Lectures';
      if (!sections.has(key)) sections.set(key, []);
      sections.get(key).push(lec);
    }
    const stateOf = (l) => (['queued', 'composing', 'running'].includes(l.status) ? 'writing'
      : l.composed ? 'composed' : l.status === 'error' ? 'error' : 'captured');
    const label = { writing: 'Writing notes', composed: 'Notes written' };
    const titleOf = (l) => (stateOf(l) === 'error' ? `Could not write notes: ${l.message || 'open it to see why'}`
      : label[stateOf(l)] || `Captured ${Math.round((l.coverage || 0) * 100)}%`);
    area.innerHTML = [...sections].map(([title, lecs]) => `
      <div class="course-section"><h3>${escapeHtml(title)}</h3>
        ${lecs.map((l) => `<button class="lec" data-lecture-url="${escapeHtml(l.url || '')}"
            data-state="${stateOf(l)}" ${l.url === currentUrl ? 'aria-current="true"' : ''}>
            <span class="lec-n">${l.lecture_index ?? ''}</span>
            <span class="lec-t">${escapeHtml(l.lecture_title || 'Untitled')}</span>
            <span class="dot" title="${escapeHtml(titleOf(l))}"></span>
          </button>`).join('')}
      </div>`).join('');
  }
}

function platformName(p) {
  return { youtube: 'YouTube', udemy: 'Udemy', coursera: 'Coursera', local: 'Local video' }[p] || 'Web video';
}

/** pandas renders DataFrames as HTML; keep the table, drop everything else. */
function sanitizeTable(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const table = doc.querySelector('table');
  if (!table) return '';
  for (const el of table.querySelectorAll('*')) {
    for (const attr of [...el.attributes]) el.removeAttribute(attr.name);
    if (!['TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD'].includes(el.tagName)) el.replaceWith(...el.childNodes);
  }
  for (const attr of [...table.attributes]) table.removeAttribute(attr.name);
  return table.outerHTML;
}

/**
 * @font-face does not work inside a shadow root, so KaTeX's fonts are declared
 * once on the page itself, with absolute extension URLs.
 */
function installKatexFonts(katexCss, fontBase) {
  if (document.getElementById('margin-katex-fonts')) return;
  const faces = katexCss.match(/@font-face\{[^}]*\}/g) || [];
  const style = document.createElement('style');
  style.id = 'margin-katex-fonts';
  style.textContent = faces.map((f) => f.replace(/url\(fonts\//g, `url(${fontBase}`)).join('\n');
  document.head.appendChild(style);
}
