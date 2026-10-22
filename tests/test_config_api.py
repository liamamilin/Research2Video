"""System config API tests: structured read + patch merge."""

from __future__ import annotations

import pytest


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


SAMPLE = """# System configuration
ai:
  model: "deepseek-v4.1-flash"   # keep this comment
  base_url: "https://opencode.ai/zen/go/v1"
  api_key_env: "LLM_API_KEY"
search:
  provider: "parallel"
  max_results: 10
budget:
  monthly_usd_limit: 0
"""


@pytest.fixture()
def cfg(web_env):
    tmp_path, _ = web_env
    path = tmp_path / "config" / "system.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_get_requires_admin(client, cfg):
    assert client.get("/api/config/system").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/config/system").status_code == 403


def test_get_returns_parsed(client, cfg):
    _login(client)
    r = client.get("/api/config/system")
    assert r.status_code == 200
    body = r.json()
    assert body["parsed"]["ai"]["model"] == "deepseek-v4.1-flash"
    assert body["parsed"]["search"]["max_results"] == 10
    assert "content" in body and "masked" in body


def test_patch_merges_and_preserves_comments(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"model": "new-model"},
                                   "budget": {"monthly_usd_limit": 20}}},
                   headers=_csrf(client))
    assert r.status_code == 200

    text = cfg.read_text(encoding="utf-8")
    assert "keep this comment" in text
    assert 'model: "new-model"' in text
    assert "monthly_usd_limit: 20" in text
    assert 'provider: "parallel"' in text

    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["ai"]["model"] == "new-model"
    assert parsed["budget"]["monthly_usd_limit"] == 20


def test_patch_creates_nested_keys(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"notifications": {
                       "enabled": True,
                       "digest": {"enabled": True, "max_actions": 3}}}},
                   headers=_csrf(client))
    assert r.status_code == 200
    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["notifications"]["digest"]["max_actions"] == 3


def test_patch_none_deletes_key(client, cfg):
    _login(client)
    client.put("/api/config/system",
               json={"patch": {"budget": None}}, headers=_csrf(client))
    parsed = client.get("/api/config/system").json()["parsed"]
    assert "budget" not in parsed


def test_patch_conflict_detection(client, cfg):
    _login(client)
    mtime = client.get("/api/config/system").json()["mtime"]
    cfg.write_text(SAMPLE + "extra: 1\n", encoding="utf-8")
    import os
    os.utime(cfg, (mtime + 5, mtime + 5))

    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"model": "x"}}, "expected_mtime": mtime},
                   headers=_csrf(client))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_patch_requires_csrf(client, cfg):
    _login(client)
    r = client.put("/api/config/system", json={"patch": {"ai": {"model": "x"}}})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


def test_invalid_patch_rejected(client, cfg):
    _login(client)
    r = client.put("/api/config/system", json={"patch": "nope"},
                   headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_patch"


def test_backup_written_on_change(client, cfg, web_env):
    _login(client)
    client.put("/api/config/system",
               json={"patch": {"ai": {"model": "changed"}}}, headers=_csrf(client))
    backups = list((web_env[0] / "state" / "backups").glob("system_*.yaml"))
    assert backups, "expected a backup of system.yaml"


def test_a_malformed_mtime_is_an_error_not_a_disabled_check(client, cfg):
    """The conflict guard exists to stop a concurrent edit being clobbered.

    An `expected_mtime` that could not be parsed raised inside the check, and a
    bare `except (TypeError, ValueError): pass` swallowed it -- so the write
    went ahead with the guard silently off. Leaving the field out is a client
    choosing not to check, which is fine; sending a broken one is a bug and is
    now reported as one.
    """
    _login(client)
    r = client.put("/api/config/system",
                   json={"content": SAMPLE, "expected_mtime": "not-a-timestamp"},
                   headers=_csrf(client))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_mtime", r.text
    assert "keep this comment" in cfg.read_text(encoding="utf-8"), \
        "the file was overwritten despite the malformed guard value"


def test_omitting_the_mtime_still_saves(client, cfg):
    _login(client)
    r = client.put("/api/config/system", json={"content": SAMPLE},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text


def test_a_stale_mtime_is_still_a_conflict(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"content": SAMPLE, "expected_mtime": 1.0},
                   headers=_csrf(client))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "conflict"
    assert "keep this comment" in cfg.read_text(encoding="utf-8")


# --- provider presets + key-in-the-wrong-field guard --------------------------

def test_providers_endpoint_lists_presets(client, cfg):
    assert client.get("/api/config/providers").status_code == 401
    _login(client)
    r = client.get("/api/config/providers")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()["providers"]}
    assert "open-design" in ids and "opencode-go" in ids


def test_a_key_in_the_env_name_field_is_rejected(client, cfg):
    """Saving it would send an unauthenticated request and a confusing 401."""
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"api_key_env": "sk-amr-secret-here"}}},
                   headers=_csrf(client))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "secret_in_env_field"
    assert "sk-amr-secret-here" not in cfg.read_text(encoding="utf-8")


def test_a_real_env_name_is_accepted(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"api_key_env": "OPEN_DESIGN_KEY"}}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    assert "OPEN_DESIGN_KEY" in cfg.read_text(encoding="utf-8")


def test_fallback_models_are_accepted_as_a_string_list(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"fallback_models": ["minimax-m3", "deepseek-v4.1-flash"]}}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["ai"]["fallback_models"] == ["minimax-m3", "deepseek-v4.1-flash"]


def test_fallback_models_rejects_non_strings(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"fallback_models": [1, 2]}}},
                   headers=_csrf(client))
    assert r.status_code == 422, r.text


def test_review_ai_section_is_a_known_field(client, cfg):
    """review_ai (spec/video-adaptation-audit.md) is a real, validated section,
    not just whatever happens to be in the file -- a patch can create and edit
    it the same way as ai."""
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"review_ai": {
                       "model": "deepseek-v4-pro", "base_url": "https://llm.test/v1",
                       "api_key_env": "REVIEW_API_KEY", "fallback_models": ["minimax-m3"],
                   }}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["review_ai"]["model"] == "deepseek-v4-pro"
    assert parsed["review_ai"]["fallback_models"] == ["minimax-m3"]


def test_review_ai_rejects_an_embedding_field_it_has_no_use_for(client, cfg):
    """embedding_* belongs only to ai: one embedding model, not one per audit model."""
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"review_ai": {"embedding_model": "x"}}},
                   headers=_csrf(client))
    assert r.status_code == 422, r.text


def test_an_unknown_top_level_section_is_rejected(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"not_a_real_section": {"x": 1}}},
                   headers=_csrf(client))
    assert r.status_code == 422, r.text


def test_switching_provider_saves_url_and_env_together(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {
                       "base_url": "https://amr-link.open-design.ai/v1",
                       "api_key_env": "LLM_API_KEY",
                       "extra_headers": None}}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["ai"]["base_url"] == "https://amr-link.open-design.ai/v1"
    assert parsed["ai"]["api_key_env"] == "LLM_API_KEY"
