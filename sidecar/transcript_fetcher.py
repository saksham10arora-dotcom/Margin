from youtube_transcript_api import YouTubeTranscriptApi

# Ordered preference. English first so English videos keep their existing
# behaviour exactly; anything else is a fallback, not a downgrade.
PREFERRED = ("en", "en-US", "en-GB")


def fetch_transcript(video_id: str) -> dict:
    """Fetches cues for a YouTube video via youtube_transcript_api.

    Returns {"cues": [{"startSec", "text"}], "language_code": str, "language": str}.

    This runs server-side (not in the browser) because YouTube's timedtext API
    requires a proof-of-origin token when fetched directly from a page/content
    script -- confirmed by direct testing: every browser-side fetch to the
    caption track's baseUrl returns HTTP 200 with an empty body, regardless of
    credentials or format params. youtube_transcript_api works around this and
    was already proven reliable on this machine (used for the Stat110 vault
    notes pipeline).

    Language handling matters more than it looks. `api.fetch(video_id)` defaults
    to languages=("en",) and raises NoTranscriptFound on a video that only has,
    say, Hindi auto-captions -- which the panel then reported as "No transcript
    available for this video" even though YouTube was visibly showing captions.
    So: ask for English, and if the video does not have it, take whatever track
    the video does have and tell the caller which language it is, so the note
    prompt can ask for English notes off a non-English transcript.
    """
    api = YouTubeTranscriptApi()
    listing = api.list(video_id)

    try:
        transcript = listing.find_transcript(list(PREFERRED))
    except Exception:
        # Manually written captions beat auto-generated ones when both exist.
        available = list(listing)
        if not available:
            raise
        transcript = next((t for t in available if not t.is_generated), available[0])

    fetched = transcript.fetch()
    return {
        "cues": [{"startSec": s.start, "text": s.text} for s in fetched],
        "language_code": transcript.language_code,
        "language": transcript.language,
    }
