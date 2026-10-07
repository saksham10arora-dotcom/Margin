// Your own notes, end to end: the real extension and sidecar, a real browser.
//
//   node scripts/e2e/mine-check.mjs <lecture-dir from make_lecture.py> <out-dir>
//
// 1. An article (no video): Margin opens on the toolbar click as a page you
//    read. Select a sentence, type a note, paste a picture, add it; record a
//    spoken voice note (a WAV made with `say` stands in for the microphone);
//    delete the first one. The note in Reading/ must follow every step.
// 2. A lecture: a note of yours at a moment of it waits for the lecture's
//    note, then is in it with its timestamp once Margin writes it.
// Nothing touches your real vault.

import { execFileSync, spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const lectureDir = path.resolve(process.argv[2] || '');
const outDir = path.resolve(process.argv[3] || path.join(os.tmpdir(), 'margin-mine-out'));
if (!existsSync(path.join(lectureDir, 'index.html'))) {
  console.error('usage: node scripts/e2e/mine-check.mjs <lecture-dir> <out-dir>');
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });
const PAGE_PORT = 8797;
const TEST_PORT = 8774;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-mine-${Date.now()}`);
const vault = path.join(work, 'vault');
mkdirSync(path.join(vault, '.obsidian'), { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.error('[mine-check]', ...a);

// The extension, pointed at this test's sidecar and allowed on the test pages.
const ext = path.join(work, 'extension');
cpSync(path.join(ROOT, 'extension'), ext, { recursive: true });
const manifest = JSON.parse(readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
manifest.host_permissions.push(`http://localhost:${PAGE_PORT}/*`, `${SIDECAR}/*`);
writeFileSync(path.join(ext, 'manifest.json'), JSON.stringify(manifest, null, 2));
const bg = path.join(ext, 'background.js');
writeFileSync(bg, readFileSync(bg, 'utf8').replace("'http://localhost:8766'", `'${SIDECAR}'`));

// A spoken voice note. The test browser's fake microphone records silence
// whatever file it is given, so the browser half proves recording, upload and
// that silence is not written down as words; the spoken recording, recorded
// the way Chrome records (Opus in WebM), proves Whisper writes down what was said.
const SAID = 'Remember that the sliding window keeps the link busy.';
const wav = path.join(work, 'memo.wav');
const spokenWebm = path.join(work, 'memo.webm');
execFileSync('say', ['-o', `${wav}.aiff`, SAID]);
execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-i', `${wav}.aiff`, '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', wav]);
execFileSync('ffmpeg', ['-loglevel', 'error', '-y', '-i', wav, '-c:a', 'libopus', '-b:a', '32k', spokenWebm]);

const ARTICLE = `<!doctype html><html><head><meta charset="utf-8"><title>Data Link Layer | Test Notes</title></head>
<body style="font:16px/1.6 Georgia;max-width:720px;margin:40px auto">
<h1>Data Link Layer</h1><p>Data link layer is the second layer of the OSI model.</p>
<h2>Flow control</h2>
<p id="flow">When a data frame is sent from one host to another over a single medium, the sender and receiver should work at the same speed.</p>
${'<p>Filler paragraph so the page scrolls.</p>'.repeat(30)}
<h2>Error control</h2><p>A frame may be lost in transit or received corrupted.</p></body></html>`;

