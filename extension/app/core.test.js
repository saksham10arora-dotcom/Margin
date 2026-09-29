import { describe, expect, it } from 'vitest';
import { planLayout } from './layout.js';
import { SceneTracker, changedFraction } from './scenes.js';
import {
  coverage, cuesFromStarts, formatTs, greySignature, mergeRanges, parseVtt, pickCaption,
  shouldAutoCompose, signatureDiff, textBetween, engineName, noteProvenance,
} from './util.js';

describe('parseVtt', () => {
  it('reads ids, settings, tags and entities', () => {
    const vtt = `WEBVTT

1
00:00:01.000 --> 00:00:04.500 align:start position:0%
<v Instructor>Modern &amp; <c.yellow>portfolio</c> theory</v>

00:01:02.250 --> 00:01:05.000
second line
continues`;
    expect(parseVtt(vtt)).toEqual([
      { start: 1, end: 4.5, text: 'Modern & portfolio theory' },
      { start: 62.25, end: 65, text: 'second line continues' },
    ]);
  });

  it('collapses rolling auto-captions into new words only', () => {
    const vtt = `WEBVTT

00:00:01.000 --> 00:00:02.000
the expected

00:00:02.000 --> 00:00:03.000
the expected return is

00:00:03.000 --> 00:00:04.000
the expected return is`;
    const cues = parseVtt(vtt);
    expect(cues.map((c) => c.text)).toEqual(['the expected', 'return is']);
    expect(cues[1].end).toBe(4);
  });

  it('accepts mm:ss timings and comma decimals', () => {
    expect(parseVtt('WEBVTT\n\n01:02,500 --> 01:03,000\nhi')).toEqual([{ start: 62.5, end: 63, text: 'hi' }]);
  });
});

describe('captions', () => {
  it('prefers English, then human-written', () => {
    const tracks = [
      { locale: 'es_ES', source: 'manual', url: 'es' },
      { locale: 'en_US', source: 'auto', url: 'en-auto' },
      { locale: 'en_GB', source: 'manual', url: 'en-human' },
    ];
    expect(pickCaption(tracks).url).toBe('en-human');
    expect(pickCaption([{ locale: 'hi_IN', source: 'auto', url: 'hi' }]).url).toBe('hi');
    expect(pickCaption([])).toBeNull();
  });

  it('derives end times from YouTube start-only cues', () => {
    expect(cuesFromStarts([{ startSec: 5, text: 'b' }, { startSec: 1, text: ' a ' }])).toEqual([
      { start: 1, end: 5, text: 'a' },
      { start: 5, end: 9, text: 'b' },
    ]);
  });

  it('pulls the words spoken over a slide', () => {
    const cues = [{ start: 0, end: 5, text: 'intro' }, { start: 5, end: 9, text: 'weights sum to one' }];
    expect(textBetween(cues, 6, 20)).toBe('weights sum to one');
    expect(textBetween(cues, 0, 20, 10)).toBe('intro…');
  });
});

