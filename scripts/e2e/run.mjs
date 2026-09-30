// End-to-end: the real extension, a real browser, a real lecture, real Gemini.
//
//   node scripts/e2e/run.mjs <lecture-dir> <out-dir> [--no-compose]
//
// Starts its own sidecar against a throwaway vault and sessions folder,
// launches Chrome for Testing with Margin loaded, opens the lecture page,
// opens Margin, presses Capture, plays the lecture at 2x to the end and lets
// Auto compose it. Then it screenshots each tab of the panel and prints what
// was captured and written. Nothing touches your real vault.

import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [lectureDir, outDir] = process.argv.slice(2);
const noCompose = process.argv.includes('--no-compose');
// --hidpi: screenshots at twice the pixels, for the README and the site.
// --record: video of the lecture playing (rec-play.webm) and of a tour through
// the finished note and code (rec-note.webm), for the README's GIF.
const record = process.argv.includes('--record');
const FFMPEG = process.env.FFMPEG || 'ffmpeg';
// A real site instead of the generated lecture: --url mode plays for
// --seconds of video (at 2x), then presses "Write notes now".
const isUrl = /^https?:/.test(lectureDir || '');
const playSeconds = Number((process.argv.find((a) => a.startsWith('--seconds=')) || '--seconds=90').split('=')[1]);
if (!lectureDir || !outDir) {
  console.error('usage: node scripts/e2e/run.mjs <lecture-dir> <out-dir>');
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });

const PAGE_PORT = 8799;
// Its own port, so a test never touches the sidecar you are actually using.
const TEST_PORT = 8767;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-e2e-${Date.now()}`);
const vault = path.join(work, 'vault');
mkdirSync(path.join(vault, '.obsidian'), { recursive: true });
const log = (...a) => console.log(`[e2e ${new Date().toISOString().slice(11, 19)}]`, ...a);

function findChrome() {
  if (process.env.E2E_CHROME) return process.env.E2E_CHROME;
  // Headless screenshots hang on this Mac with Chrome for Testing 147+ (even
  // for a blank page), so the harness prefers 131, which works, then newest.
  const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
  const major = (d) => Number(d.split('-')[1].split('.')[0]);
  const versions = readdirSync(base).filter((d) => d.startsWith('mac_arm-'))
    .sort((a, b) => (major(a) === 131 ? -1 : major(b) === 131 ? 1 : major(b) - major(a)));
  for (const v of versions) {
    const p = path.join(base, v, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
    if (existsSync(p)) return p;
  }
  throw new Error('No Chrome for Testing found in ~/.cache/puppeteer/chrome');
}

// A copy of the extension that is also allowed on the test page's origin.
function testExtension() {
  const dir = path.join(work, 'extension');
  cpSync(path.join(ROOT, 'extension'), dir, { recursive: true });
  const manifest = JSON.parse(readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  manifest.host_permissions.push(`http://localhost:${PAGE_PORT}/*`, `http://localhost:${TEST_PORT}/*`);
  const bg = path.join(dir, 'background.js');
  writeFileSync(bg, readFileSync(bg, 'utf8').replace("'http://localhost:8766'", `'http://localhost:${TEST_PORT}'`));
  manifest.content_scripts[0].matches.push(`http://localhost:${PAGE_PORT}/*`);
  writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
  return dir;
}

