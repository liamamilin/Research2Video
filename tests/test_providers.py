"""LLM provider presets and the env-name/key-field confusion guard.

The bug this covers: a secret pasted into ``ai.api_key_env`` made
``os.environ.get`` return "" and the request go out with no ``Authorization``
header, so a hosted endpoint answered a bare ``invalid_api_key`` 401 that read
as "the key is wrong" when no key had been sent at all.
"""

from __future__ import annotations

import pytest

from core.providers import (
    PROVIDERS,
    find_provider_by_base_url,
    get_provider,
    is_valid_env_name,
    list_providers,
    looks_like_secret,
)


# --- env-name vs pasted key --------------------------------------------------

@pytest.mark.parametrize("name", ["LLM_API_KEY", "PARALLEL_API_KEY", "_x", "myKey2"])
def test_real_env_names_are_accepted(name):
    assert is_valid_env_name(name) is True
    assert looks_like_secret(name) is False


@pytest.mark.parametrize("value", [
    "sk-amr-fixture-only-not-a-real-secret",  # Synthetic fixture with the provider prefix.
    "sk-fixture-only-not-a-real-secret",
    "fixtureOnlyIdentifier-with-a-dash",
    "has space", "9leading-digit", "a-b",
])
def test_a_key_or_non_identifier_is_flagged_as_a_secret(value):
    assert looks_like_secret(value) is True
    assert is_valid_env_name(value) is False


def test_empty_is_neither_a_name_nor_a_secret():
    assert looks_like_secret("") is False
    assert is_valid_env_name("") is False


# --- presets -----------------------------------------------------------------

def test_every_preset_has_an_id_and_a_url():
    assert PROVIDERS, "at least one provider must ship"
    for preset in PROVIDERS:
        assert preset.id and preset.label
        assert preset.base_url.startswith("http")
        assert preset.api_key_env == "" or is_valid_env_name(preset.api_key_env)


def test_provider_ids_are_unique():
    ids = [p.id for p in PROVIDERS]
    assert len(ids) == len(set(ids))


def test_open_design_preset_is_available():
    """The provider the user is switching to must be selectable."""
    preset = get_provider("open-design")
    assert preset is not None
    assert preset.base_url == "https://amr-link.open-design.ai/v1"
    assert preset.api_key_env == "LLM_API_KEY"


def test_base_url_matches_a_preset_ignoring_trailing_slash_and_case():
    assert find_provider_by_base_url("https://opencode.ai/zen/v1/").id == "opencode-zen"
    assert find_provider_by_base_url("HTTPS://API.DEEPSEEK.COM/v1").id == "deepseek"
    assert find_provider_by_base_url("https://not-a-provider.example/v1") is None
    assert find_provider_by_base_url("") is None


def test_list_providers_returns_plain_dicts():
    items = list_providers()
    assert isinstance(items[0], dict)
    assert set(items[0]) >= {"id", "label", "base_url", "api_key_env",
                             "extra_headers", "models", "note"}


def test_opencode_go_ships_its_required_headers():
    """The Go endpoint rejects requests without the session/user-agent headers."""
    headers = get_provider("opencode-go").extra_headers
    assert "x-opencode-session" in headers
    assert "User-Agent" in headers
