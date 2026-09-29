// End-to-end: the two interruptions a real session has.
//
//   node scripts/e2e/resilience.mjs <lecture-dir>
//
// 1. Margin is reloaded (an update, or you pressed reload) in the middle of a
//    lecture. The tab keeps playing; the old copy must step aside and a fresh
//    one must attach to the same lecture and keep capturing, with one panel.
// 2. The sidecar goes away for a while (a restart) and comes back. What was
//    captured meanwhile must arrive once it answers, not be lost.
//
// Own sidecar on a test port, throwaway vault and sessions, nothing real touched.

import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const lectureDir = path.resolve(process.argv[2] || '');
if (!existsSync(path.join(lectureDir, 'index.html'))) {
  console.error('usage: node scripts/e2e/resilience.mjs <lecture-dir made by make_lecture.py>');
  process.exit(2);
}
const PAGE_PORT = 8798;
const TEST_PORT = 8768;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-resilience-${Date.now()}`);
mkdirSync(path.join(work, 'vault', '.obsidian'), { recursive: true });
const log = (...a) => console.log(`[resilience ${new Date().toISOString().slice(11, 19)}]`, ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
  const v = readdirSync(base).find((d) => d.startsWith('mac_arm-131'));
  return path.join(base, v, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
}

function testExtension() {
  const dir = path.join(work, 'extension');
  cpSync(path.join(ROOT, 'extension'), dir, { recursive: true });
  const manifest = JSON.parse(readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  manifest.host_permissions.push(`http://localhost:${PAGE_PORT}/*`, `http://localhost:${TEST_PORT}/*`);
  manifest.content_scripts[0].matches.push(`http://localhost:${PAGE_PORT}/*`);
  writeFileSync(path.join(dir, 'manifest.json'), JSON.stringify(manifest, null, 2));
  const bg = path.join(dir, 'background.js');
  writeFileSync(bg, readFileSync(bg, 'utf8').replace("'http://localhost:8766'", `'http://localhost:${TEST_PORT}'`));
  return dir;
}