describe('ranges', () => {
  it('merges and measures coverage', () => {
    expect(mergeRanges([[10, 20], [0, 5], [21, 30]])).toEqual([[0, 5], [10, 30]]);
    expect(coverage([[0, 50], [40, 60]], 100)).toBe(0.6);
    expect(coverage([], 0)).toBe(0);
  });

  it('auto-composes only watched lectures, once', () => {
    expect(shouldAutoCompose({ reason: 'ended', coverage: 0.7, composed: false, hasContent: true })).toBe(true);
    expect(shouldAutoCompose({ reason: 'ended', coverage: 0.3, composed: false, hasContent: true })).toBe(false);
    expect(shouldAutoCompose({ reason: 'leaving', coverage: 0.7, composed: false, hasContent: true })).toBe(false);
    expect(shouldAutoCompose({ reason: 'leaving', coverage: 0.9, composed: true, hasContent: true })).toBe(false);
    // A rewatch that caught something new rewrites the note, whatever the coverage.
    expect(shouldAutoCompose({ reason: 'leaving', coverage: 0.1, composed: true, stale: true, hasContent: true })).toBe(true);
    expect(shouldAutoCompose({ reason: 'ended', coverage: 0.1, composed: true, stale: true, hasContent: true })).toBe(true);
    expect(shouldAutoCompose({ reason: 'ended', coverage: 1, composed: true, stale: false, hasContent: true })).toBe(false);
    // ...but not over edits you made to the note yourself.
    expect(shouldAutoCompose({ reason: 'ended', coverage: 1, composed: true, stale: true, edited: true, hasContent: true })).toBe(false);
    expect(shouldAutoCompose({ reason: 'ended', coverage: 1, composed: false, hasContent: false })).toBe(false);
  });

  it('formats timestamps', () => {
    expect(formatTs(252)).toBe('04:12');
    expect(formatTs(3725)).toBe('1:02:05');
  });
});

// A fake 64x36 frame: a white slide whose "text" is dark pixels placed by a
// seed, the way real slide text is sparse ink on a plain background.
function slide(seed, lines = 3) {
  const sig = new Uint8Array(64 * 36).fill(245);
  for (let line = 0; line < lines; line++) {
    for (let x = 6; x < 50; x++) {
      if ((x * 7 + seed * 13 + line * 5) % 3 !== 0) sig[(6 + line * 7) * 64 + x] = 40;
    }
  }
  return sig;
}

describe('SceneTracker', () => {
  const run = (frames) => {
    const tr = new SceneTracker();
    return frames.filter(([sig, t]) => tr.feed(sig, t).capture).map(([, t]) => t);
  };

  it('keeps each new slide once it has settled, even when it is mostly white', () => {
    const frames = [];
    let t = 0;
    for (const seed of [1, 2, 3, 4]) for (let i = 0; i < 4; i++) frames.push([slide(seed), t++]);
    // Four different sparse-text slides: four pictures, each after it settled.
    expect(run(frames)).toEqual([1, 5, 9, 13]);
  });

  it('keeps every step of a slide building up (the sidecar merges them)', () => {
    const frames = [];
    let t = 0;
    for (const lines of [1, 2, 3]) for (let i = 0; i < 3; i++) frames.push([slide(1, lines), t++]);
    expect(run(frames)).toEqual([1, 4, 7]);
  });

  it('ignores a frame that flashes past without settling', () => {
    const flash = slide(9);
    const frames = [[slide(1), 0], [slide(1), 1], [slide(1), 2], [flash, 3], [slide(1), 4], [slide(1), 5]];
    expect(run(frames)).toEqual([1]);
  });

  it('does not re-take a slide that is still showing', () => {
    const frames = Array.from({ length: 20 }, (_, t) => [slide(1), t]);
    expect(run(frames)).toEqual([1]);
  });

  it('skips black and flat frames', () => {
    const tr = new SceneTracker();
    expect(tr.feed(new Uint8Array(64 * 36), 0)).toEqual({ capture: false });
  });

  it('takes a picture of a scene that never quite settles, now and then', () => {
    const tr = new SceneTracker({ maxGapSec: 10 });
    tr.feed(slide(1), 0);
    expect(tr.feed(slide(1), 1).capture).toBe(true);
    // Small constant motion (a lecturer moving) keeps it from settling.
    const wobble = (seed) => { const s = slide(2); s[seed] = 0; s[seed + 64] = 0; s[seed + 128] = 0; s[seed + 400] = 0; return s; };
    const caps = [];
    for (let t = 3; t < 30; t++) if (tr.feed(wobble(t * 3), t).capture) caps.push(t);
    expect(caps.length).toBeGreaterThanOrEqual(1);
  });

  it('keeps the frame you pause on, unless it is already kept', () => {
    const tr = new SceneTracker();
    expect(tr.offer(slide(5), 3).capture).toBe(true);
    expect(tr.offer(slide(5), 4).capture).toBe(false);
    expect(tr.feed(slide(5), 5).capture).toBe(false);
  });

  it('measures change as a share of pixels, so sparse text registers', () => {
    expect(changedFraction(slide(1), slide(1))).toBe(0);
    expect(changedFraction(slide(1), slide(2))).toBeGreaterThan(0.02);
  });
});

