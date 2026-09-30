// Closing Margin with its x keeps it closed on that lecture: 2.8.4 and
// earlier opened it again a second later on any lecture page. Checked on a
// real YouTube page, then the toolbar shortcut must bring it back.
//
//   node scripts/e2e/close-check.mjs
//
// Prints {openBefore, presentAfterClose5s, backAfterToggle}; the fix holds
// when those are true, false, true.

import { readdirSync, rmSync, mkdtempSync } from 'node:fs';
import os from 'node:os'; import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = path.join(os.homedir(), '.cache/puppeteer/chrome');
const exe = path.join(base, readdirSync(base).find((d) => d.startsWith('mac_arm-131')), 'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');
const prof = mkdtempSync(path.join(os.tmpdir(), 'margin-close-'));
const browser = await puppeteer.launch({ executablePath: exe, headless: 'new',
  args: [`--disable-extensions-except=${ROOT}/extension`, `--load-extension=${ROOT}/extension`, `--user-data-dir=${prof}`, '--autoplay-policy=no-user-gesture-required'],
  defaultViewport: { width: 1500, height: 920 } });
const out = {};
try {
  const page = await browser.newPage();
  await page.goto('https://www.youtube.com/watch?v=jNQXAC9IVRw', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForFunction(() => !!document.querySelector('margin-panel'), { timeout: 45000 });
  const shadow = (js) => page.evaluate(`(() => { const r = document.querySelector('margin-panel')?.shadowRoot; if (!r) return null; ${js} })()`);
  await page.waitForFunction(() => !!document.querySelector('margin-panel')?.shadowRoot?.getElementById('close'), { timeout: 20000 });
  await shadow("if (r.querySelector('.panel').hidden) r.getElementById('pill').click(); return true;");
  await new Promise((r) => setTimeout(r, 1000));
  out.openBefore = await page.evaluate(() => !!document.querySelector('margin-panel'));
  await shadow("r.getElementById('close').click(); return true;");
  await new Promise((r) => setTimeout(r, 5000));
  out.presentAfterClose5s = await page.evaluate(() => !!document.querySelector('margin-panel'));
  const sw = await browser.waitForTarget((t) => t.type() === 'service_worker');
  const worker = await sw.worker();
  await worker.evaluate(async () => {
    const [tab] = await chrome.tabs.query({ url: '*://*.youtube.com/*' });
    await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
  });
  await new Promise((r) => setTimeout(r, 3000));
  out.backAfterToggle = await page.evaluate(() => !!document.querySelector('margin-panel'));
} catch (e) { out.error = String(e.message || e); }
finally { await browser.close(); rmSync(prof, { recursive: true, force: true }); console.log(JSON.stringify(out)); }