function serve(dir) {
  const types = { '.html': 'text/html', '.mp4': 'video/mp4', '.vtt': 'text/vtt' };
  return http.createServer((req, res) => {
    const file = path.join(dir, decodeURIComponent(req.url.split('?')[0]).replace(/^\//, '') || 'index.html');
    if (!file.startsWith(dir) || !existsSync(file)) { res.writeHead(404).end(); return; }
    const data = readFileSync(file);
    const range = req.headers.range?.match(/bytes=(\d+)-(\d*)/);
    const headers = { 'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Accept-Ranges': 'bytes' };
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

function startSidecar() {
  return spawn(path.join(ROOT, 'venv/bin/python'), ['-m', 'uvicorn', 'sidecar.main:app', '--port', String(TEST_PORT)], {
    cwd: ROOT,
    env: { ...process.env, MARGIN_VAULT_PATH: path.join(work, 'vault'), MARGIN_SESSIONS_PATH: path.join(work, 'sessions') },
    stdio: 'ignore',
  });
}

async function waitFor(fn, what, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const v = await fn().catch(() => null);
    if (v) return v;
    await sleep(400);
  }
  throw new Error(`Timed out waiting for ${what}`);
}

const session = async () => {
  const dir = path.join(work, 'sessions');
  const key = existsSync(dir) && readdirSync(dir)[0];
  if (!key) return null;
  const r = await fetch(`${SIDECAR}/session/${key}`);
  return r.ok ? r.json() : null;
};

const checks = {};
let side;
let server;
let browser;
try {
  side = startSidecar();
  await waitFor(() => fetch(`${SIDECAR}/health`).then((r) => r.ok), 'sidecar');
  server = serve(lectureDir);
  browser = await puppeteer.launch({
    executablePath: findChrome(), headless: 'new', protocolTimeout: 60000,
    args: [`--disable-extensions-except=${testExtension()}`, `--load-extension=${path.join(work, 'extension')}`,
      '--autoplay-policy=no-user-gesture-required', '--window-size=1500,920', `--user-data-dir=${path.join(work, 'profile')}`],
    defaultViewport: { width: 1500, height: 920 },
  });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://localhost:${PAGE_PORT}/index.html`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1);

  const worker = async () => (await browser.waitForTarget(
    (t) => t.type() === 'service_worker' && t.url().endsWith('background.js'))).worker();
  await (await worker()).evaluate(async (port) => {
    const [tab] = await chrome.tabs.query({ url: `http://localhost:${port}/*` });
    await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
  }, PAGE_PORT);
  const shadow = (js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
  await waitFor(() => shadow("return r.getElementById('title')?.textContent.trim().length > 2"), 'panel');
  await shadow("r.getElementById('primary').click(); return true;"); // Capture this lecture
  await page.evaluate(() => { const v = document.querySelector('video'); v.playbackRate = 1; v.play(); });
  await waitFor(async () => (await session())?.frame_count >= 1, 'first slide');
  await sleep(6000);
  const before = await session();
  log('before reload:', before.frame_count, 'slides, watched', JSON.stringify(before.watched));

  // --- 1. reload the extension mid-lecture --------------------------------------
  const oldWorker = await worker();
  await oldWorker.evaluate(() => chrome.runtime.reload()).catch(() => {}); // the worker dies answering
  await sleep(1500);
  await worker(); // the new one is up
  await waitFor(() => page.evaluate(() => document.querySelectorAll('margin-panel').length === 1
    && !!document.querySelector('margin-panel')?.shadowRoot?.getElementById('title')?.textContent.trim()), 'fresh panel after reload');
  const t0 = await page.evaluate(() => document.querySelector('video').currentTime);
  await sleep(9000); // well past the 10 s watched-post interval
  const afterReload = await session();
  const reach = (s) => Math.max(0, ...(s.watched || []).map(([, b]) => b));
  checks.onePanelAfterReload = await page.evaluate(() => document.querySelectorAll('margin-panel').length) === 1;
  checks.keepsCapturingAfterReload = reach(afterReload) > t0;
  log('after reload: panels ok =', checks.onePanelAfterReload, '| watched', JSON.stringify(afterReload.watched), '| video was at', t0.toFixed(1));

  // --- 2. the sidecar goes away and comes back ----------------------------------
  side.kill();
  await waitFor(() => fetch(`${SIDECAR}/health`).then(() => false, () => true), 'sidecar gone');
  const downFrom = await page.evaluate(() => document.querySelector('video').currentTime);
  await sleep(12000);
  const downTo = await page.evaluate(() => document.querySelector('video').currentTime);
  side = startSidecar();
  await waitFor(() => fetch(`${SIDECAR}/health`).then((r) => r.ok), 'sidecar back');
  await sleep(9000); // the outbox retries every few seconds
  const after = await session();
  const covered = (after.watched || []).some(([a, b]) => a <= downFrom + 1 && b >= downTo - 1);
  checks.nothingLostWhileDown = covered;
  checks.slidesKeptGrowing = after.frame_count >= before.frame_count;
  log(`sidecar down from ${downFrom.toFixed(1)}s to ${downTo.toFixed(1)}s; watched now`, JSON.stringify(after.watched),
    '|', after.frame_count, 'slides');
  checks.noPageErrors = errors.length === 0;
  if (errors.length) log('page errors:', errors);
} catch (e) {
  checks.error = String(e?.message || e);
} finally {
  console.log(JSON.stringify(checks, null, 2));
  await browser?.close().catch(() => {});
  server?.close();
  side?.kill();
  rmSync(work, { recursive: true, force: true });
  const failed = checks.error || Object.values(checks).some((v) => v === false);
  process.exit(failed ? 1 : 0);
}
