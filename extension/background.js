// Margin background worker.
//
// Every request to the sidecar goes through here, never straight from a web
// page. The sidecar only accepts requests from this extension's own origin,
// so udemy.com (or any site) cannot read your notes off localhost, and pages
// never trigger Chrome's local-network permission prompt.
//
// It also does the three things a content script is not allowed to: capture
// the visible tab, inject a script on demand (Mermaid, loaded only when a note
// has a diagram), and open Margin on a site it was not preinstalled for.

const SIDECAR = 'http://localhost:8766';
const PAGE_SCRIPTS = ['vendor/marked.min.js', 'vendor/katex.min.js', 'vendor/auto-render.min.js', 'boot.js'];

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(sendResponse, (e) => sendResponse({ ok: false, status: 0, error: String(e?.message || e) }));
  return true; // async response
});

async function handle(msg, sender) {
  switch (msg?.type) {
    case 'api':
      return sidecarJson(msg.method || 'GET', msg.path, msg.body);
    case 'blob':
      return sidecarDataUrl(msg.path);
    case 'vault-file':
      return sidecarDataUrl(`/vault-files/${String(msg.path).split('/').map(encodeURIComponent).join('/')}`);
    case 'capture-tab':
      return captureTab(sender);
    case 'fetch-text':
      return fetchCaptionText(msg.url);
    case 'open-settings': {
      const hash = typeof msg.hash === 'string' && /^[\w=.-]*$/.test(msg.hash) ? msg.hash : '';
      await chrome.tabs.create({ url: chrome.runtime.getURL('settings.html') + (hash ? `#${hash}` : '') });
      return { ok: true };
    }
    case 'load-mermaid':
      await chrome.scripting.executeScript({
        target: { tabId: sender.tab.id, frameIds: [sender.frameId ?? 0] },
        files: ['vendor/mermaid.min.js'],
      });
      return { ok: true };
    default:
      return { ok: false, error: `unknown message ${msg?.type}` };
  }
}

async function sidecarJson(method, path, body) {
  if (typeof path !== 'string' || !path.startsWith('/')) return { ok: false, status: 0, data: null };
  try {
    const res = await fetch(SIDECAR + path, {
      method,
      // X-Margin: only Margin's own pages can change its settings (keys, model
      // order); a web page cannot add this header to a request to localhost.
      headers: { 'X-Margin': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty body */ }
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null }; // sidecar not running
  }
}

async function sidecarDataUrl(path) {
  try {
    const res = await fetch(SIDECAR + path);
    if (!res.ok) return { ok: false, status: res.status };
    const type = res.headers.get('content-type') || 'application/octet-stream';
    return { ok: true, dataUrl: `data:${type};base64,${toBase64(await res.arrayBuffer())}` };
  } catch {
    return { ok: false, status: 0 };
  }
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

// captureVisibleTab is rate-limited by Chrome (about two calls a second) and
// captures whichever tab is visible in the window, so it is only honoured for
// the tab that asked, and only while that tab is the visible one.
let lastCapture = 0;
async function captureTab(sender) {
  const tab = sender.tab;
  if (!tab?.active) return { ok: false, error: 'tab not visible' };
  const wait = 550 - (Date.now() - lastCapture);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCapture = Date.now();
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 88 });
    return { ok: true, dataUrl };
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
}

// Only caption files from the platforms Margin knows, not arbitrary URLs a
// page might ask this privileged context to fetch on its behalf.
const CAPTION_HOSTS = /(^|\.)(udemycdn\.com|udemy\.com|coursera\.org|cloudfront\.net|deeplearning\.ai)$/;
async function fetchCaptionText(url) {
  let parsed;
  try { parsed = new URL(url); } catch { return { ok: false }; }
  if (parsed.protocol !== 'https:' || !CAPTION_HOSTS.test(parsed.hostname)) return { ok: false };
  try {
    const res = await fetch(parsed.href);
    return res.ok ? { ok: true, text: await res.text() } : { ok: false, status: res.status };
  } catch {
    return { ok: false };
  }
}

// --- opening the panel -----------------------------------------------------------

async function toggle(tab) {
  if (!tab?.id) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
  } catch {
    // Not injected on this site yet. The toolbar click grants activeTab, which
    // is exactly the permission needed to add Margin to this one page.
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: PAGE_SCRIPTS });
      await chrome.tabs.sendMessage(tab.id, { type: 'margin:toggle' });
    } catch (e) {
      console.warn('Margin cannot run on this page:', e?.message || e);
    }
  }
}

// Chrome only adds content scripts to pages loaded after Margin was installed,
// reloaded or updated, and cuts off the copies already running. Without this,
// a lecture that was open at the time is silently not captured until the page
// is reloaded. Tabs whose copy still answers are left alone.
chrome.runtime.onInstalled.addListener(async () => {
  const matches = chrome.runtime.getManifest().content_scripts.flatMap((c) => c.matches);
  for (const tab of await chrome.tabs.query({ url: matches })) {
    if (tab.discarded) continue; // it loads afresh, with Margin, when you return to it
    const alive = await chrome.tabs.sendMessage(tab.id, { type: 'margin:ping' }).catch(() => null);
    if (alive?.ok) continue;
    chrome.scripting.executeScript({ target: { tabId: tab.id }, files: PAGE_SCRIPTS }).catch(() => {});
  }
});

chrome.action.onClicked.addListener(toggle);
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === 'toggle-panel') toggle(tab);
});
