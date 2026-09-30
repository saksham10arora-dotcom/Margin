import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AudioRecorder } from './sensors.js';

// A video whose sound is attached a moment after its stream is asked for, the
// way hls.js and YouTube's player load it: captureStream() starts with no
// audio track and gains one. Margin used to give up at once and show
// "audio blocked" for the rest of the lecture.
function lateAudioVideo() {
  const video = new EventTarget();
  Object.assign(video, { paused: false, seeking: false, currentTime: 12, playbackRate: 1 });
  const stream = new EventTarget();
  stream.tracks = [];
  stream.getAudioTracks = () => stream.tracks;
  video.captureStream = () => stream;
  const attach = () => {
    const track = { kind: 'audio', readyState: 'live' };
    stream.tracks.push(track);
    const ev = new Event('addtrack');
    ev.track = track;
    stream.dispatchEvent(ev);
  };
  return { video, attach };
}

describe('AudioRecorder', () => {
  let started;
  beforeEach(() => {
    vi.useFakeTimers();
    started = 0;
    globalThis.MediaStream = class { constructor(tracks) { this.tracks = tracks; } };
    globalThis.MediaRecorder = class {
      static isTypeSupported() { return true; }
      constructor() { started++; }
      start() {}
      stop() { this.onstop?.(); }
    };
  });
  afterEach(() => vi.useRealTimers());

  it('waits for sound that arrives after the stream is asked for', () => {
    const { video, attach } = lateAudioVideo();
    const states = [];
    const rec = new AudioRecorder(video, { onSegment: () => {}, onState: (s) => states.push(s) });
    expect(() => rec.start()).not.toThrow();
    expect(started).toBe(0);
    attach();
    expect(started).toBe(1);
    expect(states).toEqual(['listening']);
    rec.stop();
  });

  it('says so when a playing video has no sound for a while', () => {
    const { video } = lateAudioVideo();
    const states = [];
    const rec = new AudioRecorder(video, { onSegment: () => {}, onState: (s) => states.push(s) });
    rec.start();
    vi.advanceTimersByTime(15000);
    expect(states).toEqual(['no-audio']);
    rec.stop();
  });
});
