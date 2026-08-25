from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
import sidecar.main as main_module
import sidecar.config as config_module
from sidecar.main import app, get_vault_path

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("sidecar.main.fetch_transcript")
def test_transcript_returns_cues_and_the_track_language(mock_fetch):
    # The language rides along so the note prompt can ask for English notes
    # off a transcript that is not in English.
    mock_fetch.return_value = {
        "cues": [
            {"startSec": 0.5, "text": "Hello"},
            {"startSec": 2.0, "text": "World"},
        ],
        "language_code": "en",
        "language": "English",
    }
    response = client.get("/transcript", params={"video_id": "abc123"})
    assert response.status_code == 200
    assert response.json() == {
        "cues": [
            {"startSec": 0.5, "text": "Hello"},
            {"startSec": 2.0, "text": "World"},
        ],
        "language_code": "en",
        "language": "English",
    }
    mock_fetch.assert_called_once_with("abc123")


@patch("sidecar.main.fetch_transcript")
def test_transcript_returns_404_when_unavailable(mock_fetch):
    mock_fetch.side_effect = RuntimeError("no captions for this video")
    response = client.get("/transcript", params={"video_id": "abc123"})
    assert response.status_code == 404
    assert "no captions for this video" in response.json()["detail"]


def test_documents_lists_vault_notes(tmp_path, monkeypatch):
    (tmp_path / "lec1.md").write_text(
        '---\ntitle: "Lecture 1"\nsource: https://youtube.com/watch?v=a\ncreated: "2026-07-27"\n---\n\n# Lecture 1\n'
    )
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents")
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == [{
        "filename": "lec1.md", "title": "Lecture 1",
        "source": "https://youtube.com/watch?v=a", "created": "2026-07-27",
    }]


def test_documents_search_filters_by_query(tmp_path):
    (tmp_path / "lec1.md").write_text(
        '---\ntitle: "Linear Algebra"\nsource: url\ncreated: "2026-07-27"\n---\n\n# x\n'
    )
    (tmp_path / "lec2.md").write_text(
        '---\ntitle: "Bond Math"\nsource: url\ncreated: "2026-07-15"\n---\n\n# x\n'
    )
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents/search?q=linear")
    app.dependency_overrides.clear()
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Linear Algebra"


def test_document_content_returns_raw_markdown(tmp_path):
    (tmp_path / "lec1.md").write_text("---\ntitle: Lecture 1\n---\n\n# Lecture 1\n\nBody text.")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents/content", params={"filename": "lec1.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"content": "---\ntitle: Lecture 1\n---\n\n# Lecture 1\n\nBody text."}


def test_document_content_supports_nested_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "lec1.md").write_text("nested content")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents/content", params={"filename": "sub/lec1.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"content": "nested content"}


def test_document_content_404_when_missing(tmp_path):
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents/content", params={"filename": "missing.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 404


def test_document_content_rejects_path_traversal(tmp_path):
    secret = tmp_path.parent / "secret.md"
    secret.write_text("top secret")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents/content", params={"filename": "../secret.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 400


def test_document_content_rejects_non_markdown(tmp_path):
    (tmp_path / "notes.txt").write_text("plain text")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.get("/documents/content", params={"filename": "notes.txt"})
    app.dependency_overrides.clear()
    assert response.status_code == 400


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_creates_new_note_on_first_chunk(mock_call_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    mock_call_haiku.return_value = "## Vectors [00:00:20](url)\n\nVectors are ordered lists."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json={
        "video_id": "abc123",
        "video_title": "Test Lecture",
        "video_url": "https://youtube.com/watch?v=abc123",
        "author": "Test Author",
        "duration_sec": 600,
        "transcript_chunk": "vectors are ordered lists of numbers",
        "start_ts": "00:00:00",
        "end_ts": "00:01:00",
        "is_first_chunk": True,
    })
    app.dependency_overrides.clear()

    assert response.status_code == 200
    note_path = tmp_path / "test-lecture.md"
    assert note_path.exists()
    content = note_path.read_text()
    assert "# Test Lecture" in content
    assert "## Vectors [00:00:20](url)" in content
    assert response.json()["rendered_section"] == "## Vectors [00:00:20](url)\n\nVectors are ordered lists."
    assert response.json()["filename"] == "test-lecture.md"


