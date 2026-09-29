"""Matching a lecture to the course repo's notebook, on the real layout of
ed-donner/llm_engineering."""
import json

from sidecar import repo_code as R

PATHS = [
    "guides/01_intro.ipynb", "setup/diagnostics.ipynb", "week1/day1.ipynb", "week1/day2.ipynb",
    "week1/day4.ipynb", "week1/solutions/day2 SOLUTION.ipynb", "week1/week1 EXERCISE.ipynb",
    "week2/day1.ipynb", "week7/day3 and 4.ipynb",
    "week1/community-contributions/day2-someone.ipynb",
]
REPO = [{"title": "Github link", "url": "https://github.com/ed-donner/llm_engineering"}]


def meta(section, title):
    return {"section_title": section, "lecture_title": title, "course_links": REPO}


def test_week_and_day_find_the_notebook():
    m = meta("Week 1 - Build Your First LLM Product", "Day 2 - Chat Completions API: HTTP Endpoints vs OpenAI Python Client")
    assert R.match_notebook(PATHS, m) == "week1/day2.ipynb"
    assert R.match_notebook(PATHS, meta("Week 7 - Fine-tune", "Day 4 - Training")) == "week7/day3 and 4.ipynb"


def test_no_week_or_day_means_no_notebook_rather_than_a_guess():
    assert R.match_notebook(PATHS, meta("Week 1", "Your Path to Becoming a Proficient AI Engineer")) is None
    assert R.match_notebook(PATHS, meta("Introduction", "Day 2 - Something")) is None
    assert R.match_notebook(PATHS, meta("Week 1", "Day 3 - No such notebook")) is None


def test_the_repo_comes_from_the_resources():
    assert R.course_repo({"resources": [], "course_links": REPO}) == ("ed-donner", "llm_engineering")
    assert R.course_repo({"resources": [{"title": "x", "url": "https://github.com/a/b.git"}]}) == ("a", "b")
    assert R.course_repo({"course_links": [{"title": "Docs", "url": "https://example.com"}]}) is None


def test_a_notebook_becomes_percent_text_without_outputs():
    nb = {"cells": [
        {"cell_type": "markdown", "source": ["# Day 2\n", "Using the API"]},
        {"cell_type": "code", "source": "from openai import OpenAI\nclient = OpenAI()", "outputs": [{"x": 1}]},
        {"cell_type": "code", "source": ""},
    ]}
    assert R.notebook_as_percent(nb) == ("# %% [markdown]\n# # Day 2\n# Using the API\n\n"
                                         "# %%\nfrom openai import OpenAI\nclient = OpenAI()")


def test_the_lecture_notebook_is_fetched_once_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "CACHE_DIR", tmp_path)
    calls = []

    class Resp:
        def __init__(self, payload, text=None):
            self._payload, self.text = payload, text or json.dumps(payload)

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def get(url, **kw):
        calls.append(url)
        if "api.github.com" in url:
            return Resp({"tree": [{"path": p, "type": "blob"} for p in PATHS]})
        return Resp({"cells": [{"cell_type": "code", "source": "print('day 2')"}]})

    monkeypatch.setattr(R.httpx, "get", get)
    m = meta("Week 1", "Day 2 - Chat Completions API")
    first = R.lecture_notebook(m)
    assert first == {"source": "github.com/ed-donner/llm_engineering/week1/day2.ipynb", "code": "# %%\nprint('day 2')"}
    assert R.lecture_notebook(m) == first and len(calls) == 2  # the second time from the cache


def test_a_network_failure_just_means_no_repo_code(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "CACHE_DIR", tmp_path)

    def get(url, **kw):
        raise R.httpx.ConnectError("offline")

    monkeypatch.setattr(R.httpx, "get", get)
    assert R.lecture_notebook(meta("Week 1", "Day 2 - x")) is None
