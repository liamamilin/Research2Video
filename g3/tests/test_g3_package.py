import copy
import json
import sys
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings
from g3.store import STAGES
from g3.worker import G2ProcessRunner

READY = lambda _: {"ready": True, "checks": {"diagram_layout": True}, "blockers": []}
CHAPTERS = [{"title": "开场", "paragraphs": ["先说明价格变化。"]}, {"title": "细节", "paragraphs": ["再说明细节。"]}]
PACKAGE = {"accent": "#1F7A4D",
           "intro": {"template": "stinger", "title": "快讯", "subtitle": "2026-10-19", "seconds": 1.5, "sound": "synth_sting"},
           "outro": {"template": "end_card", "title": "快讯", "lines": ["来源见简介"], "seconds": 3, "sound": "synth_sting"}}
NORMAL = {"accent": "#1f7a4d", "intro": {**PACKAGE["intro"]}, "outro": {**PACKAGE["outro"], "seconds": 3.0}}
NORMAL["intro"]["seconds"] = 1.5


def body(**extra):
    return {"title": "快讯样片", "input_mode": "narration", "chapters": CHAPTERS, "video_form": "information_motion",
            "duration_mode": "content", **extra}


def saved_input(client, jid):
    return json.loads((client.app.state.store.source_path(jid).parent / "input.json").read_text(encoding="utf-8"))