@patch("sidecar.engine_dispatch.call_haiku", side_effect=FileNotFoundError("claude CLI not found on PATH"))
def test_note_chunk_haiku_engine_502s_when_claude_cli_missing(mock_call_haiku, tmp_path, monkeypatch):
    """A fresh clone without the `claude` CLI installed hits this on its very
    first request, since haiku is the default engine -- must degrade to a
    clean 502, not an unhandled 500 with a raw traceback."""
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json={
        "video_id": "abc123",
        "video_title": "Test Lecture",
        "video_url": "https://youtube.com/watch?v=abc123",
        "author": "Test Author",
        "duration_sec": 600,
        "transcript_chunk": "vectors are ordered lists of numbers",
        "start_ts": "00:00:00",
        "end_ts": "00:01:00",
        "is_first_chunk": True,
    })
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "claude CLI not found on PATH" in response.json()["detail"]


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_appends_to_existing_note_on_later_chunk(mock_call_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text('---\ntitle: "Test Lecture"\n---\n\n# Test Lecture\n\n## Intro [00:00:00](url)\n\nFirst bit.')
    mock_call_haiku.return_value = "## Eigenvalues [00:05:00](url)\n\nCharacteristic roots."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json={
        "video_id": "abc123",
        "video_title": "Test Lecture",
        "video_url": "https://youtube.com/watch?v=abc123",
        "author": "Test Author",
        "duration_sec": 600,
        "transcript_chunk": "eigenvalues are characteristic roots",
        "start_ts": "00:05:00",
        "end_ts": "00:06:00",
        "is_first_chunk": False,
    })
    app.dependency_overrides.clear()

    assert response.status_code == 200
    content = note_path.read_text()
    assert "## Intro [00:00:00](url)" in content  # original untouched
    assert "## Eigenvalues [00:05:00](url)" in content  # new section appended


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_skips_duplicate_section(mock_call_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text(
        '---\ntitle: "Test Lecture"\n---\n\n# Test Lecture\n\n'
        '## Vectors [00:00:20](url)\n\nVectors are ordered lists of numbers.'
    )
    mock_call_haiku.return_value = "## Vectors [00:00:25](url)\n\nVectors are ordered lists of numbers here."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json={
        "video_id": "abc123", "video_title": "Test Lecture",
        "video_url": "https://youtube.com/watch?v=abc123", "author": "Test Author",
        "duration_sec": 600, "transcript_chunk": "vectors again",
        "start_ts": "00:00:25", "end_ts": "00:01:25", "is_first_chunk": False,
    })
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["rendered_section"] is None
    content = note_path.read_text()
    assert content.count("## Vectors") == 1  # not duplicated


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_matches_by_url_even_when_title_differs(mock_call_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    # Existing note has a DIFFERENT title but the SAME source URL as the
    # incoming request. URL matching should find this note (and append to
    # it) instead of creating a new note under a filename derived from the
    # request's title.
    existing_note = tmp_path / "old-title.md"
    existing_note.write_text(
        '---\ntitle: "Old Title"\nsource: https://youtube.com/watch?v=shared\n---\n\n'
        '# Old Title\n\n## Intro [00:00:00](url)\n\nFirst bit.'
    )
    mock_call_haiku.return_value = "## New Part [00:05:00](url)\n\nMore content."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json={
        "video_id": "shared", "video_title": "New Title",
        "video_url": "https://youtube.com/watch?v=shared", "author": "Test Author",
        "duration_sec": 600, "transcript_chunk": "more content here",
        "start_ts": "00:05:00", "end_ts": "00:06:00", "is_first_chunk": False,
    })
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["note_path"] == str(existing_note)
    assert not (tmp_path / "new-title.md").exists()  # no new note created
    content = existing_note.read_text()
    assert "## Intro [00:00:00](url)" in content  # original untouched
    assert "## New Part [00:05:00](url)" in content  # appended to the URL-matched note


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_url_match_wins_over_unrelated_title_collision(mock_call_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    # Two DIFFERENT documents already exist in the vault:
    #   1. doc_url_match.md -- same source URL as the incoming request, but a
    #      different title (so its filename is NOT what the request's title
    #      would sanitize to).
    #   2. lecture-1.md -- an UNRELATED video whose filename happens to
    #      collide with sanitize_filename(req.video_title) + ".md", but whose
    #      source URL is different from the request's.
    # A genuine two-pass lookup (URL first, filename fallback only if no URL
    # match exists anywhere) must append to doc_url_match.md. A buggy
    # single-pass OR loop can instead match the title-collision document if
    # it's iterated before the URL match.
    url_match_note = tmp_path / "doc_url_match.md"
    url_match_note.write_text(
        '---\ntitle: "Some Other Title"\nsource: https://youtube.com/watch?v=realvideo\n---\n\n'
        '# Some Other Title\n\n## Intro [00:00:00](url)\n\nReal video content.'
    )
    collision_note = tmp_path / "lecture-1.md"
    collision_note.write_text(
        '---\ntitle: "Lecture 1"\nsource: https://youtube.com/watch?v=unrelated\n---\n\n'
        '# Lecture 1\n\n## Intro [00:00:00](url)\n\nUnrelated video content.'
    )
    mock_call_haiku.return_value = "## New Part [00:05:00](url)\n\nMore content."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json={
        "video_id": "realvideo", "video_title": "Lecture 1",
        "video_url": "https://youtube.com/watch?v=realvideo", "author": "Test Author",
        "duration_sec": 600, "transcript_chunk": "more content here",
        "start_ts": "00:05:00", "end_ts": "00:06:00", "is_first_chunk": False,
    })
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["note_path"] == str(url_match_note)

    url_match_content = url_match_note.read_text()
    assert "## Intro [00:00:00](url)" in url_match_content  # original untouched
    assert "## New Part [00:05:00](url)" in url_match_content  # appended here

    collision_content = collision_note.read_text()
    assert collision_content == (
        '---\ntitle: "Lecture 1"\nsource: https://youtube.com/watch?v=unrelated\n---\n\n'
        '# Lecture 1\n\n## Intro [00:00:00](url)\n\nUnrelated video content.'
    )  # unrelated title-collision doc must be completely unchanged


