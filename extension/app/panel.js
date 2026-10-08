// The side panel: a view onto what Margin is sensing, and what it wrote.
//
// Tabs, one per stage of a lecture's life:
//   Live    slides captured so far, each with the words spoken over it
//   Notes   the composed note, rendered the way Obsidian renders it
//   Mine    your own notes: typed, pasted or spoken, stamped with the moment
//           (on a page that is not a lecture, this is the only tab)
//   Code    this lecture's notebook section, with its real outputs
//   Course  every lecture of the course and whether it has notes yet
//
// It lives in a shadow root so the page's CSS cannot restyle it and its CSS
// cannot restyle the page. It owns no state about the lecture: app.js tells
// it what to show and it reports clicks back through `handlers`.

import { decodeEntities, escapeHtml, highlightPython, renderMarkdown } from './render.js';
import { Quiz } from './quiz.js';
import { VoiceMemo } from './mine.js';
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
  ask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
};

const ANKI_HELP = `<details class="anki-help"><summary>What is Anki?</summary>
  <p>A free flashcard app that shows each card again just before you would forget it, so what you learn stays
  for months, not days. <b>Export to Anki</b> saves these cards as a file; in Anki, choose File, then Import.
  Get it at <a href="https://apps.ankiweb.net" target="_blank" rel="noopener">apps.ankiweb.net</a>.</p></details>`;

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
          <button class="tab" role="tab" data-view="mine" aria-selected="false">Mine<span class="count" id="count-mine"></span></button>
          <button class="tab" role="tab" data-view="crux" aria-selected="false">Crux</button>
          <button class="tab" role="tab" data-view="quiz" aria-selected="false">Quiz</button>
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
          <section class="view" data-view="mine" hidden>
            <form class="composer" id="mine-form">
              <div class="mine-quote-chip" id="mine-quote" hidden>
                <span id="mine-quote-text"></span>
                <button type="button" class="chip-x" id="mine-quote-x" aria-label="Leave the selected text out">${ICON.close}</button>
              </div>
              <textarea id="mine-text" rows="3" spellcheck="true"
                placeholder="Your own note. Paste a screenshot, drop a picture, or record your voice."></textarea>
              <div class="mine-attach" id="mine-attach" hidden></div>
              <div class="composer-row">
                <button type="button" class="icon-btn" id="mine-pick" aria-label="Add a picture" title="Add a picture">${ICON.image}</button>
                <button type="button" class="icon-btn mic" id="mine-voice" aria-pressed="false" aria-label="Record a voice note" title="Record a voice note">${ICON.mic}</button>
                <span class="mine-at" id="mine-at"></span>
                <button type="submit" class="primary mine-add" id="mine-add">Add</button>
              </div>
              <input type="file" id="mine-file" accept="image/*" multiple hidden>
            </form>
            <div class="empty" id="mine-empty"><strong>Your own notes go here.</strong>Each one is stamped with the moment
              you wrote it and goes into the note in your vault, in its own section that Margin never rewrites.</div>
            <div class="mine-list" id="mine-list"></div>
          </section>
          <section class="view" data-view="crux" hidden>
            <div id="crux-area"></div>
            <form class="ask" id="ask" hidden>
              <label class="ask-label" for="ask-input">Ask the lecture</label>
              <div class="ask-row">
                <input id="ask-input" type="text" maxlength="600" autocomplete="off" spellcheck="true"
                  placeholder="Anything it covered. Answers link to the moment.">
                <button class="ask-go" type="submit" aria-label="Ask">${ICON.ask}</button>
              </div>
            </form>
            <div id="answers"></div>
          </section>
          <section class="view" data-view="quiz" hidden><div id="quiz-area"></div></section>
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
            <button role="menuitem" data-act="cards">${ICON.cards}Quiz and Anki flashcards</button>
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
    this.$('ask').addEventListener('submit', (e) => {
      e.preventDefault();
      const q = this.$('ask-input').value.trim();
      if (q.length < 2) return;
      this.$('ask-input').value = '';
      this.h.onAsk?.(q);
    });
    this.$('quiz-area').addEventListener('keydown', (e) => this.quizKey(e));
    this.wireComposer();
    this.$('body').addEventListener('click', (e) => {
      const act = e.target.closest('[data-study]')?.dataset.study;
      if (act) {
        e.preventDefault();
        this.studyAction(act, e.target.closest('[data-study]'));
        return;
      }
      const del = e.target.closest('[data-mine-del]');
      if (del) {
        e.preventDefault();
        // Twice to delete: the first click asks.
        if (del.dataset.armed === 'true') this.h.onMineDelete?.(del.dataset.mineDel);
        else { del.dataset.armed = 'true'; del.title = 'Click again to delete'; setTimeout(() => { del.dataset.armed = 'false'; }, 3000); }
        return;
      }
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
    // Each tab keeps its own place: back to the note where you left it, a new tab from the top.
    const body = this.$('body');
    this.scrolls = { ...this.scrolls, [this.view]: body.scrollTop };
    this.view = view;
    this.root.querySelectorAll('.tab').forEach((t) => t.setAttribute('aria-selected', String(t.dataset.view === view)));
    this.root.querySelectorAll('.view').forEach((v) => { v.hidden = v.dataset.view !== view; });
    body.scrollTop = this.scrolls[view] || 0;
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
    this.platform = meta.platform;
    const crumbs = [meta.course_title, meta.section_title && (meta.section_index ? `§${meta.section_index} ${meta.section_title}` : meta.section_title)]
      .filter(Boolean).join('  ·  ');
    this.$('crumbs').textContent = crumbs
      || (meta.platform === 'page' ? `Reading · ${meta.author || ''}` : platformName(meta.platform));
    const n = Number.isInteger(meta.lecture_index) ? `${meta.lecture_index} · ` : '';
    this.$('title').textContent = `${n}${meta.lecture_title || 'Untitled lecture'}`;
    this.cards.clear();
    this.$('timeline').innerHTML = '';
    this.$('live-empty').hidden = false;
    this.$('now').hidden = true;
    this.$('note-area').innerHTML = '';
    this.$('code-area').innerHTML = '';
    this.$('crux-area').innerHTML = '';
    this.$('answers').innerHTML = '';
    this.$('ask').hidden = true;
    this.$('quiz-area').innerHTML = '';
    this.quiz = null;
    this.$('mine-list').innerHTML = '';
    this.$('count-mine').textContent = '';
    this.$('mine-empty').hidden = false;
    this.mineSeen = null; // a new lecture's notes appear at once, without fading in
    if (this.mine) { this.mine.ctx = null; this.$('mine-at').textContent = ''; }
    this.scrolls = {}; // a new lecture starts every tab at the top
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
      // Fades in once, as it arrives. As a plain .card animation it replayed on
      // every card each time the Live tab was shown again.
      card.className = 'card entering';
      card.addEventListener('animationend', () => card.classList.remove('entering'), { once: true });
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
    this.math(article);
    await Promise.all([this.loadImages(article), this.drawMermaid(article)]);
  }

  math(scope) {
    if (typeof window.renderMathInElement !== 'function') return;
    window.renderMathInElement(scope, {
      delimiters: [{ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false }],
      throwOnError: false,
    });
  }

  // --- the crux, and asking the lecture ------------------------------------------------

  /**
   * state: 'later' (no notes yet), 'missing' (a note from before there was a
   * crux), 'making', or 'ready' with the crux markdown.
   */
  showCrux({ state, crux = '', engine = '' }) {
    const area = this.$('crux-area');
    if (state === 'later') {
      area.innerHTML = `<div class="empty"><strong>The crux comes with the notes.</strong>`
        + `When the lecture's notes are written, this is the 20% of it that gives 80% of the understanding: `
        + `the one idea, the few that matter most, and what to remember.</div>`;
      return;
    }
    if (state === 'missing' || state === 'making') {
      const making = state === 'making';
      area.innerHTML = `<div class="crux-intro"><strong>The 80/20 of this lecture</strong>`
        + `<span>This note was written before Margin made a crux. It takes one short read of the note.</span>`
        + `<button class="primary" data-study="make-crux" ${making ? 'disabled' : ''}>${making ? 'Finding the crux…' : 'Make the crux'}</button></div>`;
      return;
    }
    area.innerHTML = `<p class="crux-label">The 80/20 of this lecture</p><article class="note crux">${renderMarkdown(crux)}</article>`
      + (engine ? `<p class="note-meta">${escapeHtml(engine)}</p>` : '');
    const article = area.querySelector('.crux');
    // The lead sentence, the line to remember and the last line each get their own look.
    article.querySelector(':scope > p')?.classList.add('lead');
    for (const p of article.querySelectorAll(':scope > p')) {
      const start = p.textContent.trim();
      if (start.startsWith('Remember:')) p.classList.add('remember');
      if (start.startsWith('If you forget everything else:')) p.classList.add('last');
    }
    this.math(article);
  }

  /** Asking works as soon as there is a lecture to ask about, notes or not. */
  setAskable(yes) {
    this.$('ask').hidden = !yes;
  }

  /** A question, and its answer once it comes (null while waiting). */
  showAnswer(id, question, answer, { error = false } = {}) {
    let item = this.$('answers').querySelector(`[data-answer="${id}"]`);
    let created = false;
    if (!item) {
      item = document.createElement('div');
      item.className = 'answer';
      item.dataset.answer = id;
      this.$('answers').prepend(item);
      created = true;
    }
    const body = answer === null
      ? '<p class="thinking">Looking through the lecture…</p>'
      : error ? `<div class="error-box">${escapeHtml(answer)}</div>` : `<div class="note">${renderMarkdown(answer)}</div>`;
    item.innerHTML = `<p class="question">${escapeHtml(question)}</p>${body}`;
    this.math(item);
    // The answer lands below the crux: keep the question at the top of the view
    // as you ask, and again when the answer arrives and makes it taller.
    if (created || item === this.$('answers').firstElementChild) item.scrollIntoView({ block: 'start' });
  }

  // --- the quiz ------------------------------------------------------------------------

  /** state: 'later', 'none' (no cards yet), 'making', or 'ready' with cards. */
  showQuiz({ state, cards = [], stale = false }) {
    const area = this.$('quiz-area');
    if (state === 'later') {
      area.innerHTML = `<div class="empty"><strong>Flashcards come from the notes.</strong>`
        + `Once this lecture has notes, Margin turns them into cards to quiz yourself on, right here.</div>`;
      return;
    }
    if (state === 'none' || state === 'making') {
      const making = state === 'making';
      area.innerHTML = `<div class="quiz-intro">
          <strong>Quiz yourself on this lecture</strong>
          <span>Margin turns the note into flashcards: a question on the front, the answer on the back.
            Answer in your head, flip, and say whether you knew it. The ones you miss come back until you know them all.</span>
          <button class="primary" data-study="make-cards" ${making ? 'disabled' : ''}>${making ? 'Writing the flashcards…' : 'Make the flashcards'}</button>
        </div>${ANKI_HELP}`;
      return;
    }
    this.quiz = new Quiz(cards);
    this.quizStale = stale;
    this.drawQuiz();
  }

  drawQuiz() {
    const q = this.quiz;
    const area = this.$('quiz-area');
    const total = q.cards.length;
    const stale = this.quizStale
      ? '<p class="quiz-stale">The note changed since these were made. <a href="#" data-study="make-cards">Make new ones</a></p>' : '';
    const foot = `<div class="quiz-foot"><button class="primary" data-kind="quiet" data-study="export-cards">${ICON.download}Export to Anki</button></div>${ANKI_HELP}`;
    if (q.done) {
      const again = q.missed.size;
      area.innerHTML = `${stale}<div class="quiz-done">
          <strong>All ${total} cards done.</strong>
          <span>${again ? `${again} needed another look. Quiz again tomorrow and they will stick.` : 'You knew every one the first time.'}</span>
          <button class="primary" data-study="restart">Quiz again</button>
        </div>${foot}`;
      return;
    }
    const card = q.current;
    area.innerHTML = `${stale}
      <div class="quiz-top"><span>${q.known} of ${total} known</span><span>${q.toRepeat ? `${q.toRepeat} coming back` : ''}</span></div>
      <div class="meter quiz-meter"><i style="width:${Math.round((q.known / total) * 100)}%"></i></div>
      <div class="flashcard" data-study="flip" role="button" tabindex="0" data-flipped="${q.flipped}"
        aria-label="${q.flipped ? 'Answer shown' : 'Show the answer'}">
        <div class="face front"><span class="face-label">Question</span><div class="face-text">${renderMarkdown(card.front)}</div>
          <span class="face-hint">Space or click to flip</span></div>
        <div class="face back"><span class="face-label">Answer</span><div class="face-text">${renderMarkdown(card.back)}</div></div>
      </div>
      <div class="grade" ${q.flipped ? '' : 'hidden'}>
        <button class="primary" data-kind="quiet" data-study="again">Again <kbd>1</kbd></button>
        <button class="primary" data-study="knew">Got it <kbd>2</kbd></button>
      </div>${foot}`;
    this.math(area.querySelector('.flashcard'));
    if (this.view === 'quiz') area.querySelector('.flashcard').focus({ preventScroll: true });
  }

  quizKey(e) {
    if (!this.quiz || this.quiz.done || e.target.closest('input, textarea')) return;
    if (e.key === ' ' || e.key === 'Enter') this.studyAction('flip');
    else if (e.key === '1' && this.quiz.flipped) this.studyAction('again');
    else if (e.key === '2' && this.quiz.flipped) this.studyAction('knew');
    else return;
    e.preventDefault();
  }

  studyAction(act) {
    if (act === 'make-crux') this.h.onMakeCrux?.();
    else if (act === 'make-cards') this.h.onMakeCards?.();
    else if (act === 'export-cards') this.h.onExportCards?.();
    else if (!this.quiz) return;
    else if (act === 'flip') { this.quiz.flip(); this.drawQuiz(); }
    else if (act === 'again' || act === 'knew') { this.quiz.grade(act === 'knew'); this.drawQuiz(); }
    else if (act === 'restart') { this.quiz = new Quiz(this.quiz.cards); this.drawQuiz(); }
  }

  async loadImages(scope) {
    const imgs = [...scope.querySelectorAll('img[data-vault-path], audio[data-vault-path]')];
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

  // --- mine: your own notes -------------------------------------------------------------

  /** 'lecture', or 'page': a page that is not a lecture, where your notes are all there is. */
  setMode(mode) {
    this.mode = mode;
    this.panel.dataset.mode = mode;
    if (mode === 'page') this.show('mine');
    else if (this.view === 'mine' && !this.mineTouched) this.show('live');
  }

  wireComposer() {
    this.mine = { images: [], audio: null, ctx: null, quote: null, memo: null };
    const text = this.$('mine-text');
    text.addEventListener('input', () => this.mineStart());
    text.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); this.mineSubmit(); }
    });
    text.addEventListener('paste', (e) => {
      const files = [...(e.clipboardData?.items || [])].filter((i) => i.type.startsWith('image/')).map((i) => i.getAsFile());
      if (!files.length) return;
      e.preventDefault();
      this.mineAddPictures(files);
    });
    const form = this.$('mine-form');
    form.addEventListener('dragover', (e) => { e.preventDefault(); form.dataset.drop = 'true'; });
    form.addEventListener('dragleave', () => { form.dataset.drop = 'false'; });
    form.addEventListener('drop', (e) => {
      e.preventDefault();
      form.dataset.drop = 'false';
      this.mineAddPictures([...(e.dataTransfer?.files || [])].filter((f) => f.type.startsWith('image/')));
    });
    form.addEventListener('submit', (e) => { e.preventDefault(); this.mineSubmit(); });
    this.$('mine-pick').addEventListener('click', () => this.$('mine-file').click());
    this.$('mine-file').addEventListener('change', (e) => {
      this.mineAddPictures([...e.target.files]);
      e.target.value = '';
    });
    this.$('mine-voice').addEventListener('click', () => this.mineVoice());
    this.$('mine-quote-x').addEventListener('click', () => this.setQuote(null));
    this.$('mine-attach').addEventListener('click', (e) => {
      const drop = e.target.closest('[data-drop]');
      if (!drop) return;
      if (drop.dataset.drop === 'audio') this.mine.audio = null;
      else this.mine.images.splice(Number(drop.dataset.drop), 1);
      this.drawAttachments();
    });
  }

  /** The moment (or part of the page) a note belongs to: where you were when you began it. */
  mineStart() {
    this.mineTouched = true;
    if (this.mine.ctx) return;
    this.mine.ctx = this.h.onMineStart?.() || {};
    this.$('mine-at').textContent = this.mine.ctx.label || '';
  }

  /** Text you selected on the page, offered as a quote for your next note. */
  setQuote(quote) {
    this.mine.quote = quote;
    this.$('mine-quote').hidden = !quote;
    this.$('mine-quote-text').textContent = quote ? `“${quote.text}”` : '';
  }

  async mineAddPictures(files) {
    if (!files.length) return;
    this.mineStart();
    for (const file of files.slice(0, 8 - this.mine.images.length)) {
      if (file.size > 12e6) { this.toast('That picture is over 12 MB. Try a smaller screenshot.'); continue; }
      this.mine.images.push(await readAsDataUrl(file));
    }
    this.drawAttachments();
    this.$('mine-text').focus();
  }

  drawAttachments() {
    const box = this.$('mine-attach');
    const pics = this.mine.images.map((src, i) => `<span class="attach-pic"><img src="${escapeHtml(src)}" alt="Picture ${i + 1}">`
      + `<button type="button" class="chip-x" data-drop="${i}" aria-label="Remove this picture">${ICON.close}</button></span>`);
    const voice = this.mine.audio ? [`<span class="attach-voice">${ICON.mic}Voice note ${formatTs(this.mine.audio.seconds)}`
      + `<button type="button" class="chip-x" data-drop="audio" aria-label="Remove the voice note">${ICON.close}</button></span>`] : [];
    box.innerHTML = [...pics, ...voice].join('');
    box.hidden = !pics.length && !voice.length;
  }

  async mineVoice() {
    const button = this.$('mine-voice');
    if (this.mine.memo) {
      const memo = this.mine.memo;
      this.mine.memo = null;
      clearInterval(this.recTimer);
      button.setAttribute('aria-pressed', 'false');
      button.innerHTML = ICON.mic;
      this.$('mine-at').textContent = this.mine.ctx?.label || '';
      this.mine.audio = await memo.stop();
      // Said and done: with nothing else in it, the note is kept at once.
      if (!this.$('mine-text').value.trim() && !this.mine.images.length) this.mineSubmit();
      else this.drawAttachments();
      return;
    }
    if (!VoiceMemo.supported()) { this.toast('This browser cannot record here.'); return; }
    this.mineStart();
    const memo = new VoiceMemo();
    try {
      await memo.start();
    } catch (e) {
      this.toast(e?.name === 'NotAllowedError'
        ? 'Margin needs the microphone on this site: allow it from the icon in the address bar, then try again.'
        : `Could not start recording: ${e?.message || e}`, 7000);
      return;
    }
    this.mine.memo = memo;
    button.setAttribute('aria-pressed', 'true');
    button.innerHTML = ICON.stop;
    const at = this.$('mine-at');
    const show = () => {
      at.textContent = `Recording ${formatTs(memo.seconds)} · click to stop`;
      if (memo.seconds >= 600) this.mineVoice(); // ten minutes is a lecture, not a note
    };
    show();
    this.recTimer = setInterval(show, 500);
  }

  async mineSubmit() {
    if (this.mineSending) return;
    const m = this.mine;
    const text = this.$('mine-text').value.trim();
    if (!text && !m.images.length && !m.audio && !m.quote) {
      this.$('mine-text').focus();
      return;
    }
    // Everything travels in one message to the sidecar, which Chrome caps at 64 MB.
    const size = m.images.reduce((n, s) => n + s.length, 0) + (m.audio?.blob.size || 0) * 1.4;
    if (size > 45e6) {
      this.toast('That is too much for one note: add fewer pictures at a time.', 6000);
      return;
    }
    const payload = {
      text,
      quote: m.quote?.text || null,
      where: m.ctx?.where || m.quote?.where || null,
      t: Number.isFinite(m.ctx?.t) ? m.ctx.t : null,
      images: m.images,
      ...(m.audio ? { audio: await readAsDataUrl(m.audio.blob), audio_mime: m.audio.blob.type || 'audio/webm' } : {}),
    };
    this.mineSending = true;
    this.$('mine-add').disabled = true;
    this.$('mine-add').textContent = m.audio ? 'Writing it down…' : 'Adding…';
    const ok = await this.h.onMineAdd?.(payload);
    this.mineSending = false;
    this.$('mine-add').disabled = false;
    this.$('mine-add').textContent = 'Add';
    if (!ok) return; // kept in the box, to try again
    this.$('mine-text').value = '';
    this.mine.images = [];
    this.mine.audio = null;
    this.mine.ctx = null;
    this.$('mine-at').textContent = '';
    this.setQuote(null);
    this.drawAttachments();
  }

  /** Your notes, newest first. `fileUrl(name)` loads a picture or recording. */
  showMine(entries, fileUrl) {
    this.$('count-mine').textContent = entries.length ? String(entries.length) : '';
    this.$('mine-empty').hidden = entries.length > 0;
    const list = this.$('mine-list');
    const seen = this.mineSeen || new Set();
    list.innerHTML = [...entries].reverse().map((e) => {
      const when = Number.isFinite(e.t) ? `<button class="mine-time" data-seek="${Math.floor(e.t)}">${formatTs(e.t)}</button>`
        : e.where ? `<span class="mine-where">${escapeHtml(e.where)}</span>` : '<span></span>';
      // Fades in once, when it is new: not again each time the list is drawn.
      return `<article class="mine-item${this.mineSeen && !seen.has(e.id) ? ' entering' : ''}" data-mine="${escapeHtml(e.id)}">
        <div class="mine-head">${when}<button class="icon-btn mine-del" data-mine-del="${escapeHtml(e.id)}"
          aria-label="Delete this note" title="Delete">${ICON.trash}</button></div>
        ${e.quote ? `<blockquote class="mine-quote">${escapeHtml(e.quote)}</blockquote>` : ''}
        ${e.text ? `<div class="mine-text">${escapeHtml(e.text)}</div>` : ''}
        ${(e.images || []).map((n) => `<img class="mine-img" data-mine-file="${escapeHtml(n)}" alt="Your picture">`).join('')}
        ${e.audio ? `<audio controls preload="metadata" data-mine-file="${escapeHtml(e.audio)}"></audio>` : ''}
        ${e.transcript ? `<p class="mine-said">${escapeHtml(e.transcript)}</p>` : ''}
      </article>`;
    }).join('');
    this.mineSeen = new Set(entries.map((e) => e.id));
    list.querySelectorAll('[data-mine-file]').forEach(async (el) => {
      const res = await fileUrl(el.dataset.mineFile);
      if (res?.ok) el.src = res.dataUrl;
    });
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
      || `${count} lecture${s} you finished on ${platformName(this.platform)} ${count === 1 ? 'has' : 'have'} no notes. Margin can write `
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
  return { youtube: 'YouTube', udemy: 'Udemy', coursera: 'Coursera', deeplearning: 'DeepLearning.AI', local: 'Local video',
    page: 'Reading' }[p]
    || 'Web video';
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

function readAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
