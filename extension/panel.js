// extension/panel.js
import { batchTranscript, formatTimestampLink } from './transcript.js';
import { renderMarkdown, renderMathInElement } from './renderer.js';

const SIDECAR_URL = 'http://localhost:8765';
const BATCH_DURATION_SEC = 60;

let state = {
  active: false,
  videoMeta: null,
  allCues: [],
  lastBatchedSec: 0,
  isFirstChunk: true,
  pollIntervalId: null,
  liveFilename: null, // vault-relative filename of the note being recorded
  viewingFilename: null, // set while viewing a saved file instead of the live note
  viewingSource: null,
};

let savedBodyHtml = null; // live-notes view, stashed while viewing a saved file

// Whichever note the panel is currently showing — the saved file being
// viewed, if any, otherwise the note the live session is recording into.
function currentFocus() {
  if (state.viewingFilename) {
    return { filename: state.viewingFilename, source: state.viewingSource };
  }
  if (state.liveFilename) {
    return { filename: state.liveFilename, source: state.videoMeta?.video_url ?? null };
  }
  return null;
}

function buildObsidianUri(filename) {
  // The sidecar's VAULT_PATH (sidecar/config.py) is the notesyt/ subfolder,
  // so filenames it returns are relative to notesyt/, not to the Obsidian
  // vault root ("obs") that obsidian://open expects paths relative to.
  const relPath = `notesyt/${filename}`.replace(/\.md$/, '');
  return `obsidian://open?vault=${encodeURIComponent('obs')}&file=${encodeURIComponent(relPath)}`;
}

async function copyCurrentNote() {
  const focus = currentFocus();
  if (!focus) return;
  const response = await fetch(
    `${SIDECAR_URL}/documents/content?filename=${encodeURIComponent(focus.filename)}`,
  );
  if (!response.ok) return;
  const { content } = await response.json();
  await navigator.clipboard.writeText(content);
}

