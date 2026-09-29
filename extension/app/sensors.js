// The two sensors: what the lecture shows, and what it says.
//
// FrameSampler looks at the playing <video> about once a second and, with a
// SceneTracker, keeps every frame where the screen has settled on something
// new. It reads the
// pixels straight off the video element. If a site's video cannot be read
// that way (cross-origin without CORS, or DRM, which reads as black) it falls
// back to a screenshot of the visible tab cropped to the video, which is what
// you see on screen and therefore always works while the tab is visible.
//
// AudioRecorder is only used when a lecture has no captions. It records the
// video's own audio track (video.captureStream(), which does not touch what
// you hear) in ~20-second segments that each know the video time they began
// at and the playback rate, so the sidecar can undo 1.75x and place the words
// correctly. Pausing, seeking and changing speed all close the current
// segment, because each of them breaks the mapping from recording time to
// video time.

import { SceneTracker } from './scenes.js';
import { greySignature, signatureSpread } from './util.js';

const SAMPLE_W = 64;
const SAMPLE_H = 36;
const KEEP_W = 1280;

export class FrameSampler {
  constructor(video, { bridge, onKeyframe, onModeChange, excludeRect, isBlocked }) {
    this.video = video;
    this.isBlocked = isBlocked || (() => false); // e.g. a YouTube ad is playing
    this.bridge = bridge;
    this.onKeyframe = onKeyframe;
    this.onModeChange = onModeChange || (() => {});
    this.excludeRect = excludeRect || (() => null);
    this.tracker = new SceneTracker();
    this.mode = 'video'; // 'video' | 'tab'
    this.small = Object.assign(document.createElement('canvas'), { width: SAMPLE_W, height: SAMPLE_H });
    this.full = document.createElement('canvas');
    this.timer = null;
    this.busy = false;
    this.darkStreak = 0;
    // Counters for diagnosing a lecture that yields no slides (see app.js).
    this.stats = { samples: 0, captures: 0, skipped: 0, lastError: '', brightness: 0, spread: 0 };
    // After a seek the recent past is irrelevant: judge the next settled frame fresh.
    this.onSeek = () => this.tracker.reset();
    this.onPause = () => { if (!this.video.seeking && !this.video.ended) this.tick(true); };
  }

  start() {
    if (this.timer) return;
    this.video.addEventListener('seeked', this.onSeek);
    this.video.addEventListener('pause', this.onPause);
    // Twice a second: at 1.75x a slide's build step can last under two seconds.
    this.timer = setInterval(() => this.tick(), 500);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    this.video.removeEventListener('seeked', this.onSeek);
    this.video.removeEventListener('pause', this.onPause);
  }

  /** Nothing is held back any more: every settled frame is sent as it is seen. */
  flush() {}

  async tick(paused = false) {
    const v = this.video;
    if (this.busy || (v.paused && !paused) || v.ended || v.seeking || v.readyState < 2 || this.isBlocked()) {
      this.stats.skipped++;
      return;
    }
    if (document.visibilityState !== 'visible' && this.mode === 'tab') return;
    this.busy = true;
    try {
      const t = v.currentTime;
      const source = await this.grab();
      if (!source) return;
      const ctx = this.small.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(source.image, source.sx, source.sy, source.sw, source.sh, 0, 0, SAMPLE_W, SAMPLE_H);
      const { sig, brightness } = greySignature(ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data);
      this.stats.samples++;
      this.stats.brightness = Math.round(brightness);
      this.stats.spread = Math.round(signatureSpread(sig));

      // DRM-protected video reads as UNIFORMLY black from the element while the
      // screen shows the lecture. Dark lectures (3Blue1Brown, a dark editor)
      // are near-black on average too, but they have contrast, so the test is
      // "no contrast at all", and one screen capture confirms before switching.
      if (this.mode === 'video' && brightness < 1.5 && this.stats.spread < 1) {
        if (++this.darkStreak >= 6) {
          this.darkStreak = 0;
          if (await this.screenShowsContent()) await this.switchMode('tab');
        }
        return;
      }
      this.darkStreak = 0;

      const verdict = paused ? this.tracker.offer(sig, t) : this.tracker.feed(sig, t);
      if (!verdict.capture) return;
      const scale = Math.min(1, KEEP_W / source.sw);
      this.full.width = Math.round(source.sw * scale);
      this.full.height = Math.round(source.sh * scale);
      this.full.getContext('2d').drawImage(source.image, source.sx, source.sy, source.sw, source.sh,
        0, 0, this.full.width, this.full.height);
      this.stats.captures++;
      this.onKeyframe({ t, dataUrl: this.full.toDataURL('image/jpeg', 0.86), moving: packMask(this.tracker.moving()) });
    } catch (e) {
      this.stats.lastError = `${e?.name || ''}: ${e?.message || e}`.slice(0, 160);
      if (this.mode === 'video' && e?.name === 'SecurityError') await this.switchMode('tab');
    } finally {
      this.busy = false;
    }
  }

  async screenShowsContent() {
    const saved = this.mode;
    this.mode = 'tab';
    try {
      const source = await this.grab();
      if (!source) return false;
      const ctx = this.small.getContext('2d', { willReadFrequently: true });
      ctx.drawImage(source.image, source.sx, source.sy, source.sw, source.sh, 0, 0, SAMPLE_W, SAMPLE_H);
      const { sig } = greySignature(ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data);
      return signatureSpread(sig) > 4;
    } catch {
      return false;
    } finally {
      this.mode = saved;
    }
  }