def _note_chunk_payload(**overrides):
    payload = {
        "video_id": "abc123", "video_title": "Test Lecture",
        "video_url": "https://youtube.com/watch?v=abc123", "author": "Test Author",
        "duration_sec": 600, "transcript_chunk": "vectors are ordered lists",
        "start_ts": "00:00:00", "end_ts": "00:01:00", "is_first_chunk": True,
    }
    payload.update(overrides)
    return payload


@patch("sidecar.engine_dispatch.load_gemini_api_keys", return_value=["fake-key"])
@patch("sidecar.engine_dispatch.call_gemini")
@patch("sidecar.main.extract_candidate_frames")
def test_note_chunk_gemini_engine_never_writes_frame_images_to_disk(
    mock_extract, mock_call_gemini, mock_key, tmp_path, monkeypatch,
):
    # Screenshots are paraphrased into prose with an inline timestamp marker
    # (<!-- screenshot: HH:MM:SS -->), matching the batch pipeline's exact
    # convention -- never embedded, so nothing should ever touch disk here.
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "gemini")
    mock_extract.return_value = [
        (20.0, b"\xff\xd8used-frame"),
        (40.0, b"\xff\xd8dropped-frame"),
    ]
    mock_call_gemini.return_value = (
        "## Vectors [00:00:20](url)\n\n"
        "The board shows the basis vectors explicitly. <!-- screenshot: 00:00:20 -->"
    )
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json=_note_chunk_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 200
    mock_extract.assert_called_once_with(
        "https://youtube.com/watch?v=abc123", "00:00:00", "00:01:00",
    )
    assert not (tmp_path / "hover-notes-images").exists()


@patch("sidecar.engine_dispatch.load_gemini_api_keys", return_value=["fake-key"])
@patch("sidecar.engine_dispatch.call_gemini")
@patch("sidecar.main.extract_candidate_frames", side_effect=RuntimeError("ffmpeg not found"))
def test_note_chunk_gemini_engine_degrades_to_text_only_on_frame_failure(
    mock_extract, mock_call_gemini, mock_key, tmp_path, monkeypatch,
):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "gemini")
    mock_call_gemini.return_value = "## Vectors [00:00:20](url)\n\nText-only notes."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json=_note_chunk_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 200
    mock_call_gemini.assert_called_once()
    assert mock_call_gemini.call_args.kwargs["frames"] == []


@patch("sidecar.engine_dispatch.load_gemini_api_keys", return_value=[])
def test_note_chunk_gemini_engine_502s_without_api_key(mock_key, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "gemini")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json=_note_chunk_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "GEMINI_API_KEY" in response.json()["detail"]


