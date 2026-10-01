// Margin updates itself from its folder, never in the middle of a lecture.
//
//   node scripts/e2e/self-update-check.mjs
//
// A copy of the extension in Chrome for Testing. A newer version is put in its
// folder (what ./install.sh does). While a lecture is open, closing a tab must
// not reload Margin; with none open, it must, and come back as the new version.

import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const work = mkdtempSync(path.join(os.tmpdir(), 'margin-update-'));
const ext = path.join(work, 'extension');
cpSync(path.join(ROOT, 'extension'), ext, { recursive: true });
const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const exe = path.join(base, readdirSync(base).find((d) => d.startsWith('mac_arm-131')),
  'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const browser = await puppeteer.launch({ executablePath: exe, headless: 'new',
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, `--user-data-dir=${path.join(work, 'profile')}`],
  protocolTimeout: 15000 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.error('[update-check]', ...a);
// The newest live worker: after a reload the old one's target lingers for a moment.
const worker = async () => {
  for (let i = 0; i < 40; i++) {
    const targets = browser.targets().filter((t) => t.type() === 'service_worker' && t.url().endsWith('background.js'));
    for (const t of targets.reverse()) {
      try {
        const w = await t.worker();
        if (w && await Promise.race([w.evaluate(() => 1), sleep(2000).then(() => 0)])) return w;
      } catch { /* gone */ }
    }
    await sleep(500);
  }
  throw new Error('no live service worker');
};
const version = async () => (await worker()).evaluate(() => chrome.runtime.getManifest().version);
const closeATab = async () => { const p = await browser.newPage(); await p.close(); await sleep(2500); };
const out = {};
try {
  out.before = await version(); log('running', out.before);
  const manifest = JSON.parse(readFileSync(path.join(ext, 'manifest.json'), 'utf8'));
  manifest.version = '2.99.0';
  writeFileSync(path.join(ext, 'manifest.json'), JSON.stringify(manifest, null, 2)); // ./install.sh, in effect

  await (await worker()).evaluate(() => chrome.storage.session.set({ 'lecture:999': { key: 'k', write: true } }));
  await closeATab();
  out.whileWatching = await version(); log('after a tab closed while watching:', out.whileWatching);

  await (await worker()).evaluate(() => chrome.storage.session.remove('lecture:999'));
  await closeATab();
  for (let i = 0; i < 20; i++) {
    try { out.afterLecture = await version(); } catch { /* the worker is restarting */ }
    if (out.afterLecture === '2.99.0') break;
    await sleep(500);
  }
  log('after a tab closed with no lecture:', out.afterLecture);
  out.ok = out.whileWatching === out.before && out.afterLecture === '2.99.0';
} finally {
  await browser.close();
  rmSync(work, { recursive: true, force: true });
}
console.log(JSON.stringify(out));
process.exit(out.ok ? 0 : 1);
