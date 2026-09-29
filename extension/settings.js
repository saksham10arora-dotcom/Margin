// Margin's settings page: your model order, subscriptions, API keys and the
// models on this Mac. An extension page of its own, not part of any website,
// so what you type here (a key) is out of reach of the pages you watch
// lectures on. Everything goes to the sidecar through the background worker.

import { ModelOrder } from './app/models-ui.js';
import { escapeHtml, escapeWithCode } from './app/util.js';

const api = (method, path, body) => chrome.runtime.sendMessage({ type: 'api', method, path, body })
  .then((r) => r || { ok: false, status: 0, data: null })
  .catch(() => ({ ok: false, status: 0, data: null }));

const $ = (id) => document.getElementById(id);
const state = { providers: [], subs: [], query: '', open: null, local: {} };

const order = new ModelOrder($('order'), {
  api,
  heading: 'Model order',
  openSettings: (hash) => { location.hash = hash; route(); },
});

async function load() {
  const [health, res] = await Promise.all([api('GET', '/health'), api('GET', '/providers')]);
  if (!health.ok || !res.ok) {
    showStatus('The Margin sidecar is not answering, so nothing here can be changed yet. Start it with scripts/start.sh '
      + '(or scripts/login-item.sh install to have it start at login), then reload this page.', true);
    return false;
  }
  $('live').dataset.state = 'ok';
  showStatus(res.data.setup_ready ? null
    : 'Nothing in your model order can write notes yet. Add a key, sign in to a subscription, or pick a model on this Mac.', !res.data.setup_ready);
  state.providers = res.data.providers;
  state.subs = res.data.subscriptions;
  drawSubs();
  drawKeys();
  drawLocal();
  return true;
}

function showStatus(message, bad = false) {
  const el = $('status');
  el.hidden = !message;
  el.textContent = message || '';
  el.dataset.bad = String(bad);
}

// --- subscriptions --------------------------------------------------------------------

function drawSubs() {
  $('subs').innerHTML = state.subs.map((s) => {
    const pill = s.ready ? ['ok', 'Ready'] : s.installed ? ['warn', 'Installed, needs sign-in'] : ['off', 'Not installed'];
    const token = s.key ? keyForm(s.key, `${s.name} token`) : '';
    return `<div class="row" id="sub-${s.id}">
      <div class="row-main">
        <div class="row-title">${escapeHtml(s.name)} <span class="pill" data-state="${pill[0]}">${pill[1]}</span></div>
        <div class="row-meta">Through ${escapeHtml(s.tool)}${s.images ? '' : ' · text only, cannot see slides'}</div>
        <ol class="steps">
          ${s.installed ? '' : `<li>Install: <code>${escapeHtml(s.install)}</code> <button class="copy" data-copy="${escapeHtml(s.install)}">Copy</button></li>`}
          <li>${escapeWithCode(s.login)}</li>
        </ol>
        ${token}
      </div>
      <div class="row-side"><button class="btn quiet" data-test="${s.id}">Test</button><span class="result" id="test-${s.id}"></span></div>
    </div>`;
  }).join('');
}

// --- API keys -----------------------------------------------------------------------------

const WHERE = { margin: 'Saved in Margin', shared: 'In ~/.config/keys.env', environment: 'In your environment' };

function keyForm(name, label) {
  const open = state.open === name;
  return `<div class="key" data-key="${escapeHtml(name)}">
    ${open ? `<form class="key-form" data-form="${escapeHtml(name)}">
        <input type="password" name="value" placeholder="Paste your ${escapeHtml(label)} (${escapeHtml(name)})" autocomplete="off" spellcheck="false" required>
        <button class="btn" type="submit">Save</button>
        <button class="btn quiet" type="button" data-cancel>Cancel</button></form>`
      : `<button class="btn quiet" data-add="${escapeHtml(name)}">Add or replace ${escapeHtml(name)}</button>`}
  </div>`;
}

function drawKeys() {
  const q = state.query.trim().toLowerCase();
  const rows = state.providers.filter((p) => p.supported && !p.local && p.key)
    .filter((p) => !q || p.name.toLowerCase().includes(q) || p.id.includes(q) || (p.key || '').toLowerCase().includes(q))
    .sort((a, b) => Number(b.ready) - Number(a.ready) || a.name.localeCompare(b.name));
  const shown = q ? rows : rows.slice(0, 40);
  $('key-rows').innerHTML = shown.map((p) => `<div class="row" id="key-${escapeHtml(p.key)}">
      <div class="row-main">
        <div class="row-title">${escapeHtml(p.name)}
          <span class="pill" data-state="${p.ready ? 'ok' : 'off'}">${p.ready ? escapeHtml(WHERE[p.key_set_in] || 'Key found') : 'No key'}</span></div>
        <div class="row-meta"><code>${escapeHtml(p.key)}</code>${p.models ? ` · ${p.models} models` : ''}
          ${p.doc ? ` · <a href="${escapeHtml(p.doc)}" target="_blank" rel="noopener">Get a key</a>` : ''}</div>
        ${keyForm(p.key, `${p.name} key`)}
      </div>
      <div class="row-side">${p.key_set_in === 'margin' ? `<button class="btn quiet" data-remove="${escapeHtml(p.key)}">Remove</button>` : ''}</div>
    </div>`).join('')
    + (!q && rows.length > shown.length ? `<p class="more">${rows.length - shown.length} more providers: search for yours.</p>` : '')
    + (!rows.length ? '<p class="more">No provider matches.</p>' : '');
}