function serve(dir) {
  const types = { '.html': 'text/html', '.mp4': 'video/mp4', '.vtt': 'text/vtt' };
  return http.createServer((req, res) => {
    const file = path.join(dir, decodeURIComponent(req.url.split('?')[0]).replace(/^\//, '') || 'index.html');
    if (!file.startsWith(dir) || !existsSync(file)) { res.writeHead(404).end(); return; }
    const data = readFileSync(file);
    const range = req.headers.range?.match(/bytes=(\d+)-(\d*)/);
    const headers = { 'Content-Type': types[path.extname(file)] || 'application/octet-stream',
      'Accept-Ranges': 'bytes', 'Access-Control-Allow-Origin': '*' };
    if (range) {
      const start = Number(range[1]);
      const end = range[2] ? Number(range[2]) : data.length - 1;
      res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${data.length}`, 'Content-Length': end - start + 1 });
      res.end(data.subarray(start, end + 1));
    } else {
      res.writeHead(200, { ...headers, 'Content-Length': data.length });
      res.end(data);
    }
  }).listen(PAGE_PORT);
}

async function waitFor(fn, { timeout = 60000, every = 500, what = 'condition' } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const v = await fn().catch(() => null);
    if (v) return v;
    await new Promise((r) => setTimeout(r, every));
  }
  throw new Error(`Timed out waiting for ${what}`);
}

async function shot(page, file) {
  try {
    await Promise.race([
      page.screenshot({ path: file, captureBeyondViewport: false }),
      new Promise((_, reject) => setTimeout(() => reject(new Error('screenshot timeout')), 20000)),
    ]);
  } catch (e) {
    console.log('[e2e] screenshot skipped:', path.basename(file), e.message);
  }
}

async function sidecar(pathname) {
  const r = await fetch(SIDECAR + pathname);
  return r.ok ? r.json() : null;
}

const results = { ok: false };
let side;
let server;
let browser;
try {
  side = spawn(path.join(ROOT, 'venv/bin/python'), ['-m', 'uvicorn', 'sidecar.main:app', '--port', String(TEST_PORT)], {
    cwd: ROOT,
    env: { ...process.env, MARGIN_VAULT_PATH: vault, MARGIN_SESSIONS_PATH: path.join(work, 'sessions'), MARGIN_KEEP_AUDIO: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const sideLog = [];
  side.stdout.on('data', (d) => sideLog.push(String(d)));
  side.stderr.on('data', (d) => sideLog.push(String(d)));
  results.health = await waitFor(() => sidecar('/health'), { what: 'sidecar' });
  log('sidecar up:', JSON.stringify(results.health));

  if (!isUrl) server = serve(path.resolve(lectureDir));
  browser = await puppeteer.launch({
    executablePath: findChrome(),
    headless: 'new',
    args: [
      `--disable-extensions-except=${testExtension()}`,
      `--load-extension=${path.join(work, 'extension')}`,
      '--autoplay-policy=no-user-gesture-required',
      '--window-size=1500,920',
      `--user-data-dir=${path.join(work, 'profile')}`,
    ],
    defaultViewport: { width: 1500, height: 920, deviceScaleFactor: process.argv.includes('--hidpi') ? 2 : 1 },
    protocolTimeout: 60000,
  });
  const page = await browser.newPage();
  const consoleLines = [];
  page.on('console', (m) => consoleLines.push(`${m.type()}: ${m.text()}`));
  page.on('pageerror', (e) => consoleLines.push(`pageerror: ${e.message}`));
  await page.goto(isUrl ? lectureDir : `http://localhost:${PAGE_PORT}/index.html`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1, { timeout: 30000 });

  if (!isUrl) {
    // Open Margin the way the toolbar button does: a message from the worker.
    const swTarget = await browser.waitForTarget((t) => t.type() === 'service_worker' && t.url().endsWith('background.js'));
    const worker = await swTarget.worker();
    await worker.evaluate(async (port) => {
      const [tab] = await chrome.tabs.query({ url: `http://localhost:${port}/*` });
      await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
    }, PAGE_PORT);
  }

  const shadow = (js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
  // Where Margin starts as a tab on the edge (YouTube), open it like a person would.
  await waitFor(() => shadow("return !!r.querySelector('.panel')"), { what: 'panel mounted' });
  await shadow("if (r.querySelector('.panel').hidden) r.getElementById('pill').click(); return true;");
  await waitFor(() => shadow("return r.getElementById('title')?.textContent.trim().length > 2"), { what: 'panel with a lecture' });
  await waitFor(() => shadow("return !/Checking/.test(r.getElementById('sig-speech').textContent)"), { what: 'speech source' });
  results.title = await shadow("return r.getElementById('title').textContent");
  results.speech = await shadow("return r.getElementById('sig-speech').textContent");
  log('panel open:', results.title, '|', results.speech);
  await shot(page, path.join(outDir, '0-opened.png'));

  // Capture, then play at 2x to the end.
  const playing = record ? await page.screencast({ path: path.join(outDir, 'rec-play.webm'), ffmpegPath: FFMPEG }) : null;
  // Opt-in sites (YouTube, the generated lecture) wait for Capture. Course
  // sites capture as soon as the lecture plays; their button already says
  // "Write notes now", which must not be pressed yet.
  await shadow("const b = r.getElementById('primary'); if (/Capture/.test(b.textContent)) b.click(); return true;");
  await page.evaluate(() => { const v = document.querySelector('video'); v.playbackRate = 2; v.play(); });
  const duration = await page.evaluate(() => document.querySelector('video').duration);
  const target = isUrl ? Math.min(playSeconds, duration) : duration;
  log(`playing ${target.toFixed(0)}s of ${duration.toFixed(0)}s at 2x`);
  const until = Date.now() + (target / 2 + 40) * 1000;
  while (Date.now() < until) {
    const st = await page.evaluate(() => ({ ended: document.querySelector('video').ended,
      t: document.querySelector('video').currentTime.toFixed(1),
      diag: document.querySelector('margin-panel')?.dataset.diag }));
    log('t=' + st.t, st.diag || '(no diag)');
    if (st.ended || (isUrl && Number(st.t) >= target)) break;
    await new Promise((r) => setTimeout(r, 5000));
  }
  if (isUrl) {
    await page.evaluate(() => document.querySelector('video').pause());
    await new Promise((r) => setTimeout(r, 2000));
    await shadow("r.getElementById('primary').click(); return true;"); // Write notes now
  }
  await new Promise((r) => setTimeout(r, 3000));
  await playing?.stop();
  results.slides = await shadow("return r.getElementById('sig-slides').textContent");
  results.watched = await shadow("return r.getElementById('sig-watched').textContent");
  results.speechAfter = await shadow("return r.getElementById('sig-speech').textContent");
  log('played:', results.slides, 'slides,', results.watched, 'watched,', results.speechAfter);
  await shadow("r.querySelector('.tab[data-view=live]').click(); return true;");
  await shot(page, path.join(outDir, '1-live.png'));

  const key = isUrl
    ? await page.evaluate(() => {
      const dlai = location.pathname.match(/^\/courses\/([^/]+)\/lesson\/([^/]+)/);
      if (dlai) return `deeplearning-${dlai[1]}-${dlai[2]}`;
      return `youtube-solo-${new URLSearchParams(location.search).get('v')}`;
    })
    : `web-solo-localhost_${PAGE_PORT}_index.html`;
  results.session = await sidecar(`/session/${key}`);
  if (!noCompose) {
    const status = await waitFor(async () => {
      const s = await sidecar(`/session/${key}`);
      return s && ['done', 'error'].includes(s.status.state) ? s.status : null;
    }, { timeout: 600000, every: 2000, what: 'compose (auto on end)' });
    results.status = status;
    log('compose:', status.state, status.message);
    if (status.state === 'done') {
      await shadow("r.querySelector('.tab[data-view=note]').click(); return true;");
      await waitFor(() => shadow("return !!r.querySelector('.note h2')"), { what: 'rendered note', timeout: 30000 });
      await new Promise((r) => setTimeout(r, 2500)); // mermaid + images
      const shots = [['2-notes-top', 0], ['3-notes-mid', 700], ['4-notes-more', 1400], ['5-notes-end', 99999]];
      for (const [name, y] of shots) {
        await shadow(`r.getElementById('body').scrollTop = ${y}; return true;`);
        await new Promise((r) => setTimeout(r, 400));
        await shot(page, path.join(outDir, `${name}.png`));
      }
      results.render = await shadow(`return {
        callouts: r.querySelectorAll('.callout').length,
        details: r.querySelectorAll('details.callout').length,
        mermaidSvgs: r.querySelectorAll('.mermaid-block svg').length,
        mermaidFailed: r.querySelectorAll('.mermaid-block.failed').length,
        images: [...r.querySelectorAll('figure.embed img')].map(i => i.naturalWidth),
        katex: r.querySelectorAll('.katex').length,
        timestamps: r.querySelectorAll('a.ts').length,
      }`);
      await shadow("r.querySelector('.tab[data-view=code]').click(); r.getElementById('body').scrollTop = 0; return true;");
      await new Promise((r) => setTimeout(r, 1200));
      await shot(page, path.join(outDir, '6-code.png'));
      await shadow("r.getElementById('body').scrollTop = 99999; return true;");
      await new Promise((r) => setTimeout(r, 400));
      await shot(page, path.join(outDir, '7-code-end.png'));
      await study(page, shadow, results);
      if (record) await tour(page, shadow, path.join(outDir, 'rec-note.webm'));
      const note = await sidecar(`/session/${key}/note`);
      if (note) writeFileSync(path.join(outDir, 'note.md'), note.content);
      results.ok = true;
    }
  } else {
    results.ok = true;
  }
  results.console = consoleLines.filter((l) => /error|Margin/i.test(l)).slice(-15);
  results.sidecarErrors = sideLog.join('').split('\n').filter((l) => /ERROR|Traceback|WARNING/.test(l)).slice(-15);
  results.vaultFiles = listFiles(vault).map((f) => f.slice(vault.length + 1)).filter((f) => !f.startsWith('.obsidian'));
} catch (e) {
  results.error = String(e?.stack || e);
} finally {
  writeFileSync(path.join(outDir, 'results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  await browser?.close().catch(() => {});
  server?.close();
  side?.kill();
  if (!process.argv.includes('--keep')) rmSync(work, { recursive: true, force: true });
}

/** The study tabs: the crux, a question to the lecture, and the flashcard quiz. */
async function study(page, shadow, results) {
  await shadow("r.querySelector('.tab[data-view=crux]').click(); return true;");
  await waitFor(() => shadow("return !!r.querySelector('.crux li')"), { what: 'the crux', timeout: 30000 });
  results.crux = await shadow(`return { items: r.querySelectorAll('.crux ol > li').length,
    lead: !!r.querySelector('.crux p.lead'), last: !!r.querySelector('.crux p.last'),
    words: r.querySelector('.crux').textContent.trim().split(/\\s+/).length }`);
  await shot(page, path.join(outDir, '8-crux.png'));

  await shadow(`const i = r.getElementById('ask-input'); i.value = 'Why do the chunks overlap?';
    r.getElementById('ask').requestSubmit(); return true;`);
  await waitFor(() => shadow("return !!r.querySelector('.answer .note, .answer .error-box')"), { what: 'an answer', timeout: 120000 });
  results.ask = await shadow(`return { ok: !!r.querySelector('.answer .note'), jumps: r.querySelectorAll('.answer a.ts').length,
    text: r.querySelector('.answer').textContent.slice(0, 300) }`);
  await shot(page, path.join(outDir, '9-ask.png'));

  await shadow("r.querySelector('.tab[data-view=quiz]').click(); return true;");
  await waitFor(() => shadow("return !!r.querySelector('[data-study=make-cards]')"), { what: 'the quiz intro' });
  await shot(page, path.join(outDir, '10-quiz-intro.png'));
  await shadow("r.querySelector('[data-study=make-cards]').click(); return true;");
  await waitFor(() => shadow("return !!r.querySelector('.flashcard')"), { what: 'flashcards', timeout: 180000 });
  await new Promise((r) => setTimeout(r, 600));
  await shot(page, path.join(outDir, '11-quiz-card.png'));
  await shadow("r.querySelector('.flashcard').click(); return true;");
  await new Promise((r) => setTimeout(r, 800));
  results.quiz = await shadow(`return { flipped: r.querySelector('.flashcard').dataset.flipped,
    top: r.querySelector('.quiz-top').textContent.trim(), grade: !r.querySelector('.grade').hidden }`);
  await shot(page, path.join(outDir, '12-quiz-flipped.png'));
  await shadow("r.querySelector('[data-study=knew]').click(); return true;");
  results.quiz.after = await shadow("return r.querySelector('.quiz-top').textContent.trim()");
}

/** The finished note, read top to bottom, then the crux, a flashcard and the code. */
async function tour(page, shadow, file) {
  await shadow("r.querySelector('.tab[data-view=note]').click(); r.getElementById('body').scrollTop = 0; return true;");
  await new Promise((r) => setTimeout(r, 800));
  const recorder = await page.screencast({ path: file, ffmpegPath: FFMPEG });
  await new Promise((r) => setTimeout(r, 2500));
  const height = await shadow("const b = r.getElementById('body'); return b.scrollHeight - b.clientHeight;");
  for (let y = 0; y <= height; y += 18) {
    await shadow(`r.getElementById('body').scrollTop = ${y}; return true;`);
    await new Promise((r) => setTimeout(r, 40));
  }
  await new Promise((r) => setTimeout(r, 1200));
  const tab = (view) => shadow(`r.querySelector('.tab[data-view=${view}]').click(); r.getElementById('body').scrollTop = 0; return true;`);
  await tab('crux'); // the 80/20
  await new Promise((r) => setTimeout(r, 3500));
  await tab('quiz'); // a flashcard, flipped
  await new Promise((r) => setTimeout(r, 1600));
  await shadow("r.querySelector('.flashcard')?.click(); return true;");
  await new Promise((r) => setTimeout(r, 2600));
  await tab('code');
  await new Promise((r) => setTimeout(r, 3000));
  await recorder.stop();
}

function listFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFiles(full));
    else out.push(full);
  }
  return out;
}