const server = http.createServer((req, res) => {
  const url = decodeURIComponent(req.url.split('?')[0]);
  if (url === '/article.html') return void res.writeHead(200, { 'Content-Type': 'text/html' }).end(ARTICLE);
  const file = path.join(lectureDir, url.replace(/^\/lecture\//, '') || 'index.html');
  if (!url.startsWith('/lecture/') || !file.startsWith(lectureDir) || !existsSync(file)) return void res.writeHead(404).end();
  const data = readFileSync(file);
  const type = { '.html': 'text/html', '.mp4': 'video/mp4', '.vtt': 'text/vtt' }[path.extname(file)] || 'application/octet-stream';
  const range = req.headers.range?.match(/bytes=(\d+)-(\d*)/);
  if (!range) return void res.writeHead(200, { 'Content-Type': type, 'Accept-Ranges': 'bytes' }).end(data);
  const start = Number(range[1]);
  const end = range[2] ? Number(range[2]) : data.length - 1;
  res.writeHead(206, { 'Content-Type': type, 'Content-Range': `bytes ${start}-${end}/${data.length}`, 'Content-Length': end - start + 1 });
  res.end(data.subarray(start, end + 1));
}).listen(PAGE_PORT);

const side = spawn(path.join(ROOT, 'venv/bin/python'), ['-m', 'uvicorn', 'sidecar.main:app', '--port', String(TEST_PORT)], {
  cwd: ROOT, stdio: 'ignore',
  env: { ...process.env, MARGIN_VAULT_PATH: vault, MARGIN_SESSIONS_PATH: path.join(work, 'sessions') },
});
const get = async (p) => { try { const r = await fetch(SIDECAR + p); return r.ok ? r.json() : null; } catch { return null; } };
for (let i = 0; i < 60 && !(await get('/health')); i++) await sleep(500);

const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const exe = path.join(base, readdirSync(base).find((d) => d.startsWith('mac_arm-131')),
  'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser = await puppeteer.launch({ executablePath: exe, headless: 'new',
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--user-data-dir=${path.join(work, 'profile')}`,
    '--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
  ],
  defaultViewport: { width: 1440, height: 900 } });
const out = { steps: [] };
const step = (name, ok, detail = '') => { out.steps.push({ name, ok, detail }); log(ok ? 'ok  ' : 'FAIL', name, detail); };

// What the toolbar button does on a site Margin is not preinstalled on.
async function openMargin(page, urlPart) {
  const sw = await browser.waitForTarget((t) => t.type() === 'service_worker' && t.url().endsWith('background.js'));
  await (await sw.worker()).evaluate(async (part) => {
    const [tab] = await chrome.tabs.query({ url: `http://localhost:*/${part}*` });
    await chrome.scripting.executeScript({ target: { tabId: tab.id },
      files: ['vendor/marked.min.js', 'vendor/katex.min.js', 'vendor/auto-render.min.js', 'boot.js'] });
    await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
  }, urlPart);
}
const shadow = (page, js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
const until = async (fn, ms = 20000) => { for (let i = 0; i < ms / 250; i++) { const v = await fn(); if (v) return v; await sleep(250); } return null; };
const readingNote = () => {
  const dir = path.join(vault, 'Reading');
  const f = existsSync(dir) && readdirSync(dir).find((n) => n.endsWith('.md'));
  return f ? readFileSync(path.join(dir, f), 'utf8') : '';
};

try {
  // --- 1. an article -------------------------------------------------------------------
  const page = await browser.newPage();
  await page.goto(`http://localhost:${PAGE_PORT}/article.html`, { waitUntil: 'load' });
  await openMargin(page, 'article');
  const mode = await until(() => shadow(page, "return r.querySelector('.panel')?.dataset.mode"));
  step('opens as a page you read', mode === 'page', `mode=${mode}`);

  await page.evaluate(() => {
    const p = document.getElementById('flow');
    const range = document.createRange();
    range.setStart(p.firstChild, 0);
    range.setEnd(p.firstChild, 60);
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  });
  const quote = await until(() => shadow(page, "const q = r.getElementById('mine-quote'); return !q.hidden && q.textContent"));
  step('selected text is offered as a quote', Boolean(quote?.includes('When a data frame is sent')), quote?.trim().slice(0, 60));

  const box = await page.$('margin-panel >>> #mine-text');
  await box.type('Flow control: a fast sender must not swamp a slow receiver.');
  const at = await shadow(page, "return r.getElementById('mine-at').textContent");
  step('the note knows which part of the page it is on', at.includes('Flow control'), at);

  const png = readFileSync(path.join(ROOT, 'extension/icons/icon128.png')).toString('base64');
  await page.evaluate(`(() => {
    const r = document.querySelector('margin-panel').shadowRoot;
    const bytes = Uint8Array.from(atob('${png}'), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], 'shot.png', { type: 'image/png' }));
    r.getElementById('mine-text').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  })()`);
  const pics = await until(() => shadow(page, "return r.querySelectorAll('.attach-pic').length"));
  step('a pasted picture is attached', pics === 1, `pictures=${pics}`);

  await shadow(page, "r.getElementById('mine-add').click(); return true;");
  const first = await until(() => readingNote().includes('a fast sender must not swamp') && readingNote());
  step('added: in the note in Reading/', Boolean(first), first ? path.join('Reading', readdirSync(path.join(vault, 'Reading')).find((n) => n.endsWith('.md'))) : 'no note');
  step('with the quote, the heading and the picture',
    Boolean(first && first.includes('> When a data frame is sent') && first.includes('**Flow control:**') && /!\[\[assets\/[^\]]*-M[0-9a-f]+-1\.png\|480\]\]/.test(first)));
  step('the picture file is in the vault', existsSync(path.join(vault, 'Reading', 'assets'))
    && readdirSync(path.join(vault, 'Reading', 'assets')).some((n) => /-M[0-9a-f]+-1\.png$/.test(n)));
  const listed = await until(() => shadow(page, "return r.querySelectorAll('.mine-item').length"));
  step('shown in the Mine tab', listed === 1, `items=${listed}`);
  await page.screenshot({ path: path.join(outDir, 'mine-article.png') });

  // A spoken note: record, wait, stop. With nothing typed it is kept at once.
  await shadow(page, "r.getElementById('mine-voice').click(); return true;");
  const recording = await until(() => shadow(page, "return r.getElementById('mine-voice').getAttribute('aria-pressed') === 'true'"));
  step('recording starts', Boolean(recording));
  await sleep(5000);
  await shadow(page, "r.getElementById('mine-voice').click(); return true;");
  const spoken = await until(() => /M[0-9a-f]+\.(webm|ogg|m4a)\]\]/.test(readingNote()) && readingNote(), 60000);
  step('the recording is uploaded and embedded in the note', Boolean(spoken));
  step('silence is not written down as words', Boolean(spoken) && !/\*Said:\*/.test(spoken));
  const pageKey = 'page-solo-localhost_' + PAGE_PORT + '_article.html';
  const said = await (await fetch(`${SIDECAR}/session/${pageKey}/mine`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio: readFileSync(spokenWebm).toString('base64'), audio_mime: 'audio/webm;codecs=opus' }) })).json();
  step('a spoken voice note is written down by Whisper', /sliding window/i.test(said.entry?.transcript || ''), said.entry?.transcript);
  await shadow(page, "r.querySelector('.tab[data-view=\"mine\"]')?.click(); return true;");
  await until(() => shadow(page, "return r.querySelectorAll('.mine-item').length >= 2"));
  await page.screenshot({ path: path.join(outDir, 'mine-voice.png') });

  // Delete the first: twice to delete.
  const ids = ((await get(`/session/${pageKey}/mine`))?.entries || []).map((e) => e.id);
  await shadow(page, `r.querySelector('[data-mine-del="${ids[0]}"]').click(); return true;`);
  await shadow(page, `r.querySelector('[data-mine-del="${ids[0]}"]').click(); return true;`);
  const afterDelete = await until(() => !readingNote().includes('swamp') && readingNote());
  step('deleting one takes it out of the note, the others stay',
    Boolean(afterDelete && ids.length === 3 && afterDelete.includes(ids[1]) && afterDelete.includes(ids[2]) && !afterDelete.includes(ids[0])));
  step('and its picture out of the vault',
    !readdirSync(path.join(vault, 'Reading', 'assets')).some((n) => n.includes(`-${ids[0]}`)));
  const summary = await get(`/session/${encodeURIComponent('page-solo-localhost_' + PAGE_PORT + '_article.html')}`);
  step('the note is still Margin\'s own (not marked edited)', summary?.edited === false, `edited=${summary?.edited}`);
  out.readingNote = readingNote();

  // --- 2. a lecture ---------------------------------------------------------------------
  const lec = await browser.newPage();
  await lec.goto(`http://localhost:${PAGE_PORT}/lecture/index.html`, { waitUntil: 'load' });
  await lec.waitForFunction(() => document.querySelector('video')?.readyState >= 1, { timeout: 30000 });
  await openMargin(lec, 'lecture');
  for (let i = 0; i < 60 && !(await shadow(lec, "return /Capture/.test(r.getElementById('primary').textContent)")); i++) await sleep(500);
  await shadow(lec, "r.getElementById('primary').click(); return true;");
  await lec.evaluate(() => { const v = document.querySelector('video'); v.playbackRate = 2; v.play(); });
  await lec.waitForFunction(() => document.querySelector('video').currentTime > 20, { timeout: 60000 });
  await shadow(lec, "r.querySelector('.tab[data-view=\"mine\"]').click(); return true;");
  const lbox = await lec.$('margin-panel >>> #mine-text');
  await lbox.type('This is where the retrieval step is explained.');
  const lat = await shadow(lec, "return r.getElementById('mine-at').textContent");
  step('in a lecture, the note is stamped with the moment', /At \d\d:\d\d in the lecture/.test(lat), lat);
  await shadow(lec, "r.getElementById('mine-add').click(); return true;");
  const lkey = 'web-solo-localhost_' + PAGE_PORT + '_lecture_index.html';
  const kept = await until(async () => (await get(`/session/${lkey}/mine`))?.entries?.length === 1);
  step('kept before the lecture has a note', Boolean(kept));
  // Let the lecture finish: Auto writes the note, with yours in it.
  const duration = await lec.evaluate(() => document.querySelector('video').duration);
  await lec.waitForFunction((d) => document.querySelector('video').currentTime >= d - 0.5 || document.querySelector('video').ended,
    { timeout: 120000 }, duration);
  const done = await until(async () => (await get(`/session/${lkey}`))?.status?.state === 'done', 240000);
  const lnote = (() => {
    const dir = path.join(vault, 'Web');
    const f = existsSync(dir) && readdirSync(dir).find((n) => n.endsWith('.md'));
    return f ? readFileSync(path.join(dir, f), 'utf8') : '';
  })();
  step('the lecture\'s note is written', Boolean(done && lnote));
  step('with your note in "My notes", linked to its moment',
    /## My notes[\s\S]*\*\*\[00:\d\d\]\(http[^)]*\)\*\* This is where the retrieval step is explained\./.test(lnote));
  await shadow(lec, "r.querySelector('.tab[data-view=\"note\"]').click(); return true;");
  await sleep(1500);
  await lec.screenshot({ path: path.join(outDir, 'mine-lecture-note.png') });
  out.lectureNoteTail = lnote.slice(lnote.indexOf('## My notes'));
} catch (e) {
  step('ran to the end', false, String(e?.stack || e));
} finally {
  await browser.close();
  side.kill();
  server.close();
}
out.ok = out.steps.every((s) => s.ok);
writeFileSync(path.join(outDir, 'mine-results.json'), JSON.stringify(out, null, 1));
rmSync(work, { recursive: true, force: true });
console.log(JSON.stringify({ ok: out.ok, failed: out.steps.filter((s) => !s.ok) }, null, 1));
process.exit(out.ok ? 0 : 1);
