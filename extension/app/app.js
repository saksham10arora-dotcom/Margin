// Margin in the page: finds the lecture, runs the sensors, talks to the
// sidecar, and keeps the panel honest about all of it.
//
// The loop is deliberately boring: once a second, look for the lecture's
// <video> and ask the platform adapter which lecture this is. If either
// changed, the previous lecture is closed out (and composed, if it was
// watched and Auto is on) and the new one is opened. That one check covers
// every way a lecture can change: YouTube and Udemy navigating in-page,
// autoplay moving to the next lecture, a player swapping its <video>.

import { adapterFor, detectPlatform, findVideo } from './adapters.js';
import { Panel } from './panel.js';
import { isReserved, markBeside, planLayout, releaseSpace, removeLayoutStyle, reserveSpace } from './layout.js';
import { ModelOrder } from './models-ui.js';
import { AudioRecorder, FrameSampler } from './sensors.js';
import { coverage, mergeRanges, shouldAutoCompose, textBetween } from './util.js';

// Course platforms are captured the moment a lecture plays. YouTube and the
// open web are opt-in per video: most of what people watch there is not a
// lecture, and capturing a music video helps nobody.
const CAPTURE_BY_DEFAULT = { udemy: true, coursera: true, local: true, youtube: false, web: false };

// The sidecar is long-running, so it can be older than a freshly reloaded
// extension. Below this, features the extension relies on are missing.
const MIN_SIDECAR = [2, 8, 0];

function older(version, min) {
  const v = String(version || '0').split('.').map(Number);
  for (let i = 0; i < min.length; i++) {
    if ((v[i] || 0) !== min[i]) return (v[i] || 0) < min[i];
  }
  return false;
}

const SPEECH_LABEL = {
  'udemy-captions': 'Udemy CC',
  'udemy-auto-captions': 'Udemy auto CC',
  'youtube-captions': 'YouTube CC',
  'page-captions': 'Player CC',
};

export async function start(bridge, { byUser = false } = {}) {
  const app = new MarginApp(bridge);
  await app.init({ byUser });
  return app;
}

class MarginApp {
  constructor(bridge) {
    this.bridge = bridge;
    this.platform = detectPlatform();
    this.adapter = adapterFor(this.platform);
    this.api = bridge.api;
    // Captures the sidecar could not take (it was restarting, say), sent again
    // once it answers. Kept across lectures: each item names its session.
    this.outbox = [];
    this.reset();
  }

  reset() {
    this.meta = null;
    this.session = null;
    this.cues = [];
    this.frames = new Map(); // id -> t
    this.ranges = [];
    this.rangeStart = null;
    this.lastT = null;
    this.lastWatchedPost = 0;
    this.composed = false;
    this.stale = false; // captured something the note does not have yet
    this.edited = false; // you changed the note in your vault since Margin wrote it
    this.status = { state: 'idle' };
    this.speech = 'checking';
    this.capturing = false;
    this.audioInFlight = new Set();
  }

  async init({ byUser = false } = {}) {
    const settings = await this.bridge.storageGet({ marginAuto: true, marginCapture: {}, marginOpen: {} });
    this.auto = settings.marginAuto !== false;
    this.captureSettings = settings.marginCapture || {};
    this.openPrefs = settings.marginOpen || {};

    this.panel = new Panel(this.bridge, {
      onPrimary: (a) => this.onPrimary(a),
      onAuto: (on) => { this.auto = on; this.bridge.storageSet({ marginAuto: on }); },
      onSeek: (t) => { if (this.video) { this.video.currentTime = t; this.video.play?.().catch(() => {}); } },
      onMenu: (act) => this.onMenu(act),
      onOpenLecture: (url) => { if (url) location.href = url; },
      onView: (view) => this.onView(view),
      onBackfill: () => this.backfill(),
      onClose: () => this.destroy(),
      onLayout: (expanded) => {
        this.layout(expanded);
        // Remember open/closed per site, so YouTube can stay out of the way
        // while Udemy opens ready to go.
        this.openPrefs = { ...this.openPrefs, [this.platform]: expanded };
        this.bridge.storageSet({ marginOpen: this.openPrefs });
      },
    });
    await this.panel.mount();
    this.panel.setAuto(this.auto);
    this.api('GET', '/chain').then((res) => res.ok && this.showOrderLabel(res.data.order));
    this.watchLayout();
    // Course sites open expanded. Elsewhere (a MrBeast video is not a lecture)
    // Margin starts as a small tab on the edge, unless you opened it yourself
    // or have chosen to keep it open on this site.
    const open = byUser || (this.openPrefs[this.platform] ?? CAPTURE_BY_DEFAULT[this.platform] ?? false);
    if (open) this.layout(true);
    else this.panel.collapseQuietly();

    await this.checkHealth();
    this.tickTimer = setInterval(() => this.tick(), 1000);
    this.tick();
  }

