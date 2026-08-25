// extension/transcript.test.js
import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  batchTranscript,
  parseTimestampToSeconds,
  formatTimestampLink,
  scrapeTranscript,
} from './transcript.js';

describe('batchTranscript', () => {
  it('groups cues into a single batch when total duration is under the max', () => {
    const cues = [
      { startSec: 0, text: 'Hello and welcome.' },
      { startSec: 5, text: 'Today we cover vectors.' },
    ];
    const batches = batchTranscript(cues, 60);
    expect(batches).toHaveLength(1);
    expect(batches[0].startSec).toBe(0);
    expect(batches[0].text).toBe('Hello and welcome. Today we cover vectors.');
  });

  it('splits into multiple batches once maxDurationSec is exceeded', () => {
    const cues = [
      { startSec: 0, text: 'First minute content.' },
      { startSec: 65, text: 'Second minute content.' },
      { startSec: 130, text: 'Third minute content.' },
    ];
    const batches = batchTranscript(cues, 60);
    expect(batches).toHaveLength(3);
    expect(batches[0].text).toBe('First minute content.');
    expect(batches[1].text).toBe('Second minute content.');
    expect(batches[2].text).toBe('Third minute content.');
  });

  it('sets endSec to the startSec of the first cue in the next batch', () => {
    const cues = [
      { startSec: 0, text: 'a' },
      { startSec: 10, text: 'b' },
      { startSec: 65, text: 'c' },
    ];
    const batches = batchTranscript(cues, 60);
    expect(batches[0].endSec).toBe(65);
  });

  it('returns an empty array for no cues', () => {
    expect(batchTranscript([], 60)).toEqual([]);
  });

  it('splits into a new batch when cue arrives at EXACTLY maxDurationSec', () => {
    const cues = [
      { startSec: 0, text: 'First cue' },
      { startSec: 60, text: 'Exact boundary cue' },
    ];
    const batches = batchTranscript(cues, 60);
    expect(batches).toHaveLength(2);
    expect(batches[0].text).toBe('First cue');
    expect(batches[0].endSec).toBe(60); // endSec should be startSec of next cue
    expect(batches[1].text).toBe('Exact boundary cue');
  });

  it('sets endSec to startSec for the final batch (single cue)', () => {
    const cues = [{ startSec: 5, text: 'Only cue' }];
    const batches = batchTranscript(cues, 60);
    expect(batches).toHaveLength(1);
    expect(batches[0].startSec).toBe(5);
    expect(batches[0].endSec).toBe(5); // final batch: endSec === startSec
    expect(batches[0].text).toBe('Only cue');
  });
});

describe('parseTimestampToSeconds', () => {
  it('parses mm:ss format correctly', () => {
    expect(parseTimestampToSeconds('12:34')).toBe(754); // 12*60 + 34
  });

  it('parses h:mm:ss format correctly', () => {
    expect(parseTimestampToSeconds('1:15:30')).toBe(4530); // 1*3600 + 15*60 + 30
  });

  it('handles leading/trailing whitespace', () => {
    expect(parseTimestampToSeconds('  5:45  ')).toBe(345); // 5*60 + 45
  });

  it('handles multi-digit hours in h:mm:ss format', () => {
    expect(parseTimestampToSeconds('12:34:56')).toBe(45296); // 12*3600 + 34*60 + 56
  });
});

describe('formatTimestampLink', () => {
  it('formats a sub-minute timestamp correctly', () => {
    const link = formatTimestampLink(45, 'https://youtube.com/watch?v=abc');
    expect(link).toBe('[00:00:45](https://youtube.com/watch?v=abc&t=45s)');
  });

  it('formats a multi-minute timestamp correctly', () => {
    const link = formatTimestampLink(125, 'https://youtube.com/watch?v=abc');
    expect(link).toBe('[00:02:05](https://youtube.com/watch?v=abc&t=125s)');
  });

  it('formats an hour-plus timestamp correctly', () => {
    const link = formatTimestampLink(4830, 'https://youtube.com/watch?v=abc');
    expect(link).toBe('[01:20:30](https://youtube.com/watch?v=abc&t=4830s)');
  });
});

describe('scrapeTranscript', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches cues from the sidecar /transcript endpoint for the given video id', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        cues: [
          { startSec: 0.5, text: 'Hello' },
          { startSec: 2.0, text: 'World' },
        ],
        language: 'English',
      }),
    });
    vi.stubGlobal('fetch', mockFetch);

    const result = await scrapeTranscript('abc123');

    expect(result).toEqual({
      cues: [
        { startSec: 0.5, text: 'Hello' },
        { startSec: 2.0, text: 'World' },
      ],
      language: 'English',
    });
    expect(mockFetch).toHaveBeenCalledWith('http://localhost:8765/transcript?video_id=abc123');
  });

  it('passes through the track language for a non-English video', async () => {
    // A Hindi-only video used to surface as "No transcript available"; the
    // language now rides along so the notes can still be written in English.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          cues: [{ startSec: 0.1, text: 'नमस्ते' }],
          language: 'Hindi (auto-generated)',
        }),
      }),
    );

    const result = await scrapeTranscript('xyz');

    expect(result.language).toBe('Hindi (auto-generated)');
    expect(result.cues).toHaveLength(1);
  });

  it('returns no cues when the sidecar responds with a non-ok status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    expect(await scrapeTranscript('abc123')).toEqual({ cues: [], language: 'English' });
  });

  it('returns no cues when the response has no cues field', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
    expect(await scrapeTranscript('abc123')).toEqual({ cues: [], language: 'English' });
  });

  it('URL-encodes the video id', async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ cues: [] }) });
    vi.stubGlobal('fetch', mockFetch);

    await scrapeTranscript('abc 123&x');

    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8765/transcript?video_id=abc%20123%26x',
    );
  });
});
