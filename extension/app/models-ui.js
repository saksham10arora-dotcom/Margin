// Your model order, and a dropdown for each place in it: which model writes
// your notes first, and which take over, in turn, when it is busy or fails.
//
// One component, two homes: the panel's Models view (next to the lecture) and
// the settings page. It talks to the sidecar only: GET /providers for what
// exists and what is ready, PUT/DELETE /chain to save or reset the order.
// Keys are never typed here: in the panel this sits inside the lecture's page,
// whose scripts could read what is typed. "Add key" opens the settings page.

import { escapeHtml, escapeWithCode } from './util.js';

const ICON = {
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
  remove: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 6-6 6 6 6"/></svg>',
};

/** Margin's own engines, offered first in every dropdown. */
const MARGIN_ENGINES = [
  { provider: 'google', model: 'auto', title: 'Gemini, best available', detail: 'Margin tries 3.5 Flash, then 3 Flash, then 3.6 Flash' },
  { provider: 'openrouter', model: 'auto', title: 'OpenRouter, Gemini', detail: 'Gemini 3.5 Flash, then 2.5 Flash, on OpenRouter' },
  { provider: 'google', model: 'auto-lite', title: 'Gemini Lite', detail: 'Last resort: stays up when others are overloaded; its notes are rewritten later' },
];

export function contextLabel(tokens) {
  if (!tokens) return '';
  return tokens >= 1e6 ? `${+(tokens / 1e6).toFixed(1)}M context` : `${Math.round(tokens / 1000)}k context`;
}

export function priceLabel(m) {
  if (m.cost_in == null && m.cost_out == null) return '';
  if (!m.cost_in && !m.cost_out) return 'free';
  return `$${+(m.cost_in ?? 0).toFixed(2)} in / $${+(m.cost_out ?? 0).toFixed(2)} out per M`;
}