@patch("sidecar.main.load_gemini_api_keys", return_value=["fake-key"])
@patch("sidecar.engine_dispatch.call_gemini", side_effect=httpx.ReadTimeout("The read operation timed out"))
@patch("sidecar.main.extract_candidate_frames", return_value=[])
def test_note_chunk_gemini_engine_502s_on_httpx_timeout(
    mock_extract, mock_call_gemini, mock_key, tmp_path, monkeypatch,
):
    # A Gemini call that hangs past its own timeout raises httpx.ReadTimeout,
    # not RuntimeError/subprocess.TimeoutExpired — must still degrade to a
    # clean 502 instead of leaking through as an unhandled 500.
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "gemini")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json=_note_chunk_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 502


def _finalizable_note_text():
    return (
        '---\ntitle: "Test Lecture"\nsource: https://www.youtube.com/watch?v=abc12345678\n'
        'duration_sec: 120\n---\n\n'
        '# Test Lecture\n\n## Intro [00:00:00](url)\n\nSome content.\n'
    )


@patch("sidecar.main.load_gemini_api_keys", return_value=["fake-key"])
@patch("sidecar.main.call_regenerate")
@patch("sidecar.main.extract_candidate_frames", return_value=[])
@patch("sidecar.main.fetch_transcript")
def test_finalize_note_replaces_body_with_regenerated_content(
    mock_fetch, mock_extract, mock_regenerate, mock_key, tmp_path,
):
    mock_fetch.return_value = [{"startSec": 0.0, "text": "the professor discusses vectors"}]
    mock_regenerate.return_value = (
        "> [!abstract] TL;DR\n> A summary.\n\n"
        "## Vectors [00:00:00](url)\n\nReorganized content.\n\n"
        "> [!note] Key terms\n> - **A**: def a"
    )
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text(_finalizable_note_text())
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "test-lecture.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    content = note_path.read_text()
    assert "[!abstract] TL;DR" in content
    assert "A summary." in content
    assert "[!note] Key terms" in content
    assert "Reorganized content." in content
    assert "Some content." not in content  # rough live body was discarded, not kept
    mock_fetch.assert_called_once_with("abc12345678")  # video ID parsed from frontmatter source


@patch("sidecar.main.load_gemini_api_keys", return_value=["fake-key"])
@patch("sidecar.main.call_regenerate")
def test_finalize_note_is_idempotent_on_repeat_call(mock_regenerate, mock_key, tmp_path):
    already_finalized = (
        '---\ntitle: "Test Lecture"\n---\n\n# Test Lecture\n\n'
        '> [!abstract] TL;DR\n> Already done.\n\n## Intro [00:00:00](url)\n\nSome content.\n\n'
        '---\n\n> [!note] Key terms\n> - **A**: def a\n'
    )
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text(already_finalized)
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "test-lecture.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert note_path.read_text() == already_finalized  # unchanged
    mock_regenerate.assert_not_called()  # no wasted API call


def test_finalize_note_400s_when_note_has_no_sections(tmp_path):
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text('---\ntitle: "Test Lecture"\n---\n\n# Test Lecture\n')
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "test-lecture.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "no sections" in response.json()["detail"]


def test_finalize_note_400s_when_frontmatter_missing_source(tmp_path):
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text('---\ntitle: "Test Lecture"\n---\n\n# Test Lecture\n\n## Intro\n\nContent.\n')
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "test-lecture.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "frontmatter" in response.json()["detail"]


