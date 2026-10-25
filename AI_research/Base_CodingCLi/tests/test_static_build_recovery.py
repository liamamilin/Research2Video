from fastapi.testclient import TestClient


def test_missing_build_assets_do_not_stop_api_startup_and_reappear_without_restart(web_env, monkeypatch):
    from web import server

    tmp, _ = web_env
    monkeypatch.setattr(server, "__file__", str(tmp / "web/server.py"))
    dist = tmp / "ui/dist"
    dist.mkdir(parents=True)
    assets = dist / "assets"
    with TestClient(server.create_app()) as client:
        assert client.get("/api/live").status_code == 200
        assert client.get("/assets/app-abc123.js").status_code == 404
        assets.mkdir()
        (assets / "app-abc123.js").write_text("console.log('rebuilt')")
        built = client.get("/assets/app-abc123.js")
        assert built.status_code == 200 and built.text == "console.log('rebuilt')"
        assert "immutable" in built.headers["cache-control"]
        (assets / "app-abc123.js").unlink()
        assets.rmdir()
        assert client.get("/assets/app-abc123.js").status_code == 404
        assert client.get("/api/live").status_code == 200
