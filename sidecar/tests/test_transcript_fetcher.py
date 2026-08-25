from unittest.mock import MagicMock, patch

import pytest

from sidecar.transcript_fetcher import fetch_transcript


def _track(code, name, generated=True, snippets=()):
    t = MagicMock()
    t.language_code = code
    t.language = name
    t.is_generated = generated
    t.fetch.return_value = list(snippets)
    return t


def _listing(tracks, english=None):
    listing = MagicMock()
    listing.__iter__ = lambda self: iter(tracks)
    if english is None:
        listing.find_transcript.side_effect = Exception("no english track")
    else:
        listing.find_transcript.return_value = english
    return listing


@patch("sidecar.transcript_fetcher.YouTubeTranscriptApi")
def test_prefers_english_and_converts_snippets_to_cues(mock_api_cls):
    en = _track("en", "English", generated=False, snippets=[
        MagicMock(start=0.5, text="Hello"),
        MagicMock(start=2.0, text="World"),
    ])
    mock_api_cls.return_value.list.return_value = _listing([en], english=en)

    result = fetch_transcript("abc123")

    assert result["cues"] == [
        {"startSec": 0.5, "text": "Hello"},
        {"startSec": 2.0, "text": "World"},
    ]
    assert result["language_code"] == "en"


@patch("sidecar.transcript_fetcher.YouTubeTranscriptApi")
def test_falls_back_to_the_only_available_language(mock_api_cls):
    # The real regression: a Hindi-only video reported "No transcript available"
    # because api.fetch() defaults to English and raises NoTranscriptFound.
    hi = _track("hi", "Hindi (auto-generated)", snippets=[MagicMock(start=0.1, text="नमस्ते")])
    mock_api_cls.return_value.list.return_value = _listing([hi])

    result = fetch_transcript("xyz")

    assert result["cues"] == [{"startSec": 0.1, "text": "नमस्ते"}]
    assert result["language_code"] == "hi"
    assert result["language"] == "Hindi (auto-generated)"


@patch("sidecar.transcript_fetcher.YouTubeTranscriptApi")
def test_prefers_a_manual_track_over_an_auto_generated_one(mock_api_cls):
    auto = _track("de", "German (auto-generated)", generated=True, snippets=[MagicMock(start=0.0, text="auto")])
    manual = _track("fr", "French", generated=False, snippets=[MagicMock(start=0.0, text="manuel")])
    mock_api_cls.return_value.list.return_value = _listing([auto, manual])

    assert fetch_transcript("v")["language_code"] == "fr"


@patch("sidecar.transcript_fetcher.YouTubeTranscriptApi")
def test_raises_when_the_video_has_no_tracks_at_all(mock_api_cls):
    mock_api_cls.return_value.list.return_value = _listing([])

    with pytest.raises(Exception):
        fetch_transcript("v")