def test_package_is_stored_normalised_and_absent_when_not_requested(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        jid = c.post("/api/jobs", json=body(package=PACKAGE)).json()["job_id"]
        saved = saved_input(c, jid)
        assert saved["package"] == NORMAL
        plain = c.post("/api/jobs", json=body()).json()["job_id"]
        assert "package" not in saved_input(c, plain)


def test_package_works_with_material_mode_too(tmp_path):
    source = "会议记录的目的是让未参会的同事也能理解结果。先交代必要背景，再区分已经确定的决定、需要执行的行动和仍待确认的问题。"
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        response = c.post("/api/jobs", json={"source_text": source, "video_form": "information_motion",
                                             "duration_mode": "content", "package": PACKAGE})
        assert response.status_code == 202 and saved_input(c, response.json()["job_id"])["package"] == NORMAL


def bad(path, value):
    config = copy.deepcopy(PACKAGE)
    target = config
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return config


@pytest.mark.parametrize("config,message", [
    ({**PACKAGE, "bgm": "x"}, "不支持的字段"),
    (bad(["intro", "template"], "end_card"), "stinger"),
    (bad(["outro", "template"], "stinger"), "end_card"),
    (bad(["intro", "seconds"], 6), "seconds"),
    (bad(["outro", "seconds"], 0.2), "seconds"),
    (bad(["intro", "title"], "超过十二个字的栏目名称太长了"), "intro.title"),
    (bad(["intro", "subtitle"], "副" * 25), "intro.subtitle"),
    (bad(["outro", "lines"], ["a", "b", "c", "d"]), "outro.lines"),
    (bad(["outro", "lines"], ["行" * 31]), "每行"),
    (bad(["intro", "sound"], "Bad Sound"), "sound"),
    (bad(["intro", "sound"], "unknown_cue"), "不在套件里"),
    (bad(["accent"], "#12"), "accent"),
    ({"accent": "#112233"}, "至少"),
    ({**PACKAGE, "badge": "角" * 13}, "badge"),
])
def test_package_is_validated_at_the_api(tmp_path, config, message):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        response = c.post("/api/jobs", json=body(package=config))
        assert response.status_code == 422 and message in json.dumps(response.json(), ensure_ascii=False)
        assert c.get("/api/jobs").json()["total"] == 0


def test_package_stage_follows_the_media_check_in_the_stage_list():
    assert STAGES[-2:] == ("QUALITY_CHECK", "PACKAGE")


# --- worker ----------------------------------------------------------------------------------------

FAKE_G2 = """import json, os, sys
from pathlib import Path
run = Path(os.environ["AUTOVIDEO_G2_RUN_DIR"]); art = run / "artifacts"; art.mkdir(parents=True)
(run / "fixtures").mkdir()
saved = json.loads(Path(os.environ["AUTOVIDEO_G2_INTENT_FILE"]).read_text())
mode = os.environ.get("AUTOVIDEO_TEST_MODE", "ok")
for step in ["narration-plan", "tts-and-align", "storyboard-plan", "draw-things-backgrounds", "compile-ir", "render", "verify-media"]:
    print(f"STEP {step}: PASS in 0.10s", flush=True)
(art / "G2_自动生成.mp4").write_bytes(b"content")
(art / "captions.srt").write_text("1\\n00:00:00,000 --> 00:00:01,000\\nx\\n")
(art / "chapters.json").write_text('{"chapters": []}')
(art / "qa.json").write_text('{"status": "PASS"}')
if saved.get("package"):
    package = art / "package"; package.mkdir()
    if mode == "fail":
        print("STEP package: FAIL: PACKAGE_SEAM_LOUDNESS: intro 提示音太响", flush=True)
        print("G2 FAIL: PACKAGE_SEAM_LOUDNESS: intro 提示音太响", flush=True)
        sys.exit(1)
    print("STEP package: PASS in 0.10s", flush=True)
    if mode != "no_report":
        (package / "package-qa.json").write_text('{"status": "PASS"}')
        (package / "video.mp4").write_bytes(b"packaged!")
        (package / "captions.srt").write_text("1\\n00:00:01,500 --> 00:00:02,500\\nx\\n")
        (package / "chapters.json").write_text('{"chapters": [], "package_offset_ms": 1500}')
        (package / "package.json").write_text('{"offset_ms": 1500}')
        (package / "contact-sheet.jpg").write_bytes(b"jpg")
else:
    print("STEP package: SKIPPED (请求没有 package)", flush=True)
"""


def run_worker(tmp_path, monkeypatch, mode, request):
    fake = tmp_path / "fake_g2.py"
    fake.write_text(FAKE_G2, encoding="utf-8")
    monkeypatch.setenv("AUTOVIDEO_TEST_MODE", mode)
    settings = replace(Settings(data_dir=tmp_path / "data"), g2_python=sys.executable, g2_script=fake)
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as c:
        jid = c.post("/api/jobs", json=request).json()["job_id"]
        store = c.app.state.store
        G2ProcessRunner(settings, store).execute(store.claim_next(), threading.Event())
        job = c.get(f"/api/jobs/{jid}").json()
        names = {a["name"]: a for a in c.get(f"/api/jobs/{jid}/artifacts").json()["items"]} if job["status"] == "COMPLETED" else {}
        stages = {s["stage"]: s["status"] for s in job["attempts"][-1]["stages"]}
        video = c.get(names["video.mp4"]["download_url"]).content if names else None
    return job, names, stages, video


def test_packaged_job_publishes_the_packaged_video_and_keeps_the_content_video(tmp_path, monkeypatch):
    job, names, stages, video = run_worker(tmp_path, monkeypatch, "ok", body(package=PACKAGE))
    assert job["status"] == "COMPLETED" and video == b"packaged!"
    assert {"content.mp4", "content-captions.srt", "content-chapters.json", "package.json", "package-qa.json",
            "package-contact-sheet.jpg"} <= set(names)
    assert stages["PACKAGE"] == "SUCCEEDED" and stages["QUALITY_CHECK"] == "SUCCEEDED"
    assert names["content.mp4"]["bytes"] == len(b"content")


def test_job_without_package_is_unchanged_and_the_stage_is_skipped(tmp_path, monkeypatch):
    job, names, stages, video = run_worker(tmp_path, monkeypatch, "ok", body())
    assert job["status"] == "COMPLETED" and video == b"content"
    assert not {"content.mp4", "package.json", "package-qa.json"} & set(names)
    assert stages["PACKAGE"] == "SKIPPED"


def test_a_failed_package_check_fails_the_job_with_its_code(tmp_path, monkeypatch):
    job, names, stages, _ = run_worker(tmp_path, monkeypatch, "fail", body(package=PACKAGE))
    assert job["status"] == "FAILED" and job["error_code"] == "PACKAGE_SEAM_LOUDNESS" and "太响" in job["error_message"]
    assert job["retryable"] is True and stages["PACKAGE"] == "FAILED" and stages["QUALITY_CHECK"] == "SUCCEEDED"


def test_a_package_without_a_passing_report_is_never_published(tmp_path, monkeypatch):
    job, _, stages, _ = run_worker(tmp_path, monkeypatch, "no_report", body(package=PACKAGE))
    assert job["status"] == "FAILED" and job["error_code"] == "PACKAGE_UNVERIFIED" and job["retryable"] is False


# --- retry and revisions keep the package ------------------------------------------------------------

def finish_job(store, jid, tmp_path):
    claim = store.claim_next()
    assert claim["job_id"] == jid
    store.mark_completed(jid, claim["attempt_no"], [])


def test_retry_reuses_the_saved_package_request(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        jid = c.post("/api/jobs", json=body(package=PACKAGE)).json()["job_id"]
        store = c.app.state.store
        claim = store.claim_next()
        store.mark_failed(jid, claim["attempt_no"], "PACKAGE", "PACKAGE_DECODE_FAILED", "x", True)
        store.retry(jid)
        assert saved_input(c, jid)["package"] == NORMAL  # the retry attempt reads the same input.json


def test_editable_content_and_content_revisions_inherit_the_package(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        jid = c.post("/api/jobs", json=body(package=PACKAGE)).json()["job_id"]
        store = c.app.state.store
        store.claim_next()
        # a completed parent with a narration artifact so revisions can start
        import hashlib
        claim = store.get_job(jid)
        attempt = store.data_dir / f"jobs/{jid}/attempts/1"
        attempt.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({"segments": [{"text": "先说明价格变化。"}]}).encode()
        (attempt / "narration.json").write_bytes(raw)
        store.mark_completed(jid, 1, [{"name": "narration.json", "relative_path": "narration.json", "content_type": "application/json",
                                       "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}])
        assert c.get(f"/api/jobs/{jid}/editable-content").json()["options"]["package"] == NORMAL
        for mode, text in (("narration", "先说明价格变化。"), ("source", "一段全新的素材正文，" * 8)):
            child = c.post(f"/api/jobs/{jid}/revisions", json={"mode": mode, "text": text})
            assert child.status_code == 202, child.text
            assert saved_input(c, child.json()["job_id"])["package"] == NORMAL
        plain = c.post("/api/jobs", json=body()).json()["job_id"]
        store.claim_next()
        store.mark_completed(plain, 1, [])
        assert "package" not in c.get(f"/api/jobs/{plain}/editable-content").json()["options"]


def test_scene_edit_job_inherits_the_package(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        store = c.app.state.store
        jid = c.post("/api/jobs", json=body(package=PACKAGE)).json()["job_id"]
        path = store.source_path(jid).parent / "input.json"  # scene edits need a Draw Things job
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "use_draw_things": True}), encoding="utf-8")
        store.claim_next()
        store.mark_completed(jid, 1, [])
        store.scene_edit_source = lambda *args: {"attempt_root": tmp_path}  # the image bookkeeping is covered elsewhere
        child = store.create_scene_edit_job("11111111-1111-4111-8111-111111111111", jid, "opening")
        assert saved_input(c, child["job_id"])["package"] == NORMAL


def test_badge_is_stored_with_the_package(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        jid = c.post("/api/jobs", json=body(package={**PACKAGE, "badge": "快讯"})).json()["job_id"]
        assert saved_input(c, jid)["package"] == {**NORMAL, "badge": "快讯"}