async function downloadCurrentNote() {
  const focus = currentFocus();
  if (!focus) return;
  const response = await fetch(
    `${SIDECAR_URL}/documents/content?filename=${encodeURIComponent(focus.filename)}`,
  );
  if (!response.ok) return;
  const { content } = await response.json();
  const blob = new Blob([content], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = focus.filename.split('/').pop();
  a.click();
  URL.revokeObjectURL(url);
}

function openCurrentVideo() {
  const focus = currentFocus();
  if (focus?.source) window.open(focus.source, '_blank');
}

async function exportFlashcards() {
  const focus = currentFocus();
  if (!focus) return;
  const btn = document.getElementById('hn-action-flashcards');
  btn?.classList.add('hn-busy');
  const notice = appendNotice('Generating flashcards…');
  try {
    const response = await fetch(`${SIDECAR_URL}/export/flashcards`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: focus.filename }),
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
    }
    const { count, tsv } = await response.json();
    // Anki imports .txt/.tsv directly, so hand the user a file rather than
    // making them copy text out of a panel into a text editor first.
    const blob = new Blob([tsv], { type: 'text/tab-separated-values' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${focus.filename.split('/').pop().replace(/\.md$/, '')}-flashcards.txt`;
    a.click();
    URL.revokeObjectURL(url);
    if (notice) notice.textContent = `${count} flashcards downloaded — import the .txt into Anki.`;
  } catch (err) {
    console.error('[Margin] flashcard export failed:', err);
    if (notice) notice.textContent = `Flashcard export failed: ${err.message}`;
  } finally {
    btn?.classList.remove('hn-busy');
  }
}

function openCurrentInObsidian() {
  const focus = currentFocus();
  if (focus) window.open(buildObsidianUri(focus.filename), '_blank');
}

// Strips a leading YAML frontmatter block before markdown rendering —
// the raw file otherwise renders as a literal "---" horizontal rule
// followed by a "title: ..." paragraph.
function stripFrontmatter(content) {
  return content.replace(/^---\n[\s\S]*?\n---\n/, '');
}

// Image paths in vault notes (e.g. "hover-notes-images/foo.jpg") are
// relative to the note's own folder on disk, per Obsidian's same-folder
// attachment convention — not reachable over HTTP as-is. Rewrite them to
// the sidecar's static file mount so the in-panel viewer can load them.
function resolveImagePaths(markdown, noteFilename) {
  const noteDir = noteFilename.includes('/')
    ? noteFilename.slice(0, noteFilename.lastIndexOf('/'))
    : '';
  return markdown.replace(/!\[([^\]]*)\]\((?!https?:\/\/)([^)]+)\)/g, (match, alt, src) => {
    const relPath = noteDir ? `${noteDir}/${src}` : src;
    return `![${alt}](${SIDECAR_URL}/vault-files/${relPath.split('/').map(encodeURIComponent).join('/')})`;
  });
}

async function viewFileInPanel(filename, source) {
  const body = document.getElementById('hn-body');
  if (!body) return;
  if (savedBodyHtml === null) savedBodyHtml = body.innerHTML;
  state.viewingFilename = filename;
  state.viewingSource = source ?? null;
  body.innerHTML = '<p class="hn-viewer-loading">Loading…</p>';

  try {
    const response = await fetch(
      `${SIDECAR_URL}/documents/content?filename=${encodeURIComponent(filename)}`,
    );
    if (!response.ok) throw new Error(`Sidecar returned HTTP ${response.status}`);
    const { content } = await response.json();

    body.innerHTML = '<div class="hn-viewer-bar"><button id="hn-viewer-back">&larr; Back to live notes</button></div>';
    const section = document.createElement('div');
    section.className = 'hn-section';
    section.innerHTML = renderMarkdown(resolveImagePaths(stripFrontmatter(content), filename));
    body.appendChild(section);
    renderMathInElement(section);
    document.getElementById('hn-viewer-back').addEventListener('click', restoreLiveNotesView);
  } catch (err) {
    body.innerHTML = '<div class="hn-viewer-bar"><button id="hn-viewer-back">&larr; Back to live notes</button></div>';
    document.getElementById('hn-viewer-back').addEventListener('click', restoreLiveNotesView);
    appendError(`Failed to load document: ${err.message}`);
  }
}

function restoreLiveNotesView() {
  const body = document.getElementById('hn-body');
  if (!body || savedBodyHtml === null) return;
  body.innerHTML = savedBodyHtml;
  savedBodyHtml = null;
  state.viewingFilename = null;
  state.viewingSource = null;
}

function getVideoMeta() {
  const video = document.querySelector('video');
  const titleEl = document.querySelector('h1.ytd-watch-metadata yt-formatted-string');
  const authorEl = document.querySelector('#channel-name a');
  return {
    video_id: new URLSearchParams(location.search).get('v'),
    video_title: titleEl ? titleEl.textContent.trim() : document.title,
    video_url: location.href.split('&')[0],
    author: authorEl ? authorEl.textContent.trim() : 'Unknown',
    duration_sec: video ? Math.round(video.duration) : 0,
    videoElement: video,
  };
}

const ICON_PLAY =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 3 14 9-14 9V3z"/></svg>';
const ICON_STOP =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>';

function appendSectionMarkup(container, markdown) {
  if (container.querySelector('.hn-empty')) {
    container.innerHTML = '';
  }
  const section = document.createElement('div');
  section.className = 'hn-section';
  // renderMarkdown (Task 9) turns ## headers, bold bullets, and links into real
  // HTML. renderMathInElement then walks the freshly-inserted DOM and swaps any
  // $...$ / $$...$$ delimiters for rendered KaTeX — it has to run after the HTML
  // is actually in the document, not on the raw string, which is why these are
  // two separate calls rather than one.
  section.innerHTML = renderMarkdown(markdown);
  container.appendChild(section);
  renderMathInElement(section);
}

export function renderSection(markdown) {
  const body = document.getElementById('hn-body');
  if (!body) return; // panel was closed while this response was in flight

  // Same fix as viewFileInPanel(): image paths in Gemini's markdown are
  // relative to the note's own folder on disk, not reachable as-is from
  // the youtube.com origin this script runs on.
  if (state.liveFilename) {
    markdown = resolveImagePaths(markdown, state.liveFilename);
  }

  if (savedBodyHtml !== null) {
    // User is viewing a saved file right now — apply the new section to the
    // stashed live-notes HTML instead of the visible viewer, so it's there
    // when they go back instead of being lost or painted into the wrong view.
    const scratch = document.createElement('div');
    scratch.innerHTML = savedBodyHtml;
    appendSectionMarkup(scratch, markdown);
    savedBodyHtml = scratch.innerHTML;
    return;
  }

  appendSectionMarkup(body, markdown);
  body.scrollTop = body.scrollHeight;
}

function showRecordingIndicator(filename) {
  state.liveFilename = filename;
  const row = document.getElementById('hn-recording');
  const nameEl = document.getElementById('hn-recording-name');
  if (!row || !nameEl) return;
  nameEl.textContent = filename.split('/').pop();
  row.classList.add('hn-visible');
}

function appendError(message) {
  const body = document.getElementById('hn-body');
  if (!body) return; // panel was closed while this response was in flight
  if (body.querySelector('.hn-empty')) body.innerHTML = '';
  const notice = document.createElement('p');
  notice.className = 'hn-error';
  notice.textContent = message;
  body.appendChild(notice);
}

function appendNotice(message) {
  const body = document.getElementById('hn-body');
  if (!body) return;
  const notice = document.createElement('p');
  notice.className = 'hn-notice';
  notice.textContent = message;
  body.appendChild(notice);
  body.scrollTop = body.scrollHeight;
  return notice;
}

async function finalizeNote(filename) {
  const notice = appendNotice('Finalizing notes (rebuilding with full video context)…');
  try {
    const response = await fetch(`${SIDECAR_URL}/finalize-note`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      throw new Error(`Sidecar returned HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
    }
    if (notice) notice.textContent = 'Notes rebuilt with full video context — reopen the note to see them.';
  } catch (err) {
    console.error('[Margin] finalize-note request failed:', err);
    if (notice) notice.textContent = `Finalize failed: ${err.message}`;
  }
}