describe('signatures', () => {
  it('measures brightness and difference', () => {
    const rgba = new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 0, 255]);
    const { sig, brightness } = greySignature(rgba);
    expect(Array.from(sig)).toEqual([255, 0]);
    expect(brightness).toBe(127.5);
    expect(signatureDiff(sig, new Uint8Array([255, 10]))).toBe(5);
    expect(signatureDiff(sig, new Uint8Array(3))).toBe(255);
  });
});

describe('planLayout', () => {
  it('sits over a free column instead of squeezing the page (Udemy sidebar, YouTube default)', () => {
    expect(planLayout(1470, 1054)).toEqual({ width: 416, reserve: false });
    expect(planLayout(1920, 1200)).toEqual({ width: 460, reserve: false });
  });

  it('makes room when the video reaches the edge (YouTube theater mode)', () => {
    expect(planLayout(1470, 1277)).toEqual({ width: 440, reserve: true });
    expect(planLayout(1280, 1280)).toEqual({ width: 384, reserve: true });
  });

  it('never goes narrower than a readable panel on a small screen', () => {
    expect(planLayout(1000, 1000).width).toBe(340);
  });
});

describe('note provenance', () => {
  it('names the model the way people say it', () => {
    expect(engineName('gemini-3.5-flash-lite')).toBe('Gemini 3.5 Flash Lite');
    expect(engineName('gemini-3-flash-preview')).toBe('Gemini 3 Flash Preview');
    expect(engineName('openrouter/google/gemini-2.5-flash')).toBe('Gemini 2.5 Flash via OpenRouter');
    expect(engineName('claude-sonnet')).toBe('Claude Sonnet');
  });

  it('says how a note was made and what will improve it', () => {
    const lite = '---\ntitle: x\nengine: gemini-3.5-flash-lite\nslides_captured: 2\n---\n# x';
    expect(noteProvenance(lite)).toBe('Gemini 3.5 Flash Lite · 2 slides captured · a stronger model will rewrite it');
    const captions = '---\nengine: gemini-3.5-flash\nwritten_from: captions\nslides_captured: 0\n---\n';
    expect(noteProvenance(captions)).toBe('Gemini 3.5 Flash · from captions, no slides until you watch it with Margin');
    expect(noteProvenance('# no frontmatter')).toBe('');
  });
});

describe('a lecturer in a corner bubble', () => {
  // 64x36 grey signature: a static slide, and a "face" in the top-right corner
  // that changes every sample, as a talking head does.
  const W = 64, H = 36;
  function frame(slideSeed, faceSeed) {
    const sig = new Uint8Array(W * H).fill(235);
    for (let i = 0; i < 90; i++) sig[(slideSeed * 131 + i * 37) % (W * 20) + W * 4] = 40; // slide text
    for (let y = 2; y < 12; y++) for (let x = 52; x < 62; x++) sig[y * W + x] = (faceSeed * 53 + x * 7 + y * 11) % 200;
    return sig;
  }

  it('still takes each slide once the moving corner is learned', () => {
    const tracker = new SceneTracker();
    const kept = [];
    let t = 0;
    for (const slide of [1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3]) {
      if (tracker.feed(frame(slide, t), t).capture) kept.push(slide);
      t += 0.5;
    }
    expect(kept).toEqual([1, 2, 3]);
    const mask = tracker.moving();
    expect(mask[5 * W + 56]).toBe(1); // the face
    expect(mask[20 * W + 10]).toBe(0); // the slide
  });
});
