// Deciding which moments of a lecture are worth a picture.
//
// The browser samples the playing video about once a second as a small grey
// thumbnail (a "signature"). This tracker answers one question per sample:
// is this a picture worth keeping?
//
// A picture is worth keeping when the screen has SETTLED (it has stopped
// changing between samples) on something DIFFERENT from the last picture kept.
// That rule catches every slide, every step of a slide building up bullet by
// bullet, every screen of code, and ignores transitions, fades and the frames
// of an animation in progress.
//
// It deliberately does not try to decide whether two pictures are "the same
// slide". A first version did, by average brightness change, and on real
// slides (white, thin text) a completely new slide barely moved the average,
// so four slides came out as one. Now "same slide?" is answered by the
// sidecar with the full image in hand (see sessions.add_frame: a later
// capture that still contains everything an earlier one drew is the same
// slide building up, and replaces it).
//
// Change is measured as the FRACTION OF PIXELS that changed noticeably, not
// the average change, because slide text is sparse: a new title and three new
// bullets change a few percent of pixels by a lot, which an average dilutes
// to nothing.
//
// Pixels that never stop changing are left out: the lecturer's face in a
// corner bubble, a clock, a looping animation. On a course filmed that way
// (Udemy's LLM Engineering) the screen otherwise never looked settled, and
// Margin only took its once-every-90-seconds picture. Each pixel's recent
// activity is tracked; one that changed in a good share of recent samples is
// "moving" and ignored, and the moving area is sent with each picture so the
// sidecar ignores it too when deciding whether two pictures are one slide.

import { signatureSpread } from './util.js';

export const DEFAULTS = {
  pixelDelta: 22,     // grey levels a pixel must move to count as changed (ignores compression noise)
  stillFrac: 0.006,   // below this fraction changed between samples, the screen is still
  newFrac: 0.004,     // this fraction different from the last kept picture = new content
  settleSamples: 1,   // still across this many sample gaps (at 2 samples/s) = settled
  maxGapSec: 90,      // a lecturer in front of a board never fully settles: keep one anyway
  looseStillFrac: 0.04,
  minSpread: 6,       // a flat frame (black, white flash, fade) carries nothing
  activityDecay: 0.9, // per sample: recent activity weighs most (about the last 10 samples)
  movingAbove: 0.3,   // a pixel active above this is moving (changed in ~a third of recent samples)
  minQuiet: 0.35,     // if less than this share of the frame is quiet, nothing is masked (a video clip)
};

export function changedFraction(a, b, pixelDelta = DEFAULTS.pixelDelta, moving = null) {
  if (!a || !b || a.length !== b.length) return 1;
  let n = 0;
  let counted = 0;
  for (let i = 0; i < a.length; i++) {
    if (moving && moving[i]) continue;
    counted++;
    if (Math.abs(a[i] - b[i]) > pixelDelta) n++;
  }
  return counted ? n / counted : 0;
}

export class SceneTracker {
  constructor(options = {}) {
    this.opts = { ...DEFAULTS, ...options };
    this.reset();
  }

  /** Forget the recent past (after a seek): the next settled frame is judged fresh. */
  reset() {
    this.prev = null;
    this.still = 0;
    this.lastKept = null;
    this.lastKeptT = null;
    this.pending = false;
    // The moving areas (a face bubble) stay where they are across a seek.
  }

  /** Learn which pixels keep changing, from two consecutive samples. */
  learn(sig, prev) {
    const o = this.opts;
    if (!this.activity || this.activity.length !== sig.length) this.activity = new Float32Array(sig.length);
    for (let i = 0; i < sig.length; i++) {
      const changed = Math.abs(sig[i] - prev[i]) > o.pixelDelta ? 1 : 0;
      this.activity[i] = this.activity[i] * o.activityDecay + changed * (1 - o.activityDecay);
    }
  }

  /**
   * The moving pixels (1) and the quiet ones (0), or null when nothing is
   * masked: none move, or so many do that it is a video clip, not a slide.
   */
  moving() {
    if (!this.activity) return null;
    const mask = new Uint8Array(this.activity.length);
    let n = 0;
    for (let i = 0; i < mask.length; i++) {
      if (this.activity[i] > this.opts.movingAbove) { mask[i] = 1; n++; }
    }
    if (!n || 1 - n / mask.length < this.opts.minQuiet) return null;
    return mask;
  }

  /**
   * Feed one sample at video time t. Returns {capture: true} when this frame
   * should be kept (uploaded), {capture: false} otherwise.
   */
  feed(sig, t) {
    const o = this.opts;
    if (signatureSpread(sig) < o.minSpread) {
      this.prev = null;
      this.still = 0;
      return { capture: false };
    }
    if (this.prev) this.learn(sig, this.prev);
    const moving = this.moving();
    const step = this.prev ? changedFraction(sig, this.prev, o.pixelDelta, moving) : 1;
    this.prev = sig;
    this.still = step < o.stillFrac ? this.still + 1 : 0;

    const differsFromKept = !this.lastKept || changedFraction(sig, this.lastKept, o.pixelDelta, moving) >= o.newFrac;
    if (!differsFromKept) return { capture: false };

    const settled = this.still >= o.settleSamples;
    const overdue = this.lastKeptT !== null && t - this.lastKeptT >= o.maxGapSec && step < o.looseStillFrac;
    const first = this.lastKeptT === null && this.still >= o.settleSamples;
    if (settled || overdue || first) {
      this.lastKept = sig;
      this.lastKeptT = t;
      return { capture: true };
    }
    return { capture: false };
  }

  /**
   * The viewer paused. A paused frame is settled by definition, and pausing
   * on a slide is the viewer saying it matters, so keep it if it is new.
   */
  offer(sig, t) {
    if (signatureSpread(sig) < this.opts.minSpread) return { capture: false };
    if (this.lastKept && changedFraction(sig, this.lastKept, this.opts.pixelDelta, this.moving()) < this.opts.newFrac) {
      return { capture: false };
    }
    this.lastKept = sig;
    this.lastKeptT = t;
    this.prev = sig;
    return { capture: true };
  }
}
