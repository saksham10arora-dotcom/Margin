// Margin's content-script entry point. A classic script (content scripts
// cannot be ES modules), so it only does three things: build the bridge the
// app uses to reach the extension, decide when to open, and import the real
// app as a module once it is needed.
(() => {
  // When Margin is reloaded or updated, Chrome leaves the copy already running
  // in each open tab in place but cuts it off from the extension, and the
  // background worker adds a fresh copy to open lecture tabs. So a copy that
  // arrives on a page takes over: the old one hears this and shuts down, and
  // anything an older version left on the page (a dead panel, the room it
  // made beside the video) is cleared first.
  const wasOpen = document.querySelector('margin-panel') !== null;
  document.dispatchEvent(new CustomEvent('margin:takeover'));
  document.querySelectorAll('margin-panel').forEach((el) => el.remove());
  document.getElementById('margin-layout-style')?.remove();
  document.documentElement.classList.remove('margin-reserve', 'margin-beside');

  const connected = () => {
    try { return Boolean(chrome.runtime?.id); } catch { return false; }
  };

  const bridge = {
    send: (msg) => {
      try {
        return chrome.runtime.sendMessage(msg).catch((e) => ({ ok: false, error: String(e) }));
      } catch (e) {
        return Promise.resolve({ ok: false, error: String(e) }); // cut off from the extension
      }
    },
    api: (method, path, body) =>
      bridge.send({ type: 'api', method, path, body }).then((r) => r || { ok: false, status: 0, data: null }),
    url: (path) => chrome.runtime.getURL(path),
    storageGet: (defaults) => chrome.storage.local.get(defaults),
    storageSet: (values) => chrome.storage.local.set(values),
    // Closed with its x: stay closed on this lecture. Without this the check
    // below saw a lecture page with no Margin and opened it again a second later.
    onDestroy: ({ byUser = false } = {}) => { app = null; if (byUser) closedOn = page(); },
  };

  let app = null;
  let closedOn = null;
  // The lecture you are on: Udemy, YouTube and DeepLearning.AI change it without a reload.
  const page = () => location.pathname + location.search;
  let opening = null;
  let retired = false;

  function open(options = {}) {
    if (retired || app || opening) return opening;
    opening = import(chrome.runtime.getURL('app/app.js'))
      .then((mod) => mod.start(bridge, options))
      .then((a) => { app = a; })
      .catch((e) => console.error('[Margin] failed to start:', e))
      .finally(() => { opening = null; });
    return opening;
  }

  function onMessage(msg, _sender, sendResponse) {
    // Answering tells the background worker this copy is alive, so it does
    // not add a second one.
    if (msg?.type === 'margin:ping') return void sendResponse({ ok: true });
    if (msg?.type !== 'margin:toggle') return;
    if (!app) { closedOn = null; open({ byUser: true }); } // the toolbar button or Alt+Shift+M
    else app.panel.collapse(app.panel.expanded);
    sendResponse({ ok: true });
  }
  chrome.runtime.onMessage.addListener(onMessage);

  // Where lectures live, open by itself as soon as a lecture page has a video.
  // Elsewhere, wait for the toolbar button or Alt+Shift+M.
  const isLecturePage = () => {
    const host = location.hostname;
    if (/(^|\.)udemy\.com$/.test(host)) return /\/learn\/lecture\//.test(location.pathname);
    if (/(^|\.)coursera\.org$/.test(host)) return /\/lecture\//.test(location.pathname);
    if (/(^|\.)deeplearning\.ai$/.test(host)) return /^\/courses\/[^/]+\/lesson\//.test(location.pathname);
    if (/(^|\.)youtube\.com$/.test(host)) return location.pathname === '/watch';
    return false;
  };

  // These sites navigate without reloading, so keep checking.
  const timer = setInterval(() => {
    if (!connected()) return retire(); // Margin was reloaded, updated or removed
    if (app || opening || closedOn === page()) return;
    closedOn = null; // a different lecture: open as usual
    if (isLecturePage() && document.querySelector('video')) open();
  }, 1000);
  window.addEventListener('pagehide', () => clearInterval(timer));

  function retire() {
    if (retired) return;
    retired = true;
    clearInterval(timer);
    try { chrome.runtime.onMessage.removeListener(onMessage); } catch { /* already cut off */ }
    try { app?.destroy(); } catch { /* its panel goes with the page either way */ }
    app = null;
  }
  document.addEventListener('margin:takeover', retire, { once: true });

  // Margin was open here before the reload (on YouTube, say, where it only
  // opens when asked): carry on with the same lecture rather than vanish.
  if (wasOpen) open();
})();