async function saveKey(name, value) {
  const res = await api('POST', '/keys', { name, value });
  if (!res.ok) {
    alertIn(name, res.data?.detail || 'Could not save it.');
    return;
  }
  state.open = null;
  await load();
  await order.load();
  alertIn(name, 'Saved. It is used from now on.', false);
}

function alertIn(name, message, bad = true) {
  const box = document.querySelector(`[data-key="${CSS.escape(name)}"]`);
  if (!box) return;
  box.querySelector('.key-alert')?.remove();
  box.insertAdjacentHTML('beforeend', `<div class="key-alert" data-bad="${bad}">${escapeHtml(message)}</div>`);
}

// --- on this Mac ----------------------------------------------------------------------------

async function drawLocal() {
  const local = state.providers.filter((p) => p.local);
  $('local-rows').innerHTML = local.map((p) => `<div class="row" id="local-${p.id}">
      <div class="row-main">
        <div class="row-title">${escapeHtml(p.name)} <span class="pill" data-state="${p.ready ? 'ok' : 'off'}">${p.ready ? 'Running' : 'Not running'}</span></div>
        <div class="row-meta">${p.ready ? '' : escapeWithCode(p.hint || '')}</div>
        <div class="row-meta" id="local-models-${p.id}">Looking for downloaded models…</div>
      </div></div>`).join('');
  for (const p of local) {
    const res = await api('GET', `/providers/${p.id}/models`);
    const list = res.ok ? res.data.models : [];
    $(`local-models-${p.id}`).textContent = list.length
      ? `${list.length} downloaded: ${list.slice(0, 6).map((m) => m.id).join(', ')}${list.length > 6 ? '…' : ''}. Pick one in Model order.`
      : 'No models downloaded yet.';
  }
}

// --- events ---------------------------------------------------------------------------------

document.addEventListener('click', async (e) => {
  const t = e.target.closest('button, a');
  if (!t) return;
  if (t.dataset.add) { state.open = t.dataset.add; redrawForms(); document.querySelector(`[data-form="${CSS.escape(t.dataset.add)}"] input`)?.focus(); }
  else if (t.hasAttribute('data-cancel')) { state.open = null; redrawForms(); }
  else if (t.dataset.remove) {
    const res = await api('DELETE', `/keys/${encodeURIComponent(t.dataset.remove)}`);
    if (res.ok) { await load(); await order.load(); }
  } else if (t.dataset.copy) {
    await navigator.clipboard.writeText(t.dataset.copy).catch(() => {});
    t.textContent = 'Copied';
  } else if (t.dataset.test) {
    const id = t.dataset.test;
    const sub = state.subs.find((s) => s.id === id);
    const model = id === 'claude' ? 'sonnet' : id === 'opencode' ? 'opencode/big-pickle' : 'default';
    $(`test-${id}`).textContent = 'Asking for one word…';
    const res = await api('POST', '/providers/test', { provider: id, model });
    const r = res.data || {};
    $(`test-${id}`).textContent = res.ok && r.ok ? `Answered in ${r.seconds}s` : (r.error || `No answer (${res.status})`);
    $(`test-${id}`).dataset.ok = String(Boolean(res.ok && r.ok));
    if (sub && res.ok && r.ok && !sub.ready) await load();
  }
});

document.addEventListener('submit', (e) => {
  const form = e.target.closest('[data-form]');
  if (!form) return;
  e.preventDefault();
  saveKey(form.dataset.form, new FormData(form).get('value'));
});

$('key-search').addEventListener('input', (e) => { state.query = e.target.value; drawKeys(); });

function redrawForms() {
  drawSubs();
  drawKeys();
}

/** #key=GROQ_API_KEY, #provider=groq or #sub=codex: go straight to it. */
function route() {
  const hash = decodeURIComponent(location.hash.slice(1));
  const [kind, value] = hash.split('=');
  let target = null;
  if (kind === 'key' && value) {
    // Narrow the list to that provider, so its row is there however long the list is.
    const p = state.providers.find((x) => x.key === value);
    state.open = value;
    state.query = p ? p.name : value;
    $('key-search').value = state.query;
    redrawForms();
    target = document.getElementById(`key-${value}`) || document.getElementById('keys');
  } else if (kind === 'provider' && value) {
    const p = state.providers.find((x) => x.id === value);
    if (p?.key) { state.open = p.key; state.query = p.name; $('key-search').value = p.name; redrawForms(); }
    target = p?.key ? document.getElementById(`key-${p.key}`) : document.getElementById('keys');
  } else if (kind === 'sub' && value) {
    target = document.getElementById(`sub-${value}`);
  } else if (hash) {
    target = document.getElementById(hash);
  }
  target?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  target?.querySelector('input')?.focus();
}

(async () => {
  if (await load()) {
    await order.load();
    route();
  }
})();
window.addEventListener('hashchange', route);