  // --- sidecar health ----------------------------------------------------------

  async checkHealth() {
    const res = await this.api('GET', '/health');
    this.health = res.ok ? res.data : null;
    if (!this.health) {
      this.panel.setLive('off');
      this.panel.setSpeech('Sidecar offline', 'off');
      this.panel.setPrimary('retry', 'Sidecar is not running · Retry', { quiet: true });
      this.panel.showNoteEmpty('Start it with scripts/start.sh in the Margin folder, then press Retry.');
      clearTimeout(this.healthRetry);
      this.healthRetry = setTimeout(() => this.checkHealth().then(() => this.health && this.reopen()), 5000);
      return false;
    }
    this.panel.setLive(this.capturing ? 'live' : 'ok');
    // Nothing in the model order can write yet (a fresh install): say so now,
    // not with an error after a whole lecture.
    this.panel.showSetup(this.health.setup_ready === false);
    if (older(this.health.version, MIN_SIDECAR) && !this.warnedVersion) {
      this.warnedVersion = true;
      this.panel.toast('Your Margin sidecar is out of date. Restart it (quit it, or log out and back in).', 9000);
    }
    return true;
  }

  async reopen() {
    this.lectureKey = null; // forces tick() to open the lecture afresh
    this.tick();
  }

  // --- the loop -----------------------------------------------------------------

  tick() {
    if (this.outbox.length && !this.flushing && Date.now() - (this.lastFlush || 0) > 4000) this.flush();
    const key = this.adapter.lectureKey();
    // Not a lecture page (YouTube's home feed with its hover previews, a Udemy
    // quiz): keep showing the last lecture, capture nothing.
    if (!key) {
      if (this.capturing) this.stopCapture();
      return;
    }
    const video = findVideo();
    if (video &&(video !== this.video || key !== this.lectureKey) && this.health) {
      this.switchTo(video, key);
      return;
    }
    if (this.video && this.session) this.track();
  }

  async switchTo(video, key) {
    const token = (this.switchToken = (this.switchToken || 0) + 1);
    await this.leave('leaving');
    this.reset();
    this.video = video;
    this.lectureKey = key;

    // The duration is often unknown for the first moments of a load.
    for (let i = 0; i < 20 && !Number.isFinite(video.duration); i++) await sleep(250);
    const meta = await this.adapter.lectureInfo(video);
    if (token !== this.switchToken) return; // superseded by a faster navigation
    this.meta = meta;
    this.panel.setLecture(meta);
    this.listen(video);
    this.observeVideo(video);
    if (this.panel.expanded) this.layout(true);

    // On course sites a lecture is opened (and so stored) straight away. On
    // opt-in sites nothing is stored until you press Capture: only a lookup,
    // so a lecture you captured before still reopens with its slides and notes.
    let res = this.armed()
      ? await this.api('POST', '/session', meta)
      : await this.api('POST', '/session/lookup', meta);
    if (!res.ok && !this.armed() && (res.status === 404 || res.status === 405)) {
      // An older sidecar without the lookup: behave as "never captured",
      // which stores nothing, rather than falling back to opening a session.
      res = { ok: true, data: { exists: false } };
    }
    if (!res.ok) { await this.checkHealth(); return; }
    if (token !== this.switchToken) return;
    let summary = this.armed() ? res.data : (res.data.exists ? res.data.summary : null);
    if (summary && !this.armed()) {
      // Captured before: refresh what it knows (a title read too early, say),
      // which is safe now because it already exists.
      const fresh = await this.api('POST', '/session', meta);
      if (fresh.ok) summary = fresh.data;
    }

    if (summary) {
      await this.openSession(summary, token);
      if (token !== this.switchToken) return;
      // A course lecture, or one you chose to capture before: keep capturing,
      // notes or not, so a rewatch fills in whatever the first watch missed.
      this.startCapture();
    } else {
      this.panel.setSpeech('Not capturing', 'ok');
      this.panel.showNoteEmpty('Press "Capture this lecture" to collect its slides and speech. '
        + 'Until then Margin stores nothing about this video.');
    }
    this.refreshControls();
    if (this.meta.course_id && this.session) this.loadCourse();
  }

