import base64
import copy
import io
import json
import shutil
from uuid import uuid4
from dataclasses import replace
import sys
import threading

import numpy as np
from PIL import Image
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from g2.branding import BrandError, frozen_input
from g2.production_plan import ProductionPlanError, verify
from g3.app import create_app
from g3.config import Settings
from g3.tests.test_g3_package import READY, body, saved_input
from g3.tests.test_g3_package import FAKE_G2, PACKAGE
from g3.worker import G2ProcessRunner


DEMO = {"name": "Demo Studio", "tagline": "把知识，变成画面。", "outro_seconds": 4}


def asset(kind):
    buffer = io.BytesIO()
    if kind == "logo":
        Image.new("RGBA", (96, 96), "#28775b").save(buffer, "PNG")
    else:
        sf.write(buffer, .15*np.sin(np.arange(144000)*2*np.pi*440/48000), 48000, format="WAV", subtype="PCM_16")
    return {"base64": base64.b64encode(buffer.getvalue()).decode()}


def test_default_off_activation_freeze_override_and_replay(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        assert client.get("/api/brands").json() == {"active": {"enabled": False}, "profiles": []}
        old = client.post("/api/jobs", json=body()).json()["job_id"]
        assert "brand" not in saved_input(client, old)
        assert client.put("/api/brands/demo", json=DEMO).status_code == 200
        for kind in ("logo", "sonic"):
            response = client.post(f"/api/brands/demo/assets/{kind}", json=asset(kind))
            assert response.status_code == 200, response.text
            assert client.get(f"/api/brands/demo/assets/{kind}").status_code == 200
        client.put("/api/brands/active", json={"profile": "demo"})
        payload = body(idempotency_key="brand:demo")
        preview = client.post("/api/production/resolve", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["plan"]["brand_record_sha256"]
        made = client.post("/api/jobs", json=payload)
        assert made.status_code == 202, made.text
        jid = made.json()["job_id"]
        saved = saved_input(client, jid)
        folder = client.app.state.store.source_path(jid).parent
        record = frozen_input(folder, saved)
        assert record["profile"]["name"] == "Demo Studio"
        assert record["sha256"] == preview.json()["plan"]["brand_record_sha256"]
        for slot in ("intro", "outro", "badge", "sound"):
            assert saved["packaging_resolved"]["components"][slot]["enabled"] is False
        verify(saved)
        changed = copy.deepcopy(saved)
        changed["brand_resolved"]["profile"]["name"] = "Tampered"
        with pytest.raises((BrandError, ProductionPlanError)):
            verify(changed)
        disabled = client.post("/api/jobs", json=body(brand={"enabled": False}))
        assert disabled.status_code == 202, disabled.text
        assert "brand_resolved" not in saved_input(client, disabled.json()["job_id"])
        client.put("/api/brands/demo", json={**DEMO, "name": "Changed Demo"})
        client.put("/api/brands/active", json={"enabled": False})
        replay = client.post("/api/jobs", json=payload)
        assert replay.json()["job_id"] == jid
        assert frozen_input(folder, saved) == record


def test_revision_uses_frozen_brand_after_live_profile_removed(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        client.put("/api/brands/demo", json=DEMO)
        client.post("/api/brands/demo/assets/logo", json=asset("logo"))
        made = client.post("/api/jobs", json=body(brand={"profile": "demo"}))
        assert made.status_code == 202, made.text
        jid = made.json()["job_id"]
        saved = saved_input(client, jid)
        store = client.app.state.store
        shutil.rmtree(tmp_path / "brands/demo")
        draft = store.editable_content(jid)["options"]
        assert draft["brand_resolved"] == saved["brand_resolved"]
        child = store.create_job(str(uuid4()), {**draft, "source_text": "修订后的确定讲稿。",
                    "title": "Demo 修订", "parent_job_id": jid, "operation_type": "content_revision"})
        frozen = saved_input(client, child["job_id"])
        verify(frozen)
        assert frozen["brand_resolved"] == saved["brand_resolved"]
        folder = store.source_path(child["job_id"]).parent
        frozen_input(folder, frozen)
        path = next((folder / "brand").iterdir())
        path.write_bytes(b"changed")
        with pytest.raises(BrandError):
            frozen_input(folder, frozen)


@pytest.mark.parametrize("bad", [{"name": ""}, {"outro_seconds": 0}, {"accent": "red"}, {"assets": {"logo": {"file": "../x.png"}}}])
def test_invalid_profiles_and_assets(tmp_path, bad):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        # Asset paths supplied by callers are discarded, never trusted.
        if "assets" in bad:
            assert client.put("/api/brands/demo", json={**DEMO, **bad}).json()["assets"] == {}
        else:
            assert client.put("/api/brands/demo", json={**DEMO, **bad}).status_code == 422
        client.put("/api/brands/demo", json=DEMO)
        assert client.post("/api/brands/demo/assets/logo", json={"base64": base64.b64encode(b"not png").decode()}).status_code == 422
        assert client.post("/api/jobs", json=body(brand={"profile": "../private"})).status_code == 422
        assert client.post("/api/jobs", json=body(brand={"profile": "missing"})).status_code == 422


@pytest.mark.parametrize("mode", ["ok", "missing", "wrong_hash"])
def test_worker_publishes_verified_brand_final_and_matching_chapters(tmp_path, monkeypatch, mode):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE_G2 + '''
if saved.get("brand_resolved"):
    brand = art / "brand"; brand.mkdir()
    if mode != "missing":
        (brand / "video.mp4").write_bytes(b"branded-final")
        (brand / "brand-qa.json").write_text(json.dumps({"status":"PASS", "brand_sha256":
            saved["brand_resolved"]["sha256"] if mode == "ok" else "wrong"}))
        (brand / "chapters.json").write_text('{"timeline":"branded", "chapters":[{"id":"brand_outro"}]}')
    print("STEP brand: PASS in 0.1s", flush=True)
''')
    monkeypatch.setenv("AUTOVIDEO_TEST_MODE", mode)
    settings = replace(Settings(data_dir=tmp_path / "data"), g2_python=sys.executable, g2_script=fake)
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        client.put("/api/brands/demo", json=DEMO)
        jid = client.post("/api/jobs", json=body(brand={"profile": "demo"})).json()["job_id"]
        store = client.app.state.store
        G2ProcessRunner(settings, store).execute(store.claim_next(), threading.Event())
        job = client.get(f"/api/jobs/{jid}").json()
        if mode != "ok":
            assert job["status"] == "FAILED" and job["error_code"] == "BRAND_UNVERIFIED"
        else:
            assert job["status"] == "COMPLETED"
            artifacts = {a["name"]: a for a in client.get(f"/api/jobs/{jid}/artifacts").json()["items"]}
            assert client.get(artifacts["video.mp4"]["download_url"]).content == b"branded-final"
            assert client.get(artifacts["unbranded.mp4"]["download_url"]).content == b"content"
            assert client.get(artifacts["chapters.json"]["download_url"]).json()["timeline"] == "branded"


def test_demo_installer_does_not_overwrite_edits(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        installed = client.post("/api/brands/demo/install")
        assert installed.status_code == 200, installed.text
        assert set(installed.json()["assets"]) == {"logo", "sonic"}
        assert installed.json()["intro_seconds"] == 0
        assert installed.json()["corner_reveal_seconds"] == .6
        assert installed.json()["cta"] == {"enabled": True, "message": "喜欢这期内容，欢迎"}
        client.put("/api/brands/demo", json={**DEMO, "name": "Edited Demo"})
        assert client.post("/api/brands/demo/install").json()["name"] == "Edited Demo"
        assert client.get("/api/brands").json()["active"] == {"enabled": False}


def test_legacy_package_and_matrix_brand_overrides(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        client.put("/api/brands/demo", json=DEMO)
        context = {"matrix": {"id": "test-domain", "settings": {"brand": {"profile": "demo"}}}}
        response = client.post("/api/jobs", json=body(package=PACKAGE, production_context=context))
        assert response.status_code == 202, response.text
        saved = saved_input(client, response.json()["job_id"])
        assert saved["brand"]["profile"] == "demo"
        assert saved["packaging_resolved"]["components"]["intro"]["enabled"] is False
        assert saved["packaging_resolved"]["components"]["outro"]["enabled"] is False
        verify(saved)
        off = client.post("/api/jobs", json=body(production_context=context, brand={"enabled": False}))
        assert off.status_code == 202, off.text
        assert "brand_resolved" not in saved_input(client, off.json()["job_id"])


def test_intro_is_optional_frozen_and_can_be_disabled(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        client.put("/api/brands/demo", json=DEMO)
        jid = client.post("/api/jobs", json=body(brand={"profile": "demo"})).json()["job_id"]
        old = saved_input(client, jid)
        assert "intro_seconds" not in old["brand_resolved"]["profile"]
        for seconds in (1, 0):
            assert client.put("/api/brands/demo", json={**DEMO, "intro_seconds": seconds}).status_code == 200
            made = client.post("/api/jobs", json=body(brand={"profile": "demo"}))
            saved = saved_input(client, made.json()["job_id"])
            assert saved["brand_resolved"]["profile"]["intro_seconds"] == seconds
            verify(saved)
        for bad in (-1, .1, 3, True):
            assert client.put("/api/brands/demo", json={**DEMO, "intro_seconds": bad}).status_code == 422
        # Adding the option to the live profile cannot alter pre-feature snapshots.
        frozen_input(client.app.state.store.source_path(jid).parent, old)
        verify(old)


def test_generic_cta_is_optional_frozen_and_rejects_unknown_actions(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        client.put("/api/brands/demo", json=DEMO)
        jid = client.post("/api/jobs", json=body(brand={"profile": "demo"})).json()["job_id"]
        prior = saved_input(client, jid)
        assert "cta" not in prior["brand_resolved"]["profile"]
        for enabled in (True, False):
            changed = client.put("/api/brands/demo", json={**DEMO, "corner_reveal_seconds": .6,
                                "cta": {"enabled": enabled, "message": "喜欢这期内容，欢迎"}})
            assert changed.status_code == 200, changed.text
            made = client.post("/api/jobs", json=body(brand={"profile": "demo"}))
            saved = saved_input(client, made.json()["job_id"])
            assert saved["brand_resolved"]["profile"]["cta"]["enabled"] is enabled
            verify(saved)
        for bad in ({"cta": {"actions": ["coin"]}}, {"cta": {"enabled": "yes"}},
                    {"cta": {"message": "a"*25}}, {"corner_reveal_seconds": -1}):
            assert client.put("/api/brands/demo", json={**DEMO, **bad}).status_code == 422
        frozen_input(client.app.state.store.source_path(jid).parent, prior)
        verify(prior)