export class ModelOrder {
  /**
   * `root` is the element to draw in. `api(method, path, body)` reaches the
   * sidecar; `openSettings(hash)` opens the settings page; `onChange(order)`
   * hears every saved change; `onBack()` shows a back button (the panel).
   */
  constructor(root, { api, openSettings, onChange, onBack, heading = 'Models' }) {
    Object.assign(this, { root, api, openSettings, onChange, onBack, heading });
    this.state = { loading: true, order: [], picker: null, open: new Set(), models: {}, showAll: false };
    root.addEventListener('click', (e) => this.onClick(e));
    root.addEventListener('input', (e) => { if (e.target.classList.contains('mo-search')) this.onSearch(e.target.value); });
    root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.state.picker !== null) this.closePicker(); });
  }

  async load() {
    this.render();
    const res = await this.api('GET', '/providers');
    if (!res.ok) {
      Object.assign(this.state, { loading: false, error: 'The Margin sidecar did not answer. Is it running?' });
      this.render();
      return;
    }
    const d = res.data;
    Object.assign(this.state, {
      loading: false, error: null, order: d.order, custom: d.custom_order, subs: d.subscriptions,
      providers: d.providers, popular: d.popular || [], notYet: d.not_yet || {},
    });
    this.render();
    this.onChange?.(this.state.order);
  }

  // --- saving -------------------------------------------------------------------------

  async save(entries) {
    const res = await this.api('PUT', '/chain', { entries: entries.map(({ provider, model }) => ({ provider, model })) });
    if (!res.ok) {
      this.flash(res.data?.detail || 'Could not save the order.', true);
      return;
    }
    this.state.order = res.data.order;
    this.state.custom = res.data.custom_order;
    this.render();
    this.flash('Saved. New notes use this order.');
    this.onChange?.(this.state.order);
  }

  async reset() {
    const res = await this.api('DELETE', '/chain');
    if (res.ok) {
      this.state.order = res.data.order;
      this.state.custom = false;
      this.render();
      this.flash("Back to Margin's default order.");
      this.onChange?.(this.state.order);
    }
  }

  flash(message, bad = false) {
    this.state.flash = { message, bad };
    this.render();
    clearTimeout(this.flashTimer);
    this.flashTimer = setTimeout(() => { this.state.flash = null; this.render(); }, 4000);
  }

  // --- events -------------------------------------------------------------------------

  async onClick(e) {
    const el = e.target.closest('[data-mo]');
    if (!el || !this.root.contains(el)) return;
    const act = el.dataset.mo;
    const i = el.dataset.i === undefined ? null : Number(el.dataset.i);
    const order = [...this.state.order];
    if (act === 'back') this.onBack?.();
    else if (act === 'open') this.openPicker(i);
    else if (act === 'add') this.openPicker(order.length);
    else if (act === 'close') this.closePicker();
    else if (act === 'up' && i > 0) { [order[i - 1], order[i]] = [order[i], order[i - 1]]; await this.save(order); }
    else if (act === 'down' && i < order.length - 1) { [order[i + 1], order[i]] = [order[i], order[i + 1]]; await this.save(order); }
    else if (act === 'remove' && order.length > 1) { order.splice(i, 1); await this.save(order); }
    else if (act === 'reset') await this.reset();
    else if (act === 'test') await this.test(i);
    else if (act === 'toggle') await this.toggleProvider(el.dataset.id);
    else if (act === 'all') { this.state.showAll = !this.state.showAll; this.render(); }
    else if (act === 'settings') this.openSettings?.(el.dataset.hash || '');
    else if (act === 'pick') {
      const slot = this.state.picker;
      const entry = { provider: el.dataset.provider, model: el.dataset.model };
      if (slot === null) return;
      if (slot >= order.length) order.push(entry); else order[slot] = entry;
      this.state.picker = null;
      await this.save(order);
    }
  }

  openPicker(slot) {
    Object.assign(this.state, { picker: slot, query: '', results: null });
    this.render();
    this.root.querySelector('.mo-search')?.focus();
  }

  closePicker() {
    this.state.picker = null;
    this.render();
  }

  async toggleProvider(id) {
    const s = this.state;
    if (s.open.has(id)) { s.open.delete(id); this.render(); return; }
    s.open.add(id);
    if (s.models[id]?.state !== 'ok') {
      s.models[id] = { state: 'loading' };
      this.render();
      const res = await this.api('GET', `/providers/${encodeURIComponent(id)}/models`);
      s.models[id] = res.ok ? { state: 'ok', list: res.data.models }
        : { state: 'error', error: res.data?.detail || 'Could not list its models.' };
    }
    this.render();
  }

  onSearch(query) {
    this.state.query = query;
    clearTimeout(this.searchTimer);
    if (query.trim().length < 2) { this.state.results = null; this.render(); return; }
    this.searchTimer = setTimeout(async () => {
      const res = await this.api('GET', `/providers/search?q=${encodeURIComponent(query.trim())}`);
      if (this.state.query !== query) return;
      this.state.results = res.ok ? res.data.results : [];
      this.render();
    }, 250);
  }

  async test(i) {
    const entry = this.state.order[i];
    entry.testing = true;
    entry.result = null;
    this.render();
    const res = await this.api('POST', '/providers/test', { provider: entry.provider, model: entry.model });
    entry.testing = false;
    const r = res.data || {};
    entry.result = res.ok && r.ok ? { ok: true, text: `Answered in ${r.seconds}s` }
      : { ok: false, text: r.error || `No answer (${res.status})` };
    this.render();
  }

  // --- drawing ------------------------------------------------------------------------

  render() {
    const s = this.state;
    const search = this.root.querySelector('.mo-search');
    const focused = search && (this.root.getRootNode().activeElement === search);
    this.root.innerHTML = `<div class="mo">
      <div class="mo-head">
        ${this.onBack ? `<button class="mo-icon" data-mo="back" aria-label="Back">${ICON.back}</button>` : ''}
        <div><h2>${escapeHtml(this.heading)}</h2>
        <p>Margin tries these in order. When one is busy, out of quota or fails, the next one writes the note.</p></div>
      </div>
      ${s.loading ? '<div class="mo-msg">Asking the sidecar what is set up…</div>' : ''}
      ${s.error ? `<div class="mo-msg bad">${escapeHtml(s.error)}</div>` : ''}
      ${s.loading || s.error ? '' : this.orderHtml()}
      ${s.flash ? `<div class="mo-flash" data-bad="${s.flash.bad}">${escapeHtml(s.flash.message)}</div>` : ''}
    </div>`;
    const input = this.root.querySelector('.mo-search');
    if (input) {
      input.value = s.query || '';
      if (focused) {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
    }
  }

  orderHtml() {
    const s = this.state;
    const slots = s.order.map((e, i) => `
      <li class="mo-slot" data-ready="${e.ready}">
        <span class="mo-n">${i + 1}</span>
        <button class="mo-pick" data-mo="open" data-i="${i}" aria-expanded="${s.picker === i}">
          <span class="mo-title">${escapeHtml(e.title || `${e.provider}/${e.model}`)}</span>
          <span class="mo-detail">${escapeHtml(e.detail || e.model)}</span>
          <span class="mo-state">${e.testing ? 'Testing…'
            : e.result ? `<span data-ok="${e.result.ok}">${escapeHtml(e.result.text)}</span>`
              : e.ready ? `Ready${e.vision === false ? ' · text only, cannot see slides' : ''}`
                : `<span data-ok="false">${escapeWithCode(e.hint || 'Not set up')}</span>`}</span>
        </button>
        <span class="mo-actions">
          <button class="mo-icon" data-mo="test" data-i="${i}" title="Test: ask it for one word">Test</button>
          <button class="mo-icon" data-mo="up" data-i="${i}" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>${ICON.up}</button>
          <button class="mo-icon" data-mo="down" data-i="${i}" aria-label="Move down" ${i === s.order.length - 1 ? 'disabled' : ''}>${ICON.down}</button>
          <button class="mo-icon" data-mo="remove" data-i="${i}" aria-label="Remove" ${s.order.length === 1 ? 'disabled' : ''}>${ICON.remove}</button>
        </span>
      </li>${s.picker === i ? `<li class="mo-picker-row">${this.pickerHtml()}</li>` : ''}`).join('');
    const adding = s.picker !== null && s.picker >= s.order.length;
    return `<ol class="mo-order">${slots}${adding ? `<li class="mo-picker-row">${this.pickerHtml()}</li>` : ''}</ol>
      <div class="mo-foot">
        <button class="mo-add" data-mo="add" ${s.order.length >= 8 ? 'disabled' : ''}>+ Add a fallback</button>
        ${s.custom ? '<button class="mo-link" data-mo="reset">Reset to Margin\'s default</button>' : '<span class="mo-note">Margin\'s default order</span>'}
      </div>`;
  }

  pickerHtml() {
    const s = this.state;
    const head = `<div class="mo-picker-head">
        <input class="mo-search" type="search" placeholder="Search every model: gpt, claude, llama, gemma…" autocomplete="off" spellcheck="false">
        <button class="mo-icon" data-mo="close" aria-label="Close">${ICON.remove}</button></div>`;
    if (s.results) {
      return `<div class="mo-picker">${head}${s.results.length ? s.results.map((m) => this.modelRow(m.provider, m, {
        prefix: m.provider_name, ready: m.ready, subscription: m.subscription })).join('')
        : '<div class="mo-msg">Nothing matches.</div>'}</div>`;
    }
    const providers = s.providers || [];
    const byId = Object.fromEntries(providers.map((p) => [p.id, p]));
    const readyApi = providers.filter((p) => p.ready && !p.local && p.supported);
    const local = providers.filter((p) => p.local);
    const popular = (s.popular || []).map((id) => byId[id]).filter((p) => p && !p.ready && p.supported);
    const rest = providers.filter((p) => p.supported && !p.ready && !p.local && !(s.popular || []).includes(p.id));
    const unsupported = providers.filter((p) => !p.supported).map((p) => p.name);
    const group = (title, body) => (body ? `<div class="mo-group"><h4>${title}</h4>${body}</div>` : '');
    return `<div class="mo-picker">${head}
      ${group("Margin's own", MARGIN_ENGINES.map((e) => this.engineRow(e)).join(''))}
      ${group('Subscriptions', (s.subs || []).map((sub) => this.subRow(sub)).join(''))}
      ${group('On this Mac', local.map((p) => this.providerRow(p)).join(''))}
      ${group('Your API keys', readyApi.map((p) => this.providerRow(p)).join(''))}
      ${group('Popular (add a key)', popular.map((p) => this.providerRow(p)).join(''))}
      <div class="mo-group"><button class="mo-link" data-mo="all">${s.showAll ? 'Hide' : 'Show'} all ${rest.length} other providers</button>
        ${s.showAll ? rest.map((p) => this.providerRow(p)).join('') : ''}</div>
      ${group('Not yet', [
        ...Object.values(s.notYet || {}).map((msg) => `<div class="mo-msg">${escapeHtml(msg)}</div>`),
        unsupported.length ? `<div class="mo-msg">Need cloud-account setup Margin does not do yet: ${escapeHtml(unsupported.join(', '))}</div>` : '',
      ].join(''))}
    </div>`;
  }

  engineRow(e) {
    return `<button class="mo-model" data-mo="pick" data-provider="${e.provider}" data-model="${e.model}">
      <span class="mo-model-main"><span class="mo-model-name">${escapeHtml(e.title)}</span>
      <span class="mo-model-meta">${escapeHtml(e.detail)}</span></span></button>`;
  }

  subRow(sub) {
    const open = this.state.open.has(sub.id);
    if (!sub.ready) {
      return `<div class="mo-prov off"><span class="mo-model-main"><span class="mo-model-name">${escapeHtml(sub.name)}</span>
        <span class="mo-model-meta">${escapeHtml(sub.tool)}: ${escapeWithCode(sub.hint || '')}</span></span>
        <button class="mo-link" data-mo="settings" data-hash="sub=${sub.id}">How to set up</button></div>`;
    }
    return `<button class="mo-prov" data-mo="toggle" data-id="${sub.id}" aria-expanded="${open}">
        <span class="mo-model-main"><span class="mo-model-name">${escapeHtml(sub.name)}</span>
        <span class="mo-model-meta">Through ${escapeHtml(sub.tool)}${sub.images ? '' : ' · text only'}</span></span>
        <span class="mo-chev">${ICON.chevron}</span></button>
      ${open ? `<div class="mo-models">${this.modelsHtml(sub.id, true)}</div>` : ''}`;
  }

  providerRow(p) {
    const open = this.state.open.has(p.id);
    if (!p.ready) {
      const action = p.local ? '' : `<button class="mo-link" data-mo="settings" data-hash="key=${escapeHtml(p.key || '')}">Add key</button>`;
      const canList = p.local;  // a local app lists what it has downloaded even while its server is off
      if (!canList) {
        return `<div class="mo-prov off"><span class="mo-model-main"><span class="mo-model-name">${escapeHtml(p.name)}</span>
          <span class="mo-model-meta">${p.models ? `${p.models} models · ` : ''}${escapeHtml(p.hint || 'Needs a key')}</span></span>${action}</div>`;
      }
    }
    const meta = p.local ? (p.ready ? 'Running on this Mac' : escapeHtml(p.hint || 'Not running'))
      : `${p.models ? `${p.models} models · ` : ''}key ${p.key_set_in === 'margin' ? 'saved in Margin' : 'found'}`;
    return `<button class="mo-prov" data-mo="toggle" data-id="${escapeHtml(p.id)}" aria-expanded="${open}">
        <span class="mo-model-main"><span class="mo-model-name">${escapeHtml(p.name)}</span>
        <span class="mo-model-meta">${meta}</span></span><span class="mo-chev">${ICON.chevron}</span></button>
      ${open ? `<div class="mo-models">${this.modelsHtml(p.id, p.ready)}</div>` : ''}`;
  }

  modelsHtml(id, ready) {
    const got = this.state.models[id];
    if (!got || got.state === 'loading') return '<div class="mo-msg">Loading models…</div>';
    if (got.state === 'error') return `<div class="mo-msg bad">${escapeHtml(got.error)}</div>`;
    if (!got.list.length) return '<div class="mo-msg">No models here yet. Download one in the app, then open this again.</div>';
    const shown = got.list.slice(0, 60);
    return shown.map((m) => this.modelRow(id, m, { ready })).join('')
      + (got.list.length > shown.length ? `<div class="mo-msg">${got.list.length - shown.length} more: search for them above.</div>` : '');
  }

  modelRow(provider, m, { prefix = '', ready = true, subscription = false } = {}) {
    const bits = [
      m.vision === false ? 'text only' : m.vision ? 'sees slides' : '',
      contextLabel(m.context), priceLabel(m), m.reasoning ? 'reasons' : '',
      m.installed_only ? 'downloaded; start the server to use it' : '',
    ].filter(Boolean);
    const name = `${prefix ? `${escapeHtml(prefix)} · ` : ''}${escapeHtml(m.name && m.name !== m.id ? m.name : m.id)}`;
    const body = `<span class="mo-model-main"><span class="mo-model-name">${name}</span>
      <span class="mo-model-meta">${m.name && m.name !== m.id ? `${escapeHtml(m.id)} · ` : ''}${escapeHtml(bits.join(' · '))}</span></span>`;
    if (!ready) {
      const [hash, label] = subscription ? [`sub=${provider}`, 'How to set up'] : [`provider=${provider}`, 'Add key'];
      return `<div class="mo-model off">${body}<button class="mo-link" data-mo="settings" data-hash="${escapeHtml(hash)}">${label}</button></div>`;
    }
    return `<button class="mo-model" data-mo="pick" data-provider="${escapeHtml(provider)}" data-model="${escapeHtml(m.id)}">${body}</button>`;
  }
}