  /** Course platforms capture on their own; everything else waits for you. */
  armed() {
    return this.captureSettings[this.platform] ?? CAPTURE_BY_DEFAULT[this.platform] ?? false;
  }

  async openSession(summary, token) {
    this.session = summary.key;
    await this.restore(summary);
    await this.findSpeech(token);
  }

  /** Reopening a lecture shows what was already captured and written. */
  async restore(summary) {
    this.status = summary.status || { state: 'idle' };
    this.stale = Boolean(summary.stale);
    this.edited = Boolean(summary.edited);
    // Everything watched before, not just since this page load: watching half,
    // reloading and watching the rest is a whole lecture.
    this.ranges = mergeRanges(summary.watched || []);
    for (const f of summary.frames || []) this.frames.set(f.id, f.t);
    this.panel.setSlides(this.frames.size);
    this.panel.setWatched(summary.coverage || 0);
    for (const f of summary.frames || []) {
      this.panel.addCard({ id: f.id, t: f.t });
      this.bridge.send({ type: 'blob', path: `/session/${this.session}/frame/${f.id}` })
        .then((r) => r?.ok && this.panel.addCard({ id: f.id, t: f.t, img: r.dataUrl }));
    }
    const note = await this.api('GET', `/session/${this.session}/note`);
    this.composed = note.ok;
    if (note.ok) {
      this.note = note.data;
      await this.panel.showNote(note.data);
      this.loadCode();
    } else if (['queued', 'composing', 'running'].includes(this.status.state)) {
      this.pollCompose();
    } else {
      this.panel.showNoteEmpty(this.auto
        ? 'They are written automatically when this lecture ends. Or press "Write notes now".'
        : 'Press "Write notes now" when you are done watching.');
    }
  }

  async findSpeech(token) {
    let caps = null;
    try {
      caps = await this.adapter.captions(this.meta, this.bridge, this.video);
    } catch (e) {
      console.debug('[Margin] captions unavailable:', e?.message || e);
    }
    if (token !== this.switchToken) return;
    if (caps?.cues?.length) {
      this.cues = caps.cues;
      const res = await this.api('POST', `/session/${this.session}/captions`, { cues: caps.cues, source: caps.source });
      this.noteStale(res);
      this.speech = 'captions';
      const label = SPEECH_LABEL[caps.source] || 'Captions';
      this.panel.setSpeech(`${label} · ${res.data?.cue_count ?? caps.cues.length} lines`, 'ok');
      return;
    }
    if (this.health?.asr?.available && AudioRecorder.supported(this.video)) {
      this.speech = 'listen';
      this.panel.setSpeech('No captions · will listen', 'ok');
    } else {
      this.speech = 'none';
      this.panel.setSpeech(this.health?.asr?.available ? 'No captions · audio blocked' : 'No captions · slides only', 'off');
    }
  }

  listen(video) {
    this.unlisten?.();
    const onEnded = () => this.onEnded();
    const onPlay = () => {
      if (this.session && !this.capturing) this.startCapture();
    };
    video.addEventListener('ended', onEnded);
    video.addEventListener('play', onPlay);
    this.unlisten = () => {
      video.removeEventListener('ended', onEnded);
      video.removeEventListener('play', onPlay);
    };
  }

  // --- capture ----------------------------------------------------------------------

  startCapture() {
    if (this.capturing || !this.video || !this.session) return;
    this.capturing = true;
    const isBlocked = () => Boolean(this.adapter.isAd?.());
    this.sampler = new FrameSampler(this.video, {
      bridge: this.bridge,
      isBlocked,
      onKeyframe: (kf) => this.onKeyframe(kf),
      excludeRect: () => this.panel.rect(),
      onModeChange: (mode) => {
        if (mode === 'tab') this.panel.toast('This video blocks direct capture, so Margin reads the screen instead. Keep this tab visible.', 6000);
      },
    });
    this.sampler.start();
    if (this.speech === 'listen') {
      this.recorder = new AudioRecorder(this.video, {
        isBlocked,
        onSegment: (blob, start, end, rate) => {
          const job = this.onAudio(blob, start, end, rate).finally(() => this.audioInFlight.delete(job));
          this.audioInFlight.add(job);
        },
      });
      try {
        this.recorder.start();
        this.panel.setSpeech('Listening · Whisper on this Mac', 'live');
      } catch (e) {
        this.recorder = null;
        this.speech = 'none';
        this.panel.setSpeech('No captions · audio blocked', 'off');
      }
    }
    this.panel.setLive('live');
    this.refreshControls();
  }

