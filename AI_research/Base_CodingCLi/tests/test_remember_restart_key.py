"""A settings reload must not rotate the persisted key and invalidate remembered logins."""
import pytest


@pytest.mark.parametrize("name", ["AI_RESEARCH_SECRET_KEY", "WEB_SECRET_KEY"])
def test_settings_reload_reuses_persisted_key_when_environment_snapshot_is_missing(tmp_path, monkeypatch, name):
    import jwt
    from web import settings
    key = "a-stable-private-signing-key-0123456789-AbCdEfGh"
    env_file = tmp_path / ".env"
    env_file.write_text(f'{name}="{key}"\n')
    original = env_file.read_bytes()
    monkeypatch.setattr(settings, "_secret_file", lambda: env_file)
    monkeypatch.delenv("AI_RESEARCH_SECRET_KEY", raising=False)
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)
    monkeypatch.setenv("AI_RESEARCH_ENV", "development")
    config = tmp_path / "web.yaml"
    config.write_text("auth:\n  secret_key: CHANGE_ME\n")
    token = jwt.encode({"sub": "1", "kind": "refresh"}, key, algorithm="HS256")
    first = settings.WebSettings.load(str(config))
    monkeypatch.delenv("AI_RESEARCH_SECRET_KEY", raising=False)
    second = settings.WebSettings.load(str(config))
    assert first.auth.secret_key == second.auth.secret_key == key
    assert jwt.decode(token, second.auth.secret_key, algorithms=["HS256"])["kind"] == "refresh"
    assert env_file.read_bytes() == original
