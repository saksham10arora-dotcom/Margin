// Does Margin keep taking notes while you study in another tab?
//
//   node scripts/e2e/background-check.mjs <lecture-dir | youtube-url> [--hidden=420]
//
// A lecture dir comes from make_lecture.py (--no-captions makes Margin listen
// with Whisper, which is the case where muting could matter). YouTube in an
// automated Chrome tends to stop for its bot check, so the local lecture is
// the reliable run.
//
// A real window (not headless, so Chrome hides and throttles the tab as it does
// for you), the real extension on its own sidecar and throwaway vault. Opens
// the lecture, presses Capture, mutes it with YouTube's own "m", watches for a
// minute and a half, then opens another tab in front for --hidden seconds and
// comes back. Prints what Margin kept in each stretch: slides, lines of speech,
// and how far the video got.

import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const arg = process.argv[2] || '';
const local = !arg.startsWith('http');
const PAGE_PORT = 8797;
const url = local ? `http://localhost:${PAGE_PORT}/index.html` : arg;
const lectureDir = local ? path.resolve(arg) : null;
const tabMute = process.argv.includes('--tab-mute');
const clickFix = process.argv.includes('--click-fix'); // press the warning's "Mute only this tab"
const volumeZero = process.argv.includes('--volume0'); // the volume slider at 0 instead of mute
// The video from another origin, as a protected one is: Margin cannot read it
// and has to read the screen, which only works while its tab is in view.
const crossOrigin = process.argv.includes('--cross-origin'); // Chrome's tab mute instead of the player's
const muted = !process.argv.includes('--unmuted') && !tabMute && !volumeZero;
const hiddenSec = Number((process.argv.find((a) => a.startsWith('--hidden=')) || '--hidden=420').split('=')[1]);
const visibleSec = 60;
const TEST_PORT = 8776;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-bg-${Date.now()}`);
mkdirSync(path.join(work, 'vault', '.obsidian'), { recursive: true });

const ext = path.join(work, 'extension');
cpSync(path.join(ROOT, 'extension'), ext, { recursive: true });
const manifest = JSON.parse(readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
manifest.host_permissions.push(`${SIDECAR}/*`, `http://localhost:${PAGE_PORT}/*`);
manifest.content_scripts[0].matches.push(`http://localhost:${PAGE_PORT}/*`);
writeFileSync(path.join(ext, 'manifest.json'), JSON.stringify(manifest, null, 2));
const bg = path.join(ext, 'background.js');
writeFileSync(bg, readFileSync(bg, 'utf8').replace("'http://localhost:8766'", `'${SIDECAR}'`));

const server = local && http.createServer((req, res) => {
  const file = path.join(lectureDir, decodeURIComponent(req.url.split('?')[0]).replace(/^\//, '') || 'index.html');
  if (!file.startsWith(lectureDir) || !existsSync(file)) return void res.writeHead(404).end();
  let data = readFileSync(file);
  if (crossOrigin && file.endsWith('index.html')) data = Buffer.from(String(data).replace(' crossorigin="anonymous"', '').replace('src="lecture.mp4"', `src="http://127.0.0.1:${PAGE_PORT - 1}/lecture.mp4"`));
  const type = { '.html': 'text/html', '.mp4': 'video/mp4', '.vtt': 'text/vtt' }[path.extname(file)] || 'application/octet-stream';
  const range = req.headers.range?.match(/bytes=(\d+)-(\d*)/);
  if (!range) return void res.writeHead(200, { 'Content-Type': type, 'Accept-Ranges': 'bytes' }).end(data);
  const start = Number(range[1]);
  const end = range[2] ? Number(range[2]) : data.length - 1;
  res.writeHead(206, { 'Content-Type': type, 'Content-Range': `bytes ${start}-${end}/${data.length}`, 'Content-Length': end - start + 1 });
  res.end(data.subarray(start, end + 1));
}).listen(PAGE_PORT);
const videoServer = crossOrigin && http.createServer((req, res) => {
  const data = readFileSync(path.join(lectureDir, 'lecture.mp4'));
  const range = req.headers.range?.match(/bytes=(\d+)-(\d*)/);
  if (!range) return void res.writeHead(200, { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes' }).end(data);
  const start = Number(range[1]);
  const end = range[2] ? Number(range[2]) : data.length - 1;
  res.writeHead(206, { 'Content-Type': 'video/mp4', 'Content-Range': `bytes ${start}-${end}/${data.length}`, 'Content-Length': end - start + 1 });
  res.end(data.subarray(start, end + 1));
}).listen(PAGE_PORT - 1);

const side = spawn(path.join(ROOT, 'venv/bin/python'), ['-m', 'uvicorn', 'sidecar.main:app', '--port', String(TEST_PORT)], {
  cwd: ROOT, stdio: 'ignore',
  env: { ...process.env, MARGIN_VAULT_PATH: path.join(work, 'vault'), MARGIN_SESSIONS_PATH: path.join(work, 'sessions') },
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (p) => { try { const r = await fetch(SIDECAR + p); return r.ok ? r.json() : null; } catch { return null; } };
for (let i = 0; i < 60 && !(await get('/health')); i++) await sleep(500);
const log = (...a) => console.log(`[bg ${new Date().toISOString().slice(11, 19)}]`, ...a);

const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const exe = path.join(base, readdirSync(base).find((d) => d.startsWith('mac_arm-131')),
  'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser = await puppeteer.launch({ executablePath: exe, headless: false,
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--user-data-dir=${path.join(work, 'profile')}`,
    '--autoplay-policy=no-user-gesture-required', '--window-size=1500,950'], defaultViewport: null });

const rows = [];
let ok = false;
try {
  const page = (await browser.pages())[0] || await browser.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1, { timeout: 60000 });
  const sw = await browser.waitForTarget((t) => t.type() === 'service_worker' && t.url().endsWith('background.js'));
  // Margin's page script loads a moment after the page does: retry until it answers.
  for (let i = 0; i < 30; i++) {
    const opened = await (await sw.worker()).evaluate(async () => {
      const [tab] = await chrome.tabs.query({ active: true });
      try { await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' }); return true; } catch { return false; }
    });
    if (opened) break;
    await sleep(1000);
  }
  const shadow = (js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
  for (let i = 0; i < 60 && !(await shadow("return /Capture/.test(r.getElementById('primary').textContent)")); i++) await sleep(500);
  await shadow("r.getElementById('primary').click(); return true;");
  // Wait out any ad, then make sure the lecture itself is playing.
  for (let i = 0; i < 120 && await page.evaluate(() => document.querySelector('#movie_player')?.classList.contains('ad-showing')); i++) await sleep(1000);
  await page.evaluate((yt) => { const v = document.querySelector('video'); if (yt) v.currentTime = 30; v.play(); }, !local);
  // YouTube's own mute, as you would press it: focus the player (a click would
  // pause it) and press m. YouTube's m sets the <video>'s muted, which is what
  // the local lecture gets.
  if (local) await page.evaluate((m) => { document.querySelector('video').muted = m; }, muted);
  if (volumeZero) await page.evaluate(() => { document.querySelector('video').volume = 0; });
  if (tabMute) {
    await (await sw.worker()).evaluate(async () => {
      const [tab] = await chrome.tabs.query({ active: true });
      await chrome.tabs.update(tab.id, { muted: true });
    });
  }
  else if (!local && muted) {
    await page.evaluate(() => document.querySelector('#movie_player').focus());
    await page.keyboard.press('m');
  }
  await sleep(1500);
  const t0 = await page.evaluate(() => document.querySelector('video').currentTime);
  await sleep(3000);
  const t1 = await page.evaluate(() => document.querySelector('video').currentTime);
  if (!(t1 > t0 + 1)) throw new Error(`video is not playing (${t0} -> ${t1})`);

  const key = local ? `web-solo-localhost_${PAGE_PORT}_index.html` : `youtube-solo-${new URL(url).searchParams.get('v')}`;
  const snap = async (phase) => {
    const s = await get(`/session/${key}`);
    const v = await page.evaluate(() => {
      const el = document.querySelector('video');
      return { t: Math.round(el.currentTime), paused: el.paused, muted: el.muted, vis: document.visibilityState, decoded: el.getVideoPlaybackQuality().totalVideoFrames };
    });
    const diag = await page.evaluate(() => { try { return JSON.parse(document.querySelector('margin-panel').dataset.diag); } catch { return {}; } });
    const speech = await shadow("return r.textContent.match(/(Player CC|Listening|Heard|Captions|Muted in the player)[^\\n]{0,40}/)?.[0] || null");
    const toast = await shadow("const el = r.getElementById('toast'); return el?.dataset.on === 'true' ? el.textContent.slice(0, 90) : null");
    const badge = await (await sw.worker()).evaluate(() => chrome.action.getBadgeText({}));
    const row = { phase, ...v, slides: s?.frame_count, cues: s?.cue_count, coverage: s?.coverage, samples: diag.samples, err: diag.lastError || undefined, mode: diag.mode, speech, toast, badge };
    rows.push(row);
    log(JSON.stringify(row));
    return row;
  };
  if ((await snap('start')).muted !== muted) throw new Error('mute did not take');
  if (crossOrigin) {
    const probe = await (await sw.worker()).evaluate(async () => {
      const [tab] = await chrome.tabs.query({ active: true });
      try { const d = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg' }); return `ok ${d.length}`; }
      catch (e) { return `error: ${e.message}`; }
    });
    log('captureVisibleTab:', probe);
  }
  if (clickFix) {
    await sleep(2000);
    const pressed = await shadow("const b = r.querySelector('.toast-action'); if (!b) return false; b.click(); return true;");
    await sleep(1500);
    const tabMuted = await (await sw.worker()).evaluate(async () => (await chrome.tabs.query({ active: true }))[0].mutedInfo.muted);
    log(JSON.stringify({ pressed, tabMuted, videoMuted: await page.evaluate(() => document.querySelector('video').muted) }));
  }
  for (let t = 0; t < visibleSec; t += 30) { await sleep(30000); await snap('visible'); }

  const other = await browser.newPage();
  await other.goto('https://en.wikipedia.org/wiki/Sliding_window_protocol');
  await other.bringToFront();
  for (let t = 0; t < hiddenSec; t += 30) { await sleep(30000); await snap('hidden'); }

  await page.bringToFront();
  await sleep(2500);
  await snap('return');
  for (let t = 0; t < 60; t += 30) { await sleep(30000); await snap('back'); }
  ok = true;
} finally {
  await browser.close();
  side.kill();
  if (server) server.close();
  if (videoServer) videoServer.close();
  rmSync(work, { recursive: true, force: true });
}

// Slides and lines gained per minute of video in each stretch.
const rate = (phase) => {
  const rs = rows.filter((r) => r.phase === phase);
  const before = rows[rows.indexOf(rs[0]) - 1];
  const last = rs[rs.length - 1];
  const min = Math.max(1e-9, (last.t - before.t) / 60);
  return { videoMinutes: +min.toFixed(1), samplesPerMin: +((last.samples - before.samples) / min).toFixed(1), coverageGained: +(last.coverage - before.coverage).toFixed(3), decodedPerSec: +((last.decoded - before.decoded) / (min * 60)).toFixed(1), slidesPerMin: +((last.slides - before.slides) / min).toFixed(2), linesPerMin: +((last.cues - before.cues) / min).toFixed(1) };
};
console.log(JSON.stringify({ ok, visible: rate('visible'), hidden: rate('hidden'), back: rate('back') }, null, 1));
process.exit(ok ? 0 : 1);