  stopCapture() {
    if (!this.capturing) return;
    this.capturing = false;
    this.sampler?.stop();
    this.recorder?.stop();
    this.sampler = null;
    this.recorder = null;
    this.panel.setLive(this.health ? 'ok' : 'off');
    if (this.speech === 'listen') this.panel.setSpeech(`Heard · ${this.cues.length} lines`, 'ok');
  }

  async onKeyframe({ t, dataUrl, moving }) {
    const session = this.session;
    const res = await this.send(session, 'frame', { t, data: dataUrl, ...(moving ? { moving } : {}) });
    this.onFrameSaved(session, res, t, dataUrl);
  }

  onFrameSaved(session, res, t, dataUrl) {
    if (!res.ok || session !== this.session) return;
    this.noteStale(res);
    const { id } = res.data;
    this.frames.set(id, Math.min(this.frames.get(id) ?? t, t));
    this.panel.addCard({ id, t: this.frames.get(id), img: dataUrl });
    this.refreshCardTexts();
    this.panel.setSlides(this.frames.size);
    this.refreshControls();
  }

  async onAudio(blob, start, end, rate) {
    const session = this.session;
    const data = await blobToBase64(blob);
    const res = await this.send(session, 'audio', { data, mime: blob.type, video_start: start, rate });
    this.onAudioSaved(session, res);
  }

  onAudioSaved(session, res) {
    if (!res.ok || session !== this.session) return;
    this.noteStale(res);
    const fresh = res.data.cues || [];
    if (!fresh.length) return;
    const lo = Math.min(...fresh.map((c) => c.start));
    const hi = Math.max(...fresh.map((c) => c.end));
    this.cues = this.cues.filter((c) => c.end <= lo || c.start >= hi).concat(fresh).sort((a, b) => a.start - b.start);
    this.refreshCardTexts();
    this.panel.setSpeech(`Listening · ${this.cues.length} lines heard`, 'live');
    this.refreshControls();
  }

  /**
   * POST a capture. If the sidecar cannot be reached it waits in the outbox
   * (up to ~40 MB, oldest slides dropped first) and is sent when it is back,
   * so a restart mid-lecture loses nothing.
   */
  async send(session, kind, body) {
    const res = await this.api('POST', `/session/${session}/${kind}`, body);
    if (!res.ok && res.status === 0) this.hold({ session, kind, body });
    return res;
  }

  hold(item) {
    this.outbox.push(item);
    const size = (x) => (x.body.data?.length || 0) + 100;
    let total = this.outbox.reduce((n, x) => n + size(x), 0);
    while (total > 40e6 && this.outbox.length > 1) {
      const i = this.outbox.findIndex((x) => x.kind !== 'watched');
      if (i < 0) break;
      total -= size(this.outbox[i]);
      this.outbox.splice(i, 1);
    }
  }

  async flush() {
    this.flushing = true;
    this.lastFlush = Date.now();
    try {
      while (this.outbox.length) {
        const item = this.outbox[0];
        const res = await this.api('POST', `/session/${item.session}/${item.kind}`, item.body);
        if (!res.ok && res.status === 0) break; // still away: try again in a few seconds
        this.outbox.shift();
        if (item.kind === 'frame') this.onFrameSaved(item.session, res, item.body.t, item.body.data);
        if (item.kind === 'audio') this.onAudioSaved(item.session, res);
      }
    } finally {
      this.flushing = false;
    }
  }

  refreshCardTexts() {
    const times = [...this.frames.values()].sort((a, b) => a - b);
    this.panel.updateCardTexts((t) => {
      const next = times.find((x) => x > t) ?? t + 90;
      return textBetween(this.cues, t, next);
    });
  }