  async switchMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.tracker.reset();
    this.onModeChange(mode);
  }

  /** The current frame as {image, sx, sy, sw, sh}. */
  async grab() {
    const v = this.video;
    if (this.mode === 'video') {
      if (!v.videoWidth) return null;
      // Throws SecurityError here if the video is cross-origin and tainted.
      const probe = this.small.getContext('2d', { willReadFrequently: true });
      probe.drawImage(v, 0, 0, 1, 1);
      probe.getImageData(0, 0, 1, 1);
      return { image: v, sx: 0, sy: 0, sw: v.videoWidth, sh: v.videoHeight };
    }
    const shot = await this.bridge.send({ type: 'capture-tab' });
    if (!shot?.ok) return null;
    const blob = await (await fetch(shot.dataUrl)).blob();
    const bitmap = await createImageBitmap(blob);
    const rect = visibleVideoRect(v, this.excludeRect());
    if (!rect) return null;
    const k = bitmap.width / window.innerWidth; // device pixel ratio as captured
    return { image: bitmap, sx: rect.x * k, sy: rect.y * k, sw: rect.w * k, sh: rect.h * k };
  }
}

/**
 * The part of the video actually drawing pixels: the element's box minus the
 * letterboxing object-fit adds, clipped to the viewport and to the left of the
 * Margin panel if the panel overlaps it.
 */
export function visibleVideoRect(video, exclude) {
  const box = video.getBoundingClientRect();
  let { x, y, width: w, height: h } = box;
  if (video.videoWidth && video.videoHeight && w && h) {
    const vr = video.videoWidth / video.videoHeight;
    const br = w / h;
    if (br > vr) { const nw = h * vr; x += (w - nw) / 2; w = nw; } else { const nh = w / vr; y += (h - nh) / 2; h = nh; }
  }
  let right = Math.min(x + w, window.innerWidth);
  if (exclude && exclude.left < right && exclude.left > x) right = exclude.left;
  const bottom = Math.min(y + h, window.innerHeight);
  x = Math.max(0, x);
  y = Math.max(0, y);
  if (right - x < 120 || bottom - y < 80) return null;
  return { x, y, w: right - x, h: bottom - y };
}

// --- audio -------------------------------------------------------------------

export class AudioRecorder {
  constructor(video, { onSegment, segmentSec = 20, isBlocked }) {
    this.video = video;
    this.isBlocked = isBlocked || (() => false);
    this.onSegment = onSegment;
    this.segmentSec = segmentSec;
    this.track = null;
    this.rec = null;
    this.lastTime = 0;
    this.listeners = [];
  }

  static supported(video) {
    return typeof video?.captureStream === 'function' && typeof MediaRecorder !== 'undefined';
  }

  start() {
    const stream = this.video.captureStream();
    const [track] = stream.getAudioTracks();
    if (!track) throw new Error('This video exposes no audio track to record.');
    this.track = track;
    const on = (ev, fn) => { this.video.addEventListener(ev, fn); this.listeners.push([ev, fn]); };
    on('timeupdate', () => { if (!this.video.seeking) this.lastTime = this.video.currentTime; });
    on('play', () => this.begin());
    on('pause', () => this.end());
    on('seeking', () => this.end(this.lastTime));
    on('seeked', () => { this.lastTime = this.video.currentTime; if (!this.video.paused) this.begin(); });
    on('ratechange', () => { this.end(); if (!this.video.paused) this.begin(); });
    on('ended', () => this.end());
    if (!this.video.paused) this.begin();
  }

  stop() {
    this.end();
    for (const [ev, fn] of this.listeners) this.video.removeEventListener(ev, fn);
    this.listeners = [];
  }

  begin() {
    if (this.rec || !this.track || this.track.readyState === 'ended' || this.isBlocked()) return;
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
    const rec = new MediaRecorder(new MediaStream([this.track]), { mimeType: mime });
    const seg = { start: this.video.currentTime, rate: this.video.playbackRate, chunks: [], mime, end: null };
    rec.ondataavailable = (e) => { if (e.data?.size) seg.chunks.push(e.data); };
    seg.done = new Promise((resolve) => {
      rec.onstop = () => {
        const end = seg.end ?? this.video.currentTime;
        if (end - seg.start >= 1.5 && seg.chunks.length) {
          this.onSegment(new Blob(seg.chunks, { type: mime }), seg.start, end, seg.rate);
        }
        resolve();
      };
    });
    rec.start();
    this.rec = rec;
    this.seg = seg;
    this.cutTimer = setTimeout(() => { this.end(); if (!this.video.paused) this.begin(); }, this.segmentSec * 1000);
  }

  end(atTime) {
    clearTimeout(this.cutTimer);
    if (!this.rec) return this.seg?.done ?? Promise.resolve();
    this.seg.end = atTime ?? this.video.currentTime;
    try { this.rec.stop(); } catch { /* already stopped */ }
    this.rec = null;
    return this.seg.done;
  }

  /**
   * Close the segment being recorded and wait until it has been handed over.
   * Composing must not start before this: MediaRecorder delivers the last
   * piece of audio asynchronously, after stop(), so without waiting the end
   * of every lecture was missing from its transcript.
   */
  drain() {
    return this.end();
  }
}

/**
 * The moving area (a face bubble) as the sidecar reads it: 64x36 bits, row
 * by row, base64. Null when nothing is moving.
 */
export function packMask(mask) {
  if (!mask) return null;
  const bytes = new Uint8Array(Math.ceil(mask.length / 8));
  for (let i = 0; i < mask.length; i++) if (mask[i]) bytes[i >> 3] |= 128 >> (i & 7);
  return btoa(String.fromCharCode(...bytes));
}