async function sendChunk(chunk) {
  const meta = state.videoMeta;
  const toTimestamp = (sec) => {
    const h = Math.floor(sec / 3600).toString().padStart(2, '0');
    const m = Math.floor((sec % 3600) / 60).toString().padStart(2, '0');
    const s = Math.floor(sec % 60).toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
  };

  try {
    const response = await fetch(`${SIDECAR_URL}/note-chunk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: meta.video_id,
        video_title: meta.video_title,
        video_url: meta.video_url,
        author: meta.author,
        duration_sec: meta.duration_sec,
        transcript_chunk: chunk.text,
        start_ts: toTimestamp(chunk.startSec),
        end_ts: toTimestamp(chunk.endSec),
        is_first_chunk: state.isFirstChunk,
        transcript_language: state.transcriptLanguage || 'English',
      }),
    });
    state.isFirstChunk = false;
    if (!response.ok) {
      const detail = await response.text().catch(() => '');
      throw new Error(`Sidecar returned HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
    }
    const data = await response.json();
    if (data.filename) showRecordingIndicator(data.filename);
    if (data.rendered_section) renderSection(data.rendered_section);
  } catch (err) {
    console.error('[Margin] note-chunk request failed:', err);
    appendError(
      err instanceof TypeError
        ? 'Sidecar unreachable at localhost:8765 — is it running?'
        : `Sidecar error: ${err.message}`,
    );
  }
}

function pollForNewBatches() {
  const currentSec = state.videoMeta.videoElement?.currentTime ?? 0;
  if (currentSec - state.lastBatchedSec < BATCH_DURATION_SEC) return;

  const batches = batchTranscript(state.allCues, BATCH_DURATION_SEC);
  const newBatch = batches.find((b) => b.startSec >= state.lastBatchedSec);
  if (newBatch) {
    newBatch.endSec = currentSec;
    sendChunk(newBatch);
    state.lastBatchedSec = currentSec;
  }
}

export async function startAiNotes() {
  const { scrapeTranscript } = await import('./transcript.js');
  state.videoMeta = getVideoMeta();
  const transcript = await scrapeTranscript(state.videoMeta.video_id);
  state.allCues = transcript.cues;
  state.transcriptLanguage = transcript.language;
  state.lastBatchedSec = 0;
  state.isFirstChunk = true;
  state.active = true;

  if (state.allCues.length === 0) {
    appendError('No transcript available for this video.');
    state.active = false;
    return;
  }

  state.pollIntervalId = setInterval(pollForNewBatches, 5000);
}

export function stopAiNotes() {
  state.active = false;
  if (state.pollIntervalId) clearInterval(state.pollIntervalId);
  // Fire-and-forget: the toggle button's own UI update shouldn't wait on a
  // Gemini round-trip. Only finalize if at least one section was actually
  // written -- no point summarizing an empty note.
  if (state.liveFilename) finalizeNote(state.liveFilename);
}

export function wirePanelButtons() {
  const toggleBtn = document.getElementById('hn-toggle');
  const toggleLabel = document.getElementById('hn-toggle-label');
  const toggleIcon = toggleBtn.querySelector('.hn-btn-icon');

  toggleBtn.addEventListener('click', () => {
    if (state.active) {
      stopAiNotes();
      toggleLabel.textContent = 'Start AI Notes';
      toggleIcon.innerHTML = ICON_PLAY;
      toggleBtn.classList.remove('hn-active');
      toggleBtn.classList.add('hn-btn-primary');
    } else {
      startAiNotes();
      toggleLabel.textContent = 'Stop AI Notes';
      toggleIcon.innerHTML = ICON_STOP;
      toggleBtn.classList.remove('hn-btn-primary');
      toggleBtn.classList.add('hn-active');
    }
  });

  document.getElementById('hn-action-video')?.addEventListener('click', openCurrentVideo);
  document.getElementById('hn-action-copy')?.addEventListener('click', copyCurrentNote);
  document.getElementById('hn-action-download')?.addEventListener('click', downloadCurrentNote);
  document.getElementById('hn-action-history')?.addEventListener('click', () => openFilesModal());
  document.getElementById('hn-action-flashcards')?.addEventListener('click', exportFlashcards);
  document.getElementById('hn-action-obsidian')?.addEventListener('click', openCurrentInObsidian);
}