  /** Once a second: extend the watched range and the "now" line. */
  track() {
    const v = this.video;
    const ad = Boolean(this.adapter.isAd?.());
    if (ad && this.recorder?.rec) this.recorder.end(); // do not record the ad
    if (!v || v.paused || v.seeking || ad) {
      // Save the stretch that just ended before forgetting where it was.
      if (this.rangeStart !== null) this.commitRange(this.lastT);
      this.lastT = null;
      this.panel.setNow(0, '');
      return;
    }
    const t = v.currentTime;
    const rate = v.playbackRate || 1;
    if (this.lastT !== null && t > this.lastT && t - this.lastT <= 3 * rate + 0.5) {
      if (this.rangeStart === null) this.rangeStart = this.lastT;
    } else if (this.rangeStart !== null) {
      this.commitRange(this.lastT); // a jump: close the stretch before it
    }
    this.lastT = t;
    if (this.rangeStart !== null && Date.now() - this.lastWatchedPost > 10000) this.commitRange(t, true);

    // Visible from DevTools as <margin-panel data-diag>: the first thing to
    // look at when a lecture yields no slides or no speech.
    if (this.sampler) {
      this.panel.host.dataset.diag = JSON.stringify({ mode: this.sampler.mode, ...this.sampler.stats,
        speech: this.speech, cues: this.cues.length, frames: this.frames.size });
    }
    const cue = this.cues.find((c) => c.start <= t && c.end >= t);
    this.panel.setNow(t, this.capturing && cue ? cue.text : '');
  }

  commitRange(end, keepOpen = false) {
    if (this.rangeStart === null || end === null || end <= this.rangeStart) {
      if (!keepOpen) this.rangeStart = null;
      return;
    }
    const range = [this.rangeStart, end];
    this.ranges = mergeRanges([...this.ranges, range]);
    this.lastWatchedPost = Date.now();
    const duration = this.duration();
    this.panel.setWatched(coverage(this.ranges, duration));
    if (this.session) {
      this.send(this.session, 'watched', { start: range[0], end: range[1], duration_sec: duration || null });
    }
    this.rangeStart = keepOpen ? end : null;
  }

  duration() {
    // The lecture's own recorded length first: when a player reuses its
    // <video> for the next lecture, video.duration already describes that one.
    return this.meta?.duration_sec || (Number.isFinite(this.video?.duration) ? this.video.duration : 0);
  }

  watchedFraction() {
    return coverage(this.ranges, this.duration());
  }

  hasContent() {
    return this.cues.length > 0 || this.frames.size > 0;
  }

  /** The sidecar says whether the note is missing something captured since. */
  noteStale(res) {
    if (typeof res?.data?.stale !== 'boolean' || res.data.stale === this.stale) return;
    this.stale = res.data.stale;
    this.refreshControls();
  }

  onEnded() {
    this.sampler?.flush();
    this.commitRange(this.video?.currentTime ?? null);
    this.maybeAutoCompose('ended');
  }

  maybeAutoCompose(reason, { background = false } = {}) {
    // Only lectures you were capturing. Captions are fetched for every video
    // (to show the Speech signal), and a YouTube video you never opted into
    // must not get notes written for it just because it had captions.
    if (!this.auto || !this.capturing) return false;
    const go = shouldAutoCompose({
      reason, coverage: this.watchedFraction(), composed: this.composed, stale: this.stale,
      edited: this.edited, hasContent: this.hasContent(),
    });
    if (go) this.compose({ background, auto: true });
    return go;
  }

  /** Closing out a lecture: navigation, autoplay to the next one, closing the panel. */
  async leave(reason) {
    if (!this.session) return;
    this.sampler?.flush();
    this.commitRange(this.lastT); // not currentTime: the element may already be on the next lecture
    const updating = this.composed;
    if (reason === 'leaving' && this.maybeAutoCompose('leaving', { background: true })) {
      const title = this.meta?.lecture_title || 'the last lecture';
      this.panel.toast(updating
        ? `Adding what you just caught to the notes for "${title}".`
        : `Writing notes for "${title}" in the background.`);
      this.watchInBackground(this.session, title);
    }
    this.stopCapture();
    this.unlisten?.();
  }

  // --- composing ------------------------------------------------------------------

