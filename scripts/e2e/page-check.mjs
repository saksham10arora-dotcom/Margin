// Margin on a real page you read, in a test browser with a throwaway vault:
//
//   node scripts/e2e/page-check.mjs <url> <out-dir>
//
// Opens the page, adds Margin as the toolbar button would, then reports what
// it found (the title, the section in view, a selected sentence) and keeps a
// note, which must land in Reading/.
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const target = process.argv[2];
const outDir = path.resolve(process.argv[3] || path.join(os.tmpdir(), 'margin-page-out'));
mkdirSync(outDir, { recursive: true });
const TEST_PORT = 8775;
const SIDECAR = `http://localhost:${TEST_PORT}`;
const work = path.join(os.tmpdir(), `margin-page-${Date.now()}`);
const vault = path.join(work, 'vault');
mkdirSync(path.join(vault, '.obsidian'), { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ext = path.join(work, 'extension');
cpSync(path.join(ROOT, 'extension'), ext, { recursive: true });
const manifest = JSON.parse(readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
manifest.host_permissions.push(`${new URL(target).origin}/*`, `${SIDECAR}/*`);
writeFileSync(path.join(ext, 'manifest.json'), JSON.stringify(manifest, null, 2));
const bg = path.join(ext, 'background.js');
writeFileSync(bg, readFileSync(bg, 'utf8').replace("'http://localhost:8766'", `'${SIDECAR}'`));
const side = spawn(path.join(ROOT, 'venv/bin/python'), ['-m', 'uvicorn', 'sidecar.main:app', '--port', String(TEST_PORT)], {
  cwd: ROOT, stdio: 'ignore', env: { ...process.env, MARGIN_VAULT_PATH: vault, MARGIN_SESSIONS_PATH: path.join(work, 'sessions') } });
for (let i = 0; i < 60; i++) { try { if ((await fetch(SIDECAR + '/health')).ok) break; } catch { /* starting */ } await sleep(500); }
const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const exe = path.join(base, readdirSync(base).find((d) => d.startsWith('mac_arm-131')),
  'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser = await puppeteer.launch({ executablePath: exe, headless: 'new',
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--user-data-dir=${path.join(work, 'profile')}`],
  defaultViewport: { width: 1440, height: 900 } });
const out = {};
const shadow = (page, js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
try {
  const page = await browser.newPage();
  await page.goto(target, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(3000);
  out.frames = page.frames().map((f) => f.url()).filter((u) => u && u !== 'about:blank').slice(0, 6);
  out.pageHeadings = await page.evaluate(() => [...document.querySelectorAll('h1,h2,h3,h4')].map((h) => h.textContent.trim()).filter(Boolean).slice(0, 8));
  const sw = await browser.waitForTarget((t) => t.type() === 'service_worker' && t.url().endsWith('background.js'));
  await (await sw.worker()).evaluate(async (origin) => {
    const [tab] = await chrome.tabs.query({ url: `${origin}/*` });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['vendor/marked.min.js', 'vendor/katex.min.js', 'vendor/auto-render.min.js', 'boot.js'] });
    await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
  }, new URL(target).origin);
  for (let i = 0; i < 40 && !(await shadow(page, "return r.querySelector('.panel')?.dataset.mode")); i++) await sleep(250);
  out.mode = await shadow(page, "return r.querySelector('.panel')?.dataset.mode");
  out.title = await shadow(page, "return r.getElementById('title').textContent");
  // Select the first long paragraph the top document has.
  out.selected = await page.evaluate(() => {
    const p = [...document.querySelectorAll('p, li, div')].find((el) => el.childNodes.length === 1 && el.firstChild.nodeType === 3 && el.textContent.trim().length > 80 && el.getClientRects().length);
    if (!p) return null;
    const range = document.createRange(); range.selectNodeContents(p);
    getSelection().removeAllRanges(); getSelection().addRange(range);
    return p.textContent.trim().slice(0, 80);
  });
  await sleep(800);
  out.quote = await shadow(page, "const q = r.getElementById('mine-quote'); return q.hidden ? null : q.textContent.trim().slice(0, 80)");
  const box = await page.$('margin-panel >>> #mine-text');
  await box.type('Margin test note on a real page.');
  out.at = await shadow(page, "return r.getElementById('mine-at').textContent");
  await shadow(page, "r.getElementById('mine-add').click(); return true;");
  for (let i = 0; i < 40 && !existsSync(path.join(vault, 'Reading')); i++) await sleep(250);
  await sleep(1000);
  const dir = path.join(vault, 'Reading');
  const file = existsSync(dir) ? readdirSync(dir).find((n) => n.endsWith('.md')) : null;
  out.noteFile = file;
  out.note = file ? readFileSync(path.join(dir, file), 'utf8') : null;
  await page.screenshot({ path: path.join(outDir, 'page-real.png') });
} catch (e) {
  out.error = String(e?.stack || e);
} finally {
  await browser.close();
  side.kill();
  rmSync(work, { recursive: true, force: true });
}
console.log(JSON.stringify(out, null, 1));