document.addEventListener('keydown', (e) => {
  const isCmdJ = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'j';
  if (!isCmdJ || !state.active) return;
  e.preventDefault();

  const currentSec = state.videoMeta.videoElement?.currentTime ?? 0;
  const link = formatTimestampLink(currentSec, state.videoMeta.video_url);
  const body = document.getElementById('hn-body');
  if (body.querySelector('.hn-empty')) body.innerHTML = '';
  const marker = document.createElement('p');
  // Same reasoning as renderSection() (Task 10): this is markdown link syntax,
  // not plain text — textContent would show literal [HH:MM:SS](url) instead of a
  // clickable link.
  marker.innerHTML = renderMarkdown(`📍 ${link}`);
  body.appendChild(marker);
  body.scrollTop = body.scrollHeight;
});

// HTML-escape untrusted text before it is interpolated into innerHTML.
// doc.title (from video_title, scraped raw off a YouTube page's DOM) and the
// documentDomain() fallback (raw source string when new URL() throws) are
// both attacker-controlled. renderer.js's escapeHtmlTags() only escapes
// < and > because its output is fed through marked.parse() afterward, which
// itself entity-escapes & — that pipeline doesn't apply here, since these
// values are dropped straight into a template-literal innerHTML string with
// no markdown pass, so & and " need escaping too.
function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function documentDomain(sourceUrl) {
  try {
    return new URL(sourceUrl).hostname.replace('www.', '');
  } catch {
    return sourceUrl;
  }
}

async function fetchDocuments(query) {
  const url = query
    ? `${SIDECAR_URL}/documents/search?q=${encodeURIComponent(query)}`
    : `${SIDECAR_URL}/documents`;
  const response = await fetch(url);
  return response.json();
}

function renderDocumentList(listEl, docs) {
  listEl.innerHTML = '';
  if (docs.length === 0) {
    listEl.innerHTML = '<p style="color: #93939c; font-size: 12px; padding: 8px 4px;">No notes found.</p>';
    return;
  }
  for (const doc of docs) {
    const item = document.createElement('div');
    item.className = 'hn-modal-item';
    item.innerHTML = `
      <div>
        <div class="hn-modal-item-title">${escapeHtml(doc.title)}</div>
        <div class="hn-modal-item-meta">${escapeHtml(doc.created)} · ${escapeHtml(documentDomain(doc.source))}</div>
      </div>
      <button data-filename="${escapeHtml(doc.filename)}" data-source="${escapeHtml(doc.source)}">Open</button>
    `;
    listEl.appendChild(item);
  }
}

export async function openFilesModal() {
  if (document.querySelector('.hn-modal-overlay')) return;

  const overlay = document.createElement('div');
  overlay.className = 'hn-modal-overlay';
  overlay.innerHTML = `
    <div class="hn-modal">
      <h3 style="margin-top: 0;">Markdown Files</h3>
      <input type="text" id="hn-search" placeholder="Search files..." />
      <div class="hn-modal-list" id="hn-modal-list"></div>
    </div>
  `;
  document.body.appendChild(overlay);

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });

  const listEl = overlay.querySelector('#hn-modal-list');
  const searchInput = overlay.querySelector('#hn-search');

  // Event delegation: the "Open" buttons are re-created on every refresh(),
  // so a single listener on the stable list container (rather than one
  // per-item listener that would need re-wiring after each render) handles
  // clicks for the button's whole lifetime.
  listEl.addEventListener('click', (e) => {
    const filename = e.target.dataset.filename;
    if (!filename) return;
    closeFilesModal();
    viewFileInPanel(filename, e.target.dataset.source);
  });

  const refresh = async (query) => renderDocumentList(listEl, await fetchDocuments(query));
  await refresh('');

  let debounceId;
  searchInput.addEventListener('input', () => {
    clearTimeout(debounceId);
    debounceId = setTimeout(() => refresh(searchInput.value), 200);
  });
}

export function closeFilesModal() {
  document.querySelector('.hn-modal-overlay')?.remove();
}