  /**
   * Write the notes for the current lecture. `background` is for a lecture
   * being left behind (autoplay moved on): its notes are written without
   * touching the panel, which already shows the next lecture.
   */
  async compose({ background = false, auto = false } = {}) {
    if (!this.session) return;
    const session = this.session;
    const recorder = this.recorder;
    const inFlight = [...this.audioInFlight];
    this.sampler?.flush();
    // Every word must be in before the notes are written: close the audio
    // segment still recording and wait for the ones still being transcribed.
    if (recorder || inFlight.length) {
      if (!background) {
        this.status = { state: 'queued', message: 'Transcribing the last few seconds' };
        this.panel.show('note');
        this.panel.showProgress(this.status);
        this.refreshControls();
      }
      await recorder?.drain();
      await Promise.allSettled([...inFlight, ...this.audioInFlight]);
    }
    // Anything still held from a sidecar restart belongs in these notes.
    if (this.outbox.some((x) => x.session === session)) await this.flush();
    const res = await this.api('POST', `/session/${session}/compose`, this.composeBody({ auto }));
    if (background) return;
    if (res.ok && res.data?.skipped === 'edited') {
      this.edited = true;
      this.status = res.data.status || this.status;
      this.refreshControls();
      this.panel.toast('You edited this note, so Margin left it alone. "Add what\'s new" rewrites it and keeps your version.', 7000);
      return;
    }
    if (!res.ok) {
      this.panel.showError(res.data?.detail || `Sidecar returned ${res.status}`);
      return;
    }
    this.status = res.data.status;
    this.panel.show('note');
    this.pollCompose();
  }

  pollCompose() {
    const session = this.session;
    clearInterval(this.pollTimer);
    const started = Date.now() / 1000;
    const poll = async () => {
      if (session !== this.session) { clearInterval(this.pollTimer); return; }
      const res = await this.api('GET', `/session/${session}`);
      if (!res.ok) return;
      this.status = res.data.status;
      this.stale = Boolean(res.data.stale);
      this.edited = Boolean(res.data.edited);
      this.refreshControls();
      if (this.status.state === 'done') {
        clearInterval(this.pollTimer);
        this.composed = true;
        await this.loadNote();
        const r = this.status.result || {};
        const bits = [r.slides_embedded?.length ? `${r.slides_embedded.length} slides` : null,
          r.figures?.length ? `${r.figures.length} plots` : null].filter(Boolean).join(', ');
        this.panel.toast(`Notes saved to your vault${bits ? ` with ${bits}` : ''}.`);
        // Capture carries on: whatever plays from here is added next time.
        this.refreshControls();
        if (this.meta?.course_id) this.loadCourse();
      } else if (this.status.state === 'error') {
        clearInterval(this.pollTimer);
        this.panel.showError(this.status.message || 'Unknown error');
      } else {
        this.panel.showProgress({ ...this.status, started });
      }
    };
    poll();
    this.pollTimer = setInterval(poll, 1500);
  }

  /** A lecture left behind while its notes are still being written. */
  watchInBackground(session, title) {
    const check = async () => {
      const res = await this.api('GET', `/session/${session}`);
      const state = res.data?.status?.state;
      if (state === 'done') {
        this.panel.toast(`Notes ready for "${title}".`);
        if (this.meta?.course_id) this.loadCourse();
      } else if (state === 'error') {
        this.panel.toast(`Could not write notes for "${title}". Open it to see why.`, 6000);
      } else {
        setTimeout(check, 4000);
      }
    };
    setTimeout(check, 4000);
  }

  async loadNote() {
    const note = await this.api('GET', `/session/${this.session}/note`);
    if (!note.ok) return;
    this.note = note.data;
    await this.panel.showNote(note.data);
    await this.loadCode();
  }

  async loadCode() {
    const code = await this.api('GET', `/session/${this.session}/code`);
    this.code = code.ok ? code.data : null;
    this.panel.showCode(this.code, this.status?.result?.run);
    this.refreshControls();
  }

  async loadCourse() {
    const courseId = this.meta?.course_id;
    if (!courseId) return;
    const res = await this.api('GET', `/course/${encodeURIComponent(courseId)}`);
    if (!res.ok || courseId !== this.meta?.course_id) return;
    const lectures = res.data.lectures;
    this.panel.showCourse(lectures, this.meta.url);
    if (!this.backfilling) this.offerBackfill(lectures);
    // Keep the list current while notes are being written for it.
    clearTimeout(this.courseTimer);
    if (lectures.some((l) => ['queued', 'composing', 'running'].includes(l.status))) {
      this.courseTimer = setTimeout(() => this.loadCourse(), 10000);
    }
  }

  /** Lectures you finished on the platform (before Margin, say) with no notes. */
  async offerBackfill(lectures) {
    if (!this.adapter.finishedLectures) return;
    let finished = [];
    try {
      finished = await this.adapter.finishedLectures();
    } catch {
      return; // the platform would not say; nothing to offer
    }
    const covered = new Set(lectures
      .filter((l) => l.composed || ['queued', 'composing', 'running'].includes(l.status))
      .map((l) => String(l.lecture_id)));
    this.missing = finished.filter((m) => !covered.has(String(m.lecture_id)));
    if (!this.backfilling) this.panel.showBackfill(this.missing.length);
  }

