// Does the panel ever cover the video? Checked on a real YouTube page, in
// the default layout and in theater mode, with the real extension loaded.
//
//   node scripts/e2e/layout-check.mjs <youtube-url> <out-dir>
//
// Uses whatever sidecar is already running on :8766.

import { mkdirSync, readdirSync, existsSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const [url, outDir] = process.argv.slice(2);
mkdirSync(outDir, { recursive: true });

const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const v131 = readdirSync(base).find((d) => d.startsWith('mac_arm-131'));
const exe = path.join(base, v131, 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const profile = path.join(os.tmpdir(), `margin-layout-${Date.now()}`);

const browser = await puppeteer.launch({
  executablePath: exe, headless: 'new', protocolTimeout: 60000,
  args: [`--disable-extensions-except=${ROOT}/extension`, `--load-extension=${ROOT}/extension`,
    '--window-size=1470,900', `--user-data-dir=${profile}`, '--autoplay-policy=no-user-gesture-required'],
  defaultViewport: { width: 1470, height: 800 },
});
const results = {};
try {
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForFunction(() => document.querySelector('margin-panel')?.shadowRoot?.querySelector('.panel'), { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 4000));
  // On YouTube Margin starts as a tab on the edge; open it the way a person would.
  results.startsCollapsed = await page.evaluate(() => document.querySelector('margin-panel').shadowRoot.querySelector('.panel').hidden);
  await page.evaluate(() => { const r = document.querySelector('margin-panel').shadowRoot; if (r.querySelector('.panel').hidden) r.getElementById('pill').click(); });
  await new Promise((r) => setTimeout(r, 1500));
  results.title = await page.evaluate(() => document.querySelector('margin-panel').shadowRoot.getElementById('title').textContent);

  const measure = () => page.evaluate(() => {
    const panel = document.querySelector('margin-panel').shadowRoot.querySelector('.panel').getBoundingClientRect();
    const video = document.querySelector('video').getBoundingClientRect();
    const mast = document.querySelector('#masthead-container')?.getBoundingClientRect();
    const title = document.querySelector('ytd-watch-metadata #title h1, h1.ytd-watch-metadata')?.getBoundingClientRect();
    return {
      theater: document.querySelector('ytd-watch-flexy')?.hasAttribute('theater'),
      reserved: document.documentElement.classList.contains('margin-reserve'),
      panel: [Math.round(panel.left), Math.round(panel.width)],
      video: [Math.round(video.left), Math.round(video.right)],
      mastheadRight: mast ? Math.round(mast.right) : null,
      titleLeft: title ? Math.round(title.left) : null,
      videoCovered: Math.max(0, Math.round(video.right - panel.left)),
      scrollX: window.scrollX,
    };
  });

  results.default = await measure();
  await page.screenshot({ path: path.join(outDir, 'default.png') });

  // Theater mode: YouTube's own shortcut.
  await page.evaluate(() => document.querySelector('#movie_player')?.focus());
  await page.keyboard.press('t');
  await page.waitForFunction(() => document.querySelector('ytd-watch-flexy')?.hasAttribute('theater') !== undefined, { timeout: 5000 });
  await new Promise((r) => setTimeout(r, 2500));
  results.theater = await measure();
  await page.screenshot({ path: path.join(outDir, 'theater.png') });

  await page.keyboard.press('t');
  await new Promise((r) => setTimeout(r, 2500));
  results.back = await measure();
  await page.screenshot({ path: path.join(outDir, 'back.png') });
} catch (e) {
  results.error = String(e?.stack || e);
} finally {
  console.log(JSON.stringify(results, null, 2));
  await browser.close();
  if (existsSync(profile)) rmSync(profile, { recursive: true, force: true });
}
