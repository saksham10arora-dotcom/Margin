// Closing the tab writes the note, as moving on to the next lecture does.
//
//   node scripts/e2e/tab-close-check.mjs <lecture-dir>     (from make_lecture.py)
//
// The real extension, on its own sidecar and throwaway vault: open the
// lecture, capture, watch most of it, close the tab. The sidecar must then
// have the last stretch watched and be writing the note. Before 2.10.4 the
// tab closing did neither: the note waited for the next lecture or the end.

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
  console.error('usage: node scripts/e2e/tab-close-check.mjs <lecture-dir from make_lecture.py>');
  process.exit(2);
}
const PAGE_PORT = 8798;
const TEST_PORT = 8772;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-tabclose-${Date.now()}`);
mkdirSync(path.join(work, 'vault', '.obsidian'), { recursive: true });

const ext = path.join(work, 'extension');
cpSync(path.join(ROOT, 'extension'), ext, { recursive: true });
const manifest = JSON.parse(readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
manifest.host_permissions.push(`http://localhost:${PAGE_PORT}/*`, `${SIDECAR}/*`);
manifest.content_scripts[0].matches.push(`http://localhost:${PAGE_PORT}/*`);
writeFileSync(path.join(ext, 'manifest.json'), JSON.stringify(manifest, null, 2));
const bg = path.join(ext, 'background.js');
writeFileSync(bg, readFileSync(bg, 'utf8').replace("'http://localhost:8766'", `'${SIDECAR}'`));

const server = http.createServer((req, res) => {
  const file = path.join(lectureDir, decodeURIComponent(req.url.split('?')[0]).replace(/^\//, '') || 'index.html');
  if (!file.startsWith(lectureDir) || !existsSync(file)) return void res.writeHead(404).end();
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
  env: { ...process.env, MARGIN_VAULT_PATH: path.join(work, 'vault'), MARGIN_SESSIONS_PATH: path.join(work, 'sessions') },
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (p) => { try { const r = await fetch(SIDECAR + p); return r.ok ? r.json() : null; } catch { return null; } };
for (let i = 0; i < 60 && !(await get('/health')); i++) await sleep(500);

const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const exe = path.join(base, readdirSync(base).find((d) => d.startsWith('mac_arm-131')),
  'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser = await puppeteer.launch({ executablePath: exe, headless: 'new',
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--user-data-dir=${path.join(work, 'profile')}`,
    '--autoplay-policy=no-user-gesture-required'], defaultViewport: { width: 1500, height: 920 } });
const out = {};
try {
  const page = await browser.newPage();
  await page.goto(`http://localhost:${PAGE_PORT}/index.html`, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('video')?.readyState >= 1, { timeout: 30000 });
  const sw = await browser.waitForTarget((t) => t.type() === 'service_worker' && t.url().endsWith('background.js'));
  await (await sw.worker()).evaluate(async (port) => {
    const [tab] = await chrome.tabs.query({ url: `http://localhost:${port}/*` });
    await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
  }, PAGE_PORT);
  const shadow = (js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
  for (let i = 0; i < 60 && !(await shadow("return /Capture/.test(r.getElementById('primary').textContent)")); i++) await sleep(500);
  await shadow("r.getElementById('primary').click(); return true;");
  const duration = await page.evaluate(() => { const v = document.querySelector('video'); v.playbackRate = 2; v.play(); return v.duration; });
  while (await page.evaluate(() => document.querySelector('video').currentTime) < duration * 0.9) await sleep(500);
  const key = `web-solo-localhost_${PAGE_PORT}_index.html`;
  out.watchedBeforeClose = (await get(`/session/${key}`))?.watched;
  // Close the tab as Chrome does for its x (Puppeteer's own close skips the page's
  // pagehide, which a person closing the tab does not): mid-lecture, no end, no next lecture.
  await (await sw.worker()).evaluate(async (port) => {
    const [tab] = await chrome.tabs.query({ url: `http://localhost:${port}/*` });
    await chrome.tabs.remove(tab.id);
  }, PAGE_PORT);
  for (let i = 0; i < 20; i++) {
    const s = await get(`/session/${key}`);
    out.watchedAfterClose = s?.watched;
    out.noteAfterClose = s?.status?.state;
    if (['queued', 'composing', 'running', 'done'].includes(out.noteAfterClose)) break;
    await sleep(500);
  }
  out.ok = ['queued', 'composing', 'running', 'done'].includes(out.noteAfterClose);
} finally {
  await browser.close();
  side.kill();
  server.close();
  rmSync(work, { recursive: true, force: true });
}
console.log(JSON.stringify(out, null, 1));
process.exit(out.ok ? 0 : 1);