  /**
   * Notes for finished lectures from their captions. Margin only fetches the
   * captions here; the sidecar writes the notes one at a time in the
   * background, so leaving this page does not stop them.
   */
  async backfill() {
    const todo = this.missing || [];
    if (!todo.length || this.backfilling) return;
    this.backfilling = true;
    const keys = [];
    let noCaptions = 0;
    try {
      for (const [i, meta] of todo.entries()) {
        this.panel.showBackfill(todo.length, `Fetching captions: ${i + 1} of ${todo.length}`);
        const res = await this.api('POST', '/session', meta);
        if (!res.ok) continue;
        let caps = null;
        try {
          caps = await this.adapter.captions(meta, this.bridge);
        } catch { /* counted below */ }
        if (!caps?.cues?.length) { noCaptions++; continue; }
        await this.api('POST', `/session/${res.data.key}/captions`, { cues: caps.cues, source: caps.source });
        keys.push(res.data.key);
      }
      const queued = keys.length
        ? await this.api('POST', `/course/${encodeURIComponent(this.meta.course_id)}/backfill`,
          { keys })
        : null;
      const n = queued?.data?.queued?.length || 0;
      this.panel.toast(n
        ? `Writing ${n} note${n === 1 ? '' : 's'} from captions, one at a time. Each appears in Course when done.`
          + (noCaptions ? ` ${noCaptions} had no captions.` : '')
        : 'Those lectures have no captions to write notes from.', 7000);
    } finally {
      this.backfilling = false;
      this.panel.showBackfill(0);
      this.loadCourse();
    }
  }

  onView(view) {
    if (view === 'course' && this.meta?.course_id) this.loadCourse();
    if (view === 'code' && this.composed) this.loadCode();
  }

  // --- controls -------------------------------------------------------------------

  refreshControls() {
    const p = this.panel;
    if (!this.health) return;
    const busy = ['queued', 'composing', 'running'].includes(this.status?.state);
    if (busy) p.setPrimary('none', 'Writing notes…', { disabled: true });
    else if (this.composed && this.stale) {
      p.setPrimary('recompose', this.edited ? 'Add what\'s new (keeps your edits)' : 'Add what\'s new to the notes');
    }
    else if (this.composed) p.setPrimary('recompose', 'Rewrite the notes', { quiet: true });
    // Captions alone do not count as "captured": they arrive for every video
    // the moment it opens, and offering "Write notes" there would skip the
    // slides entirely. Capture comes first until a slide has been kept.
    else if (!this.capturing && this.frames.size === 0) p.setPrimary('capture', 'Capture this lecture');
    else p.setPrimary('compose', 'Write notes now', { disabled: !this.hasContent() });
    p.setMenuState({ note: this.composed, notebook: Boolean(this.code?.notebook_path) });
  }

  async onPrimary(action) {
    if (action === 'retry') {
      if (await this.checkHealth()) this.reopen();
    } else if (action === 'capture') {
      if (!this.session && this.meta) {
        // The first moment anything about this video is stored.
        const res = await this.api('POST', '/session', this.meta);
        if (!res.ok) { await this.checkHealth(); return; }
        await this.openSession(res.data, this.switchToken);
      }
      this.startCapture();
      if (this.video?.paused) this.video.play?.().catch(() => {});
    } else if (action === 'compose' || action === 'recompose') {
      this.compose();
    }
  }

  async onMenu(act) {
    const note = this.note;
    if (act === 'obsidian' && note?.obsidian_uri) {
      window.open(note.obsidian_uri, '_blank');
    } else if (act === 'obsidian') {
      this.panel.toast('This vault is not inside an Obsidian vault, so there is nothing to open.');
    } else if (act === 'notebook' && this.code?.notebook_path) {
      window.open(`vscode://file${encodeURI(this.code.notebook_path)}`, '_blank');
      navigator.clipboard?.writeText(this.code.notebook_path).catch(() => {});
      this.panel.toast('Opening in VS Code. The path is on your clipboard too.');
    } else if (act === 'copy' && note) {
      await navigator.clipboard.writeText(note.content);
      this.panel.toast('Note copied as markdown.');
    } else if (act === 'cards' && note) {
      this.panel.toast('Making flashcards…', 20000);
      const res = await this.api('POST', '/export/flashcards', { filename: note.filename });
      if (!res.ok) { this.panel.toast(`Flashcards failed: ${res.data?.detail || res.status}`, 6000); return; }
      download(`${note.filename.split('/').pop().replace(/\.md$/, '')} - flashcards.txt`, res.data.tsv);
      this.panel.toast(`${res.data.count} flashcards downloaded. Import the .txt into Anki.`, 5000);
    } else if (act === 'recompose') {
      this.compose();
    } else if (act === 'model') {
      this.openModels();
    } else if (act === 'settings') {
      this.bridge.send({ type: 'open-settings' });
    }
  }

