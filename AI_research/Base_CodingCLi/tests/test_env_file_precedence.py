"""The .env file must win over a stale value inherited from a parent process.

The bug: the Web console writes API keys into .env and promises they take
effect, but ``load_dotenv`` defaults to ``override=False``, so a value already
in ``os.environ`` silently won. The launchd gateway runs for weeks, loads .env
into its own environment at startup, and passes that snapshot to every app it
spawns -- so a key saved in the console came back as the *previous* key after a
restart. These tests pin the contract that fixes it.
"""

from __future__ import annotations

import os

import pytest

from utils.env_file import load_env


@pytest.fixture()
def env_file(tmp_path):
    def write(text: str):
        path = tmp_path / ".env"
        path.write_text(text, encoding="utf-8")
        return path
    return write


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LLM_API_KEY", "PARALLEL_API_KEY", "AI_RESEARCH_SECRET_KEY",
                 "WEB_SECRET_KEY", "AI_RESEARCH_ENV"):
        monkeypatch.delenv(name, raising=False)


# --- the bug ----------------------------------------------------------------

def test_a_stale_inherited_value_does_not_shadow_the_file(env_file, monkeypatch):
    """Replays the incident: the parent exported the OLD key weeks ago."""
    path = env_file('LLM_API_KEY="sk-amr-NEW"\n')
    monkeypatch.setenv("LLM_API_KEY", "sk-fixture-OLD")

    load_env(path)

    assert os.environ["LLM_API_KEY"] == "sk-amr-NEW"


def test_a_missing_file_is_not_an_error(monkeypatch, tmp_path):
    """A container configured purely by environment variables has no .env."""
    monkeypatch.setenv("LLM_API_KEY", "from-environment")
    assert load_env(tmp_path / "does-not-exist.env") is None
    assert os.environ["LLM_API_KEY"] == "from-environment"


# --- what must keep working -------------------------------------------------

def test_variables_absent_from_the_file_keep_their_environment_value(
        env_file, monkeypatch):
    """AI_RESEARCH_ENV / AI_RESEARCH_WEB_CONFIG are deployment concerns."""
    path = env_file('LLM_API_KEY="sk-file"\n')
    monkeypatch.setenv("AI_RESEARCH_ENV", "production")
    monkeypatch.setenv("AI_RESEARCH_WEB_CONFIG", "/etc/arec/web.yaml")

    load_env(path)

    assert os.environ["AI_RESEARCH_ENV"] == "production"
    assert os.environ["AI_RESEARCH_WEB_CONFIG"] == "/etc/arec/web.yaml"


def test_an_operator_secret_key_still_beats_the_file(env_file, monkeypatch):
    """The README tells operators to set the JWT secret in the environment."""
    path = env_file('AI_RESEARCH_SECRET_KEY="file-secret-aaaaaaaaaaaaaaaaaaaa"\n')
    monkeypatch.setenv("AI_RESEARCH_SECRET_KEY", "operator-secret-bbbbbbbbbb")

    load_env(path)

    assert os.environ["AI_RESEARCH_SECRET_KEY"] == "operator-secret-bbbbbbbbbb"


def test_the_file_secret_is_used_when_the_operator_set_none(env_file):
    path = env_file('AI_RESEARCH_SECRET_KEY="file-secret-aaaaaaaaaaaaaaaaaaaa"\n')
    load_env(path)
    assert os.environ["AI_RESEARCH_SECRET_KEY"] == "file-secret-aaaaaaaaaaaaaaaaaaaa"


def test_the_legacy_web_secret_key_spelling_is_honoured(env_file, monkeypatch):
    """WEB_SECRET_KEY is the older spelling of the same value."""
    path = env_file('AI_RESEARCH_SECRET_KEY="file-secret-aaaaaaaaaaaaaaaaaaaa"\n')
    monkeypatch.setenv("WEB_SECRET_KEY", "legacy-operator-secret-cccccccc")

    load_env(path)

    assert os.environ["AI_RESEARCH_SECRET_KEY"] == "legacy-operator-secret-cccccccc"


# --- rendered wiring --------------------------------------------------------

def test_both_entry_points_load_the_file_first():
    """The CLI and the web app must agree, or a bug shows up on only one path."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("run.py", "web/settings.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "load_env(" in text, f"{rel} does not use the file-first loader"
        assert "load_dotenv(" not in text, (
            f"{rel} still calls load_dotenv directly (override=False), which "
            f"lets a stale inherited value shadow the file")


# --- the origin of the stale snapshot ---------------------------------------

def test_gateway_does_not_forward_the_keys_it_loaded_at_startup(tmp_path):
    """The gateway lives for weeks; its .env copy is stale almost immediately."""
    from scripts.gateway import _env_file_keys, _OPERATOR_OWNED

    (tmp_path / ".env").write_text('LLM_API_KEY="sk-x"\nPARALLEL_API_KEY="par"\n',
                                   encoding="utf-8")
    keys = _env_file_keys(tmp_path)

    gateway_env = {
        "PATH": "/usr/bin",
        "LLM_API_KEY": "sk-fixture-stale-key",
        "AI_RESEARCH_SECRET_KEY": "operator-set-secret",
    }
    passed = {k: v for k, v in gateway_env.items()
              if k not in keys or k in _OPERATOR_OWNED}

    assert "LLM_API_KEY" not in passed, "the stale key would shadow the file"
    assert passed["AI_RESEARCH_SECRET_KEY"] == "operator-set-secret"
    assert passed["PATH"] == "/usr/bin"


def test_the_project_env_file_defines_the_llm_key():
    """A sanity check on the shipped .env.example the loader is written for."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert "LLM_API_KEY" in example.read_text(encoding="utf-8")


def test_gateway_env_key_parser_handles_comments_and_export(tmp_path):
    from scripts.gateway import _env_file_keys

    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        'LLM_API_KEY="sk-x"\n'
        "export PARALLEL_API_KEY=par\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    assert _env_file_keys(tmp_path) == {"LLM_API_KEY", "PARALLEL_API_KEY"}


def test_gateway_key_parser_tolerates_a_missing_file(tmp_path):
    from scripts.gateway import _env_file_keys

    assert _env_file_keys(tmp_path) == frozenset()
