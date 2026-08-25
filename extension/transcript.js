// extension/transcript.js

/**
 * Parses a timestamp string in either "mm:ss" or "h:mm:ss" format to seconds.
 * Pure function — no DOM access.
 *
 * @param {string} text - The timestamp string (e.g. "12:34" or "1:15:30")
 * @returns {number} Total seconds
 */
export function parseTimestampToSeconds(text) {
  const parts = text.trim().split(':').map(Number);
  if (parts.length === 2) {
    // mm:ss format
    const [min, sec] = parts;
    return min * 60 + sec;
  } else if (parts.length === 3) {
    // h:mm:ss format
    const [hours, min, sec] = parts;
    return hours * 3600 + min * 60 + sec;
  }
  // Fallback: return 0 if unexpected format
  return 0;
}

/**
 * Groups caption cues into time-bounded batches for sending to the sidecar.
 * Pure function — no DOM access — so it's fully unit-testable.
 */
export function batchTranscript(cues, maxDurationSec) {
  if (cues.length === 0) return [];

  const batches = [];
  let currentBatch = { startSec: cues[0].startSec, texts: [cues[0].text] };

  for (let i = 1; i < cues.length; i++) {
    const cue = cues[i];
    const elapsed = cue.startSec - currentBatch.startSec;
    if (elapsed >= maxDurationSec) {
      batches.push({
        startSec: currentBatch.startSec,
        endSec: cue.startSec,
        text: currentBatch.texts.join(' '),
      });
      currentBatch = { startSec: cue.startSec, texts: [cue.text] };
    } else {
      currentBatch.texts.push(cue.text);
    }
  }

  batches.push({
    startSec: currentBatch.startSec,
    endSec: currentBatch.startSec, // caller should also close this out using video.currentTime
    text: currentBatch.texts.join(' '),
  });

  return batches;
}

const SIDECAR_URL = 'http://localhost:8765';

/**
 * Fetches {startSec, text} cues for a video from the sidecar's /transcript
 * endpoint. Two prior in-browser approaches were tried and abandoned:
 * 1. DOM-scraping the transcript panel — its trigger buttons are
 *    conditionally hidden behind other UI state, and even once opened, the
 *    panel inconsistently rendered a real segment list vs. an empty stub
 *    across videos and page loads, in ways that couldn't be reliably driven.
 * 2. Fetching YouTube's caption-track `timedtext` API directly from the page
 *    — confirmed by direct testing that every such request now returns
 *    HTTP 200 with an empty body (YouTube added a proof-of-origin token
 *    requirement to that endpoint that a plain fetch can't satisfy).
 * The sidecar instead fetches server-side via `youtube_transcript_api`
 * (Python), which handles this correctly and was already proven reliable on
 * this machine.
 */
export async function scrapeTranscript(videoId) {
  const response = await fetch(`${SIDECAR_URL}/transcript?video_id=${encodeURIComponent(videoId)}`);
  if (!response.ok) return { cues: [], language: 'English' };
  const data = await response.json();
  // A video may only have captions in one non-English language. The sidecar
  // returns whichever track it found, and the note prompt needs to know which.
  return { cues: data.cues || [], language: data.language || 'English' };
}

/**
 * Formats a timestamp (in seconds) into a markdown jump-link to the video
 * at that timestamp. Pure function — no DOM access.
 *
 * @param {number} currentSec - The timestamp in seconds
 * @param {string} videoUrl - The video URL (e.g., youtube.com/watch?v=abc)
 * @returns {string} Markdown link: [HH:MM:SS](url&t=Ns)
 */
export function formatTimestampLink(currentSec, videoUrl) {
  const h = Math.floor(currentSec / 3600).toString().padStart(2, '0');
  const m = Math.floor((currentSec % 3600) / 60).toString().padStart(2, '0');
  const s = Math.floor(currentSec % 60).toString().padStart(2, '0');
  return `[${h}:${m}:${s}](${videoUrl}&t=${Math.floor(currentSec)}s)`;
}
