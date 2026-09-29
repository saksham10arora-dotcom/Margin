"""Shared test setup."""
import pytest


@pytest.fixture(autouse=True)
def _no_personal_engines(tmp_path_factory, monkeypatch):
    """Tests never read your ~/.margin/engines.toml: the engine chain they see
    is the built-in one unless a test writes its own file."""
    from sidecar import providers

    monkeypatch.setattr(providers, "CONFIG_PATH", tmp_path_factory.mktemp("engines") / "engines.toml")
    monkeypatch.setattr(providers, "_cache", None)
    monkeypatch.delenv("MARGIN_ENGINES", raising=False)


@pytest.fixture(autouse=True)
def _offline_catalog_and_private_keys(tmp_path_factory, monkeypatch):
    """No test fetches models.dev or reads/writes Margin's real key file."""
    from sidecar import catalog, config

    monkeypatch.setattr(catalog, "CACHE_PATH", tmp_path_factory.mktemp("catalog") / "models.dev.json")
    monkeypatch.setattr(catalog, "_loaded", None)
    monkeypatch.setattr(catalog, "_real_fetch", catalog._fetch, raising=False)  # for the test of fetching itself
    monkeypatch.setattr(catalog, "_fetch", lambda: None)
    monkeypatch.setattr(config, "MARGIN_KEYS_PATH", tmp_path_factory.mktemp("keys") / "keys.env")
    # Nor your ~/.config/keys.env, nor keys in the shell that ran the tests.
    monkeypatch.setattr(config, "KEYS_PATH", tmp_path_factory.mktemp("shared") / "keys.env")
    import os
    import re
    for name in [n for n in os.environ if re.search(r"(API_KEY|TOKEN)(_\d+)?$", n)]:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _no_personal_order(tmp_path_factory, monkeypatch):
    """No test reads or writes your saved model order."""
    from sidecar import chain

    monkeypatch.setattr(chain, "CHAIN_PATH", tmp_path_factory.mktemp("chain") / "chain.json")
