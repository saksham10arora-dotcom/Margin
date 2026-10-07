// Your own notes, in the Mine tab: what you type, paste or say, and where it
// belongs (the moment of the lecture, or the part of the page you are on).
//
// Everything here runs in the page. The panel draws the tab; app.js sends
// each note to the sidecar, which keeps it and writes it into the note.

const clean = (s) => String(s || '').replace(/\s+/g, ' ').replace(/^[^\p{L}\p{N}]+/u, '').trim();

/**
 * A tab title without the site's tagline: "Computer Networks | Dotnotes |
 * Notes, Books" is "Computer Networks". The tab title, not og:title, which
 * single-page sites often leave as one tagline for every page.
 */
export function pageTitle(raw) {
  const full = clean(raw);
  const first = full.split(/\s+[|·–—-]\s+/)[0].trim();
  return first.length >= 4 ? first : full;
}

/** A page that is not a lecture, as Margin files it: under Reading, by its title and site. */
export function pageMeta(loc = location, doc = document) {
  const og = doc.querySelector('meta[property="og:title"]')?.content;
  const title = pageTitle(doc.title) || clean(doc.querySelector('h1')?.textContent) || clean(og) || loc.hostname;
  return {
    platform: 'page',
    url: loc.href.split('#')[0],
    lecture_id: `${loc.host}${loc.pathname}${loc.search}`,
    lecture_title: title.slice(0, 140),
    author: loc.hostname,
  };
}

// Headings of the page's chrome (a menu, a sidebar of chapters) are not where you are reading.
const CHROME = 'nav, aside, header, footer, [role="navigation"], [role="complementary"], [role="banner"], margin-panel';

/** The headings in the column `el` is in: a sidebar's headings are not the article's. */
function headings(doc, el) {
  let col = el;
  while (col && col !== doc.body && col.getBoundingClientRect().width < 280) col = col.parentElement;
  const box = col?.getBoundingClientRect();
  return [...doc.querySelectorAll('h1, h2, h3, h4')].filter((h) => {
    if (h.closest(CHROME) || !clean(h.textContent) || !h.getClientRects().length) return false;
    const r = h.getBoundingClientRect();
    return !box || (r.left < box.right && r.right > box.left);
  });
}

/** What you are looking at: the page under a point left of centre (the panel is on the right). */
function readingElement(doc, win) {
  for (const x of [0.4, 0.3, 0.55]) {
    const el = doc.elementFromPoint(win.innerWidth * x, win.innerHeight * 0.45);
    if (el && !el.closest('margin-panel')) return el;
  }
  return null;
}

/** The heading a point on the page falls under: the last one before it in its column. */
export function headingBefore(node, doc = document) {
  if (!node) return null;
  const el = node.nodeType === 1 ? node : node.parentElement;
  let found = null;
  for (const h of headings(doc, el)) {
    // eslint-disable-next-line no-bitwise
    if (h === node || h.contains(node) || (h.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)) found = h;
    else break;
  }
  return found ? clean(found.textContent).slice(0, 120) : null;
}

/** The heading of what you are reading: the last one above the top third of the window. */
export function headingInView(doc = document, win = window) {
  let found = null;
  for (const h of headings(doc, readingElement(doc, win))) {
    if (h.getBoundingClientRect().top <= win.innerHeight / 3) found = h;
    else break;
  }
  return found ? clean(found.textContent).slice(0, 120) : null;
}

/** Text you selected on the page (not in Margin's panel), or null. */
export function selectedText(doc = document) {
  const sel = doc.getSelection?.();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const anchor = sel.anchorNode;
  const el = anchor?.nodeType === 1 ? anchor : anchor?.parentElement;
  if (!el || el.closest('margin-panel')) return null;
  const text = sel.toString().replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  return text ? { text: text.slice(0, 2000), where: headingBefore(anchor, doc) } : null;
}

/**
 * A voice note: the microphone, recorded until you stop it. The browser asks
 * once per site whether this page may use the microphone.
 */
export class VoiceMemo {
  static supported() {
    return Boolean(globalThis.navigator?.mediaDevices?.getUserMedia && globalThis.MediaRecorder);
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
      .find((t) => MediaRecorder.isTypeSupported(t));
    this.rec = new MediaRecorder(this.stream, type ? { mimeType: type } : undefined);
    this.chunks = [];
    this.rec.ondataavailable = (e) => { if (e.data?.size) this.chunks.push(e.data); };
    this.startedAt = Date.now();
    this.rec.start(1000);
  }

  get seconds() {
    return this.startedAt ? Math.round((Date.now() - this.startedAt) / 1000) : 0;
  }

  /** Stops, and resolves to {blob, seconds}. */
  stop() {
    const seconds = this.seconds;
    return new Promise((resolve) => {
      this.rec.onstop = () => {
        this.release();
        resolve({ blob: new Blob(this.chunks, { type: this.rec.mimeType || 'audio/webm' }), seconds });
      };
      this.rec.stop();
    });
  }

  cancel() {
    try { this.rec?.stop(); } catch { /* already stopped */ }
    this.release();
  }

  release() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}