  // --- the model menu ---------------------------------------------------------------

  /** What goes with a compose request: whether Margin decided on its own. */
  composeBody({ auto = false } = {}) {
    return auto ? { auto: true } : undefined;
  }

  showOrderLabel(order) {
    const first = order?.[0];
    this.panel.setModelLabel(first ? `${first.title}${order.length > 1 ? ` +${order.length - 1}` : ''}` : 'none');
  }

  /** Your model order, 1st to last, with a dropdown for each place. */
  async openModels() {
    if (this.panel.view !== 'models') this.viewBeforeModels = this.panel.view;
    this.panel.show('models');
    if (!this.modelsUI) {
      this.modelsUI = new ModelOrder(this.panel.$('models-area'), {
        api: this.api,
        openSettings: (hash) => this.bridge.send({ type: 'open-settings', hash }),
        onChange: (order) => {
          this.showOrderLabel(order);
          this.panel.showSetup(!order.some((e) => e.ready));
        },
        onBack: () => this.panel.show(this.viewBeforeModels || (this.composed ? 'note' : 'live')),
        heading: 'Models for your notes',
      });
    }
    await this.modelsUI.load();
  }

  // --- page layout ---------------------------------------------------------------

  /** Put the panel beside the video, never over it (see layout.js). */
  layout(expanded) {
    this.laying = true;
    releaseSpace();
    window.dispatchEvent(new Event('resize'));
    if (expanded) {
      const right = this.video?.getBoundingClientRect().right ?? window.innerWidth;
      const plan = planLayout(window.innerWidth, right);
      if (plan.reserve) reserveSpace(plan.width);
      else markBeside(plan.width);
      window.dispatchEvent(new Event('resize')); // players that size themselves in JS
      this.panel.panel?.style.setProperty('--w', `${plan.width}px`);
    }
    this.laying = false;
  }

  /**
   * Re-fit when the page changes shape underneath the panel: the window is
   * resized, YouTube's theater mode is toggled, a site opens or closes its
   * own sidebar. The video is watched directly, and the panel re-fits only
   * when the video has come to overlap it, which cannot loop: after a re-fit
   * it no longer overlaps.
   */
  watchLayout() {
    let timer;
    const refit = () => {
      clearTimeout(timer);
      timer = setTimeout(() => this.panel.expanded && this.layout(true), 200);
    };
    this.onResize = () => { if (!this.laying) refit(); };
    window.addEventListener('resize', this.onResize);
    this.videoObserver = new ResizeObserver(() => {
      if (this.laying || !this.panel.expanded) return;
      const panelLeft = this.panel.rect()?.left ?? window.innerWidth;
      const videoRight = this.video?.getBoundingClientRect().right ?? 0;
      // Overlap appeared (theater on), or space was reserved that the video
      // no longer needs (theater off): either way, plan again.
      if (videoRight > panelLeft + 4 || (isReserved() && videoRight < panelLeft - 360)) refit();
    });
    this.flexyObserver = new MutationObserver(refit);
  }

  observeVideo(video) {
    this.videoObserver?.disconnect();
    this.videoObserver?.observe(video);
    const flexy = document.querySelector('ytd-watch-flexy');
    this.flexyObserver?.disconnect();
    if (flexy) this.flexyObserver?.observe(flexy, { attributes: true, attributeFilter: ['theater', 'fullscreen'] });
  }

  destroy() {
    this.leave('closing');
    clearInterval(this.tickTimer);
    clearInterval(this.pollTimer);
    clearTimeout(this.healthRetry);
    clearTimeout(this.courseTimer);
    this.videoObserver?.disconnect();
    this.flexyObserver?.disconnect();
    window.removeEventListener('resize', this.onResize);
    removeLayoutStyle();
    window.dispatchEvent(new Event('resize'));
    this.panel.destroy();
    this.bridge.onDestroy?.();
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/tab-separated-values' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