def test_finalize_note_404s_when_missing(tmp_path):
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.post("/finalize-note", json={"filename": "missing.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 404


def test_finalize_note_rejects_path_traversal(tmp_path):
    secret = tmp_path.parent / "secret.md"
    secret.write_text("top secret")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "../secret.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 400


@patch("sidecar.main.load_gemini_api_keys", return_value=[])
def test_finalize_note_502s_without_api_key(mock_key, tmp_path):
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text(_finalizable_note_text())
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "test-lecture.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "GEMINI_API_KEY" in response.json()["detail"]


@patch("sidecar.main.load_gemini_api_keys", return_value=["fake-key"])
@patch("sidecar.main.fetch_transcript", side_effect=RuntimeError("no captions"))
def test_finalize_note_502s_when_transcript_refetch_fails(mock_fetch, mock_key, tmp_path):
    note_path = tmp_path / "test-lecture.md"
    note_path.write_text(_finalizable_note_text())
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/finalize-note", json={"filename": "test-lecture.md"})
    app.dependency_overrides.clear()

    assert response.status_code == 502
    assert "no captions" in response.json()["detail"]


NOTE_FOR_CARDS = (
    '---\ntitle: "Linear Algebra 1"\nsource: https://youtube.com/watch?v=a\n---\n\n'
    "# Linear Algebra 1\n\n## Vectors [00:01:00](url)\n\n* **Vector** is an ordered list of numbers.\n"
)


@patch("sidecar.main.engine_complete")
def test_export_flashcards_returns_parsed_cards_and_anki_tsv(mock_complete, tmp_path):
    mock_complete.return_value = (
        "Q: What is a vector?\nA: An ordered list of numbers.\n"
        "---\nQ: What is a scalar?\nA: A single number."
    )
    (tmp_path / "lec1.md").write_text(NOTE_FOR_CARDS)
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.post("/export/flashcards", json={"filename": "lec1.md", "tags": "stat110"})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["cards"][0]["front"] == "What is a vector?"
    assert payload["tsv"].startswith("#separator:tab")
    assert "stat110" in payload["tsv"]


@patch("sidecar.main.engine_complete")
def test_export_flashcards_sends_the_note_body_to_the_engine(mock_complete, tmp_path):
    mock_complete.return_value = "Q: q\nA: a"
    (tmp_path / "lec1.md").write_text(NOTE_FOR_CARDS)
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    client.post("/export/flashcards", json={"filename": "lec1.md"})
    app.dependency_overrides.clear()

    prompt = mock_complete.call_args.args[0]
    assert "ordered list of numbers" in prompt
    assert "Linear Algebra 1" in prompt
    assert "source: https://" not in prompt  # frontmatter stripped, not card material


@patch("sidecar.main.engine_complete", return_value="I cannot make cards from this.")
def test_export_flashcards_422s_when_the_model_returns_nothing_usable(mock_complete, tmp_path):
    (tmp_path / "lec1.md").write_text(NOTE_FOR_CARDS)
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.post("/export/flashcards", json={"filename": "lec1.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 422


@patch("sidecar.main.engine_complete", side_effect=RuntimeError("engine exploded"))
def test_export_flashcards_502s_on_engine_failure(mock_complete, tmp_path):
    (tmp_path / "lec1.md").write_text(NOTE_FOR_CARDS)
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.post("/export/flashcards", json={"filename": "lec1.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 502
    assert "engine exploded" in response.json()["detail"]


def test_export_flashcards_rejects_path_traversal(tmp_path):
    secret = tmp_path.parent / "secret.md"
    secret.write_text("top secret")
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.post("/export/flashcards", json={"filename": "../secret.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 400


def test_export_flashcards_404s_when_the_note_is_missing(tmp_path):
    app.dependency_overrides[get_vault_path] = lambda: tmp_path
    response = client.post("/export/flashcards", json={"filename": "nope.md"})
    app.dependency_overrides.clear()
    assert response.status_code == 404


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_autolinks_concepts_against_existing_vault_notes(mock_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    monkeypatch.setattr(main_module, "AUTOLINK", True)
    (tmp_path / "eigen.md").write_text('---\ntitle: "Eigenvalues"\n---\n\n# Eigenvalues\n')
    mock_haiku.return_value = "## Recap [00:05:00](url)\n\nWe revisit eigenvalues today."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json=_note_chunk_payload())
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "[[Eigenvalues|eigenvalues]]" in response.json()["rendered_section"]


@patch("sidecar.engine_dispatch.call_haiku")
def test_note_chunk_writes_plain_prose_when_autolink_is_disabled(mock_haiku, tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "NOTE_ENGINE", "haiku")
    monkeypatch.setattr(main_module, "AUTOLINK", False)
    (tmp_path / "eigen.md").write_text('---\ntitle: "Eigenvalues"\n---\n\n# Eigenvalues\n')
    mock_haiku.return_value = "## Recap [00:05:00](url)\n\nWe revisit eigenvalues today."
    app.dependency_overrides[get_vault_path] = lambda: tmp_path

    response = client.post("/note-chunk", json=_note_chunk_payload())
    app.dependency_overrides.clear()

    assert "[[" not in response.json()["rendered_section"]


# --- non-English transcripts ---------------------------------------------

def test_language_directive_is_absent_for_english():
    from sidecar.main import _with_language_directive

    assert _with_language_directive("some words", "English") == "some words"


def test_language_directive_asks_for_english_notes_on_a_hindi_transcript():
    from sidecar.main import _with_language_directive

    out = _with_language_directive("नमस्ते", "Hindi (auto-generated)")
    assert out.endswith("नमस्ते")
    assert "Hindi (auto-generated)" in out
    assert "Write the notes themselves in English" in out
