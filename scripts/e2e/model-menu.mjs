// End-to-end: your model order and the settings page, in a real browser.
//
//   node scripts/e2e/model-menu.mjs <lecture-dir> <out-dir> [provider] [model]
//
// In the panel: opens Models, sees the default order, searches the 1st slot's
// dropdown and puts a model there, adds Margin's Gemini as a fallback, writes
// the lecture's note (it must come from the 1st model). On the settings page:
// saves a key, checks it is never shown again, removes it, resets the order.
// Own sidecar, throwaway vault, sessions, key file and order; your real keys
// are read only to know which providers are ready.

import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
// Positional arguments, with flags like --hidpi anywhere among them.
const args = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const lectureDir = path.resolve(args[0] || '');
const outDir = path.resolve(args[1] || 'model-menu-out');
const PROVIDER = args[2] || 'groq';
const MODEL = args[3] || 'openai/gpt-oss-120b';
mkdirSync(outDir, { recursive: true });
if (!existsSync(path.join(lectureDir, 'index.html'))) {
  console.error('usage: node scripts/e2e/resilience.mjs <lecture-dir made by make_lecture.py>');
  process.exit(2);
}
const PAGE_PORT = 8797;
const TEST_PORT = 8769;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-menu-${Date.now()}`);
mkdirSync(path.join(work, 'vault', '.obsidian'), { recursive: true });
const log = (...a) => console.log(`[menu ${new Date().toISOString().slice(11, 19)}]`, ...a);
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
    env: { ...process.env, MARGIN_VAULT_PATH: path.join(work, 'vault'), MARGIN_SESSIONS_PATH: path.join(work, 'sessions'),
      MARGIN_KEYS_FILE: path.join(work, 'keys.env'), MARGIN_CHAIN_FILE: path.join(work, 'chain.json') },
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
const shot = (page, name) => page.screenshot({ path: path.join(outDir, name) }).catch(() => {});
try {
  side = startSidecar();
  await waitFor(() => fetch(`${SIDECAR}/health`).then((r) => r.ok), 'sidecar');
  server = serve(lectureDir);
  browser = await puppeteer.launch({
    executablePath: findChrome(), headless: 'new', protocolTimeout: 90000,
    args: [`--disable-extensions-except=${testExtension()}`, `--load-extension=${path.join(work, 'extension')}`,
      '--autoplay-policy=no-user-gesture-required', '--window-size=1500,920', `--user-data-dir=${path.join(work, 'profile')}`],
    defaultViewport: { width: 1500, height: 920, deviceScaleFactor: process.argv.includes('--hidpi') ? 2 : 1 },
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
  const mo = (js) => shadow(`const m = r.getElementById('models-area'); ${js}`);
  await waitFor(() => shadow("return !r.getElementById('model-label').textContent.includes('…')"), 'order label', 15000);
  checks.labelShowsFirst = (await shadow("return r.getElementById('model-label').textContent")).startsWith('Google Gemini');

  // ... then Models: the default order, numbered.
  await shadow("r.getElementById('more').click(); r.querySelector('[data-act=model]').click(); return true;");
  await waitFor(() => mo("return m.querySelectorAll('.mo-slot').length === 4"), 'default order', 30000);
  checks.defaultOrder = JSON.stringify(await mo("return [...m.querySelectorAll('.mo-title')].map((e) => e.textContent)"));
  await shot(page, '1-order.png');

  // Slot 1's dropdown: search every model, put the chosen one first.
  await mo("m.querySelector('.mo-pick[data-i=\"0\"]').click(); return true;");
  await waitFor(() => mo("return !!m.querySelector('.mo-picker')"), 'dropdown', 10000);
  checks.groupsShown = JSON.stringify(await mo("return [...m.querySelectorAll('.mo-group h4')].map((h) => h.textContent)"));
  await shot(page, '2-dropdown.png');
  await page.evaluate((q) => {
    const m = document.querySelector('margin-panel').shadowRoot.getElementById('models-area');
    const i = m.querySelector('.mo-search'); i.value = q; i.dispatchEvent(new Event('input', { bubbles: true }));
  }, MODEL.split('/').pop());
  await waitFor(() => mo(`return !!m.querySelector('.mo-model[data-provider="${PROVIDER}"][data-model="${MODEL}"]')`), 'search results', 20000);
  checks.searchFindsIt = true;
  // Long model names must not push the dropdown wider than the panel.
  checks.noSidewaysScroll = await shadow("const b = r.getElementById('body'); return b.scrollWidth <= b.clientWidth + 1");
  await shot(page, '3-search.png');
  await mo(`m.querySelector('.mo-model[data-provider="${PROVIDER}"][data-model="${MODEL}"]').click(); return true;`);
  await waitFor(() => mo(`return m.querySelector('.mo-slot .mo-detail')?.textContent.length > 0 && !m.querySelector('.mo-picker')`), 'saved', 15000);
  const saved = JSON.parse(readFileSync(path.join(work, 'chain.json'), 'utf8'));
  checks.firstIsThePick = saved.entries[0].provider === PROVIDER && saved.entries[0].model === MODEL;
  checks.labelFollows = !(await shadow("return r.getElementById('model-label').textContent")).startsWith('Google Gemini');

  // + Add a fallback: Margin's own Gemini, from the dropdown's first group.
  const before = saved.entries.length;
  await mo("m.querySelector('[data-mo=add]').click(); return true;");
  await waitFor(() => mo("return !!m.querySelector('.mo-picker [data-provider=google][data-model=auto]')"), 'add dropdown', 10000);
  await mo("m.querySelector('.mo-picker [data-provider=google][data-model=auto]').click(); return true;");
  await waitFor(async () => JSON.parse(readFileSync(path.join(work, 'chain.json'), 'utf8')).entries.length === before + 1, 'fallback added', 10000);
  checks.fallbackAdded = true;
  await shot(page, '4-order-changed.png');

  // Capture the lecture and write its note: it must come from the 1st model.
  await mo("m.querySelector('[data-mo=back]').click(); return true;");
  await shadow("r.getElementById('primary').click(); return true;");
  await page.evaluate(() => { const v = document.querySelector('video'); v.playbackRate = 4; v.play(); });
  await waitFor(async () => (await session())?.frame_count >= 2, 'slides', 60000);
  await page.evaluate(() => document.querySelector('video').pause());
  await new Promise((r) => setTimeout(r, 1500));
  await shadow("r.getElementById('primary').click(); return true;");
  const done = await waitFor(async () => {
    const s = await session();
    return ['done', 'error'].includes(s?.status?.state) ? s : null;
  }, 'the note', 240000);
  checks.writtenByFirst = done.status.state === 'done' && String(done.status.result?.engine).includes(MODEL);
  log('note written by', done.status.result?.engine);

  // The settings page: a key goes in, is never shown again, comes out.
  const extId = new URL((await browser.waitForTarget((t) => t.type() === 'service_worker')).url()).host;
  const settings = await browser.newPage();
  settings.on('pageerror', (e) => errors.push(`settings: ${e.message}`));
  await settings.goto(`chrome-extension://${extId}/settings.html#key=DEEPSEEK_API_KEY`, { waitUntil: 'load' });
  await settings.waitForSelector('[data-form="DEEPSEEK_API_KEY"] input', { timeout: 20000 });
  await shot(settings, '5-settings.png');
  const secret = 'sk-e2e-not-a-real-key-0123456789';
  await settings.type('[data-form="DEEPSEEK_API_KEY"] input', secret);
  await settings.click('[data-form="DEEPSEEK_API_KEY"] button[type=submit]');
  await settings.waitForFunction(() => document.querySelector('#key-DEEPSEEK_API_KEY .pill')?.textContent === 'Saved in Margin', { timeout: 20000 });
  checks.keySaved = readFileSync(path.join(work, 'keys.env'), 'utf8').includes(`DEEPSEEK_API_KEY=${secret}`);
  checks.keyNeverShown = !(await settings.content()).includes(secret);
  await shot(settings, '6-key-saved.png');
  await settings.click('#key-DEEPSEEK_API_KEY [data-remove]');
  await settings.waitForFunction(() => document.querySelector('#key-DEEPSEEK_API_KEY .pill')?.textContent === 'No key', { timeout: 20000 });
  checks.keyRemoved = !readFileSync(path.join(work, 'keys.env'), 'utf8').includes('DEEPSEEK_API_KEY');
  checks.subscriptionsListed = await settings.$$eval('#subs .row', (rows) => rows.length);
  await settings.evaluate(() => document.querySelector('#order [data-mo=reset]')?.click());
  await settings.waitForFunction(() => !document.querySelector('#order [data-mo=reset]'), { timeout: 15000 });
  checks.resetToDefault = !existsSync(path.join(work, 'chain.json'));
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
  const failed = checks.error || Object.values(checks).some((v) => v === false || v === 0);
  process.exit(failed ? 1 : 0);
}
