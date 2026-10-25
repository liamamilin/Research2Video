import json
import sys
import threading
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings
from g3.worker import G2ProcessRunner

READY = lambda _: {"ready": True, "checks": {"diagram_layout": True}, "blockers": []}
CHAPTERS = [
    {"title": "开场：你要的数字已经内置", "paragraphs": ["你在给 crewAI 的 eval --models 做模型横评。", "  好消息是：从 1.15.27 起自带报告。  "]},
    {"title": "三个必须实测的口径", "paragraphs": ["用 cost_per_token 和 completion_cost 对照。"]},
]


def narration(**overrides):
    return {"title": "crewAI 成本与耗时", "input_mode": "narration", "chapters": CHAPTERS,
            "video_form": "auto", "duration_mode": "content", **overrides}


@pytest.mark.parametrize("form", ["auto", "information_motion", "process_diagram"])
def test_narration_mode_create_stores_the_script_and_chapters(tmp_path, form):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        response = c.post("/api/jobs", json=narration(video_form=form))
        assert response.status_code == 202
        jid = response.json()["job_id"]
        job = c.get(f"/api/jobs/{jid}").json()
        assert job["input_mode"] == "narration" and job["video_form"] == form and job["duration_mode"] == "content"
        store = c.app.state.store
        source = store.source_path(jid).read_text(encoding="utf-8")
        assert source == ("你在给 crewAI 的 eval --models 做模型横评。\n好消息是：从 1.15.27 起自带报告。\n\n"
                          "用 cost_per_token 和 completion_cost 对照。")
        saved = json.loads((store.source_path(jid).parent / "input.json").read_text(encoding="utf-8"))
        assert saved["input_mode"] == "narration"
        assert [c["title"] for c in saved["chapters"]] == [c["title"] for c in CHAPTERS]
        assert saved["chapters"][0]["paragraphs"][1] == "好消息是：从 1.15.27 起自带报告。"
        assert "source_text" not in saved


def test_material_mode_stays_the_default_and_stores_no_chapters(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        source = "会议记录的目的是让未参会的同事也能理解结果。先交代必要背景，再区分已经确定的决定、需要执行的行动和仍待确认的问题。"
        jid = c.post("/api/jobs", json={"source_text": source, "video_form": "auto", "duration_mode": "content"}).json()["job_id"]
        assert c.get(f"/api/jobs/{jid}").json()["input_mode"] == "material"
        saved = json.loads((c.app.state.store.source_path(jid).parent / "input.json").read_text(encoding="utf-8"))
        assert saved["input_mode"] == "material" and "chapters" not in saved


@pytest.mark.parametrize("payload,message", [
    ({"chapters": None}, "chapters"),
    ({"source_text": "这是一段足够长的素材正文，" * 5}, "source_text"),
    ({"duration_mode": "fixed"}, "content"),
    ({"video_form": "knowledge_cards"}, "流程动态图解或简洁信息动效"),
    ({"input_document_id": "abc"}, "input_document_id"),
    ({"chapters": [{"title": "空段落", "paragraphs": ["  "]}]}, "段落不能为空"),
    ({"chapters": [{"title": "空章节", "paragraphs": []}]}, "at least 1"),
    ({"chapters": []}, "at least 1"),
    ({"chapters": [{"title": "标" * 33, "paragraphs": ["正文。"]}]}, "at most 32"),
    ({"chapters": [{"title": "两行\n标题", "paragraphs": ["正文。"]}]}, "一行"),
    ({"chapters": [{"title": "额外字段", "paragraphs": ["正文。"], "visual_cues": []}]}, "Extra inputs"),
    ({"input_mode": "script"}, "material"),
])
def test_narration_mode_rejects_ambiguous_or_unsupported_requests(tmp_path, payload, message):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        body = narration(**payload)
        body = {k: v for k, v in body.items() if v is not None}
        response = c.post("/api/jobs", json=body)
        assert response.status_code == 422
        assert message in json.dumps(response.json(), ensure_ascii=False)
        assert c.get("/api/jobs").json()["total"] == 0


def test_material_mode_rejects_chapters_and_still_requires_source_text(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        assert "source_text" in c.post("/api/jobs", json={"video_form": "auto", "duration_mode": "content"}).text
        response = c.post("/api/jobs", json={"source_text": "这是一段足够长的素材正文，" * 5, "chapters": CHAPTERS,
                                            "video_form": "auto", "duration_mode": "content"})
        assert response.status_code == 422 and "input_mode" in response.text


def test_openapi_documents_both_input_modes(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        schemas = c.get("/openapi.json").json()["components"]["schemas"]
    request = schemas["CreateJobRequest"]
    assert "source_text" not in request.get("required", [])
    assert request["properties"]["input_mode"]["enum"] == ["material", "narration"]
    assert request["properties"]["input_mode"]["default"] == "material"
    assert schemas["NarrationChapter"]["required"] == ["title", "paragraphs"]


FAKE_G2 = """import json, os, sys
open(os.environ["AUTOVIDEO_TEST_ENV_FILE"], "w").write(json.dumps({k: v for k, v in os.environ.items() if k.startswith("AUTOVIDEO_G2_")}))
print("G2 FAIL: NARRATION_TEXT_MISMATCH: c1_n2（第 1 章「开场」第 30 字起）与讲稿不一致", flush=True)
sys.exit(1)
"""


def test_worker_locks_narration_and_a_mismatch_is_not_retryable(tmp_path, monkeypatch):
    fake = tmp_path / "fake_g2.py"
    fake.write_text(FAKE_G2, encoding="utf-8")
    env_file = tmp_path / "env.json"
    monkeypatch.setenv("AUTOVIDEO_TEST_ENV_FILE", str(env_file))
    settings = replace(Settings(data_dir=tmp_path / "data"), g2_python=sys.executable, g2_script=fake)
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as c:
        jid = c.post("/api/jobs", json=narration(video_form="information_motion")).json()["job_id"]
        store = c.app.state.store
        G2ProcessRunner(settings, store).execute(store.claim_next(), threading.Event())
        job = c.get(f"/api/jobs/{jid}").json()
    env = json.loads(env_file.read_text())
    assert env["AUTOVIDEO_G2_LOCK_NARRATION"] == "1" and env["AUTOVIDEO_G2_DURATION_MODE"] == "content"
    assert job["status"] == "FAILED" and job["error_code"] == "NARRATION_TEXT_MISMATCH"
    assert job["retryable"] is False and "第 1 章" in job["error_message"]


# --- Sources for the closing source card ------------------------------------------------------------

SOURCES = [{"title": "Opus 5.5 定价更新", "url": "https://www.example.com/news/opus-pricing", "date": "2026-10-16"},
           {"title": "GPT-6 Sol 价格页", "url": "http://pricing.example.org/sol", "date": "2026-10-16"}]


def test_sources_are_stored_in_input_json_for_the_pipeline(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        response = c.post("/api/jobs", json=narration(video_form="information_motion", sources=SOURCES))
        assert response.status_code == 202
        store = c.app.state.store
        saved = json.loads((store.source_path(response.json()["job_id"]).parent / "input.json").read_text(encoding="utf-8"))
        assert saved["sources"] == SOURCES
        assert "来源" not in store.source_path(response.json()["job_id"]).read_text(encoding="utf-8")  # never part of the script
        plain = c.post("/api/jobs", json=narration(video_form="information_motion")).json()["job_id"]
        assert "sources" not in json.loads((store.source_path(plain).parent / "input.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("payload,message", [
    ({"sources": []}, "at least 1"),
    ({"sources": SOURCES + SOURCES[:2]}, "at most 3"),
    ({"sources": [{**SOURCES[0], "title": "题" * 41}]}, "at most 40"),
    ({"sources": [{**SOURCES[0], "title": "两行\n标题"}]}, "一行"),
    ({"sources": [{**SOURCES[0], "url": "ftp://example.com/a"}]}, "http(s)"),
    ({"sources": [{**SOURCES[0], "url": "https://example.com/a b"}]}, "http(s)"),
    ({"sources": [{**SOURCES[0], "url": "javascript:alert(1)"}]}, "http(s)"),
    ({"sources": [{**SOURCES[0], "date": "2026-13-01"}]}, "YYYY-MM-DD"),
    ({"sources": [{**SOURCES[0], "date": "2026-1-5"}]}, "YYYY-MM-DD"),
    ({"sources": [{**SOURCES[0], "author": "x"}]}, "Extra inputs"),
    ({"sources": [{"title": "缺日期", "url": "https://example.com/a"}]}, "Field required"),
])
def test_sources_are_validated(tmp_path, payload, message):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        response = c.post("/api/jobs", json=narration(**payload))
        assert response.status_code == 422
        assert message in json.dumps(response.json(), ensure_ascii=False)
        assert c.get("/api/jobs").json()["total"] == 0


def test_sources_are_available_to_material_mode_via_the_packaging_library(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, preflight=READY)) as c:
        source = "会议记录的目的是让未参会的同事也能理解结果。先交代必要背景，再区分已经确定的决定、需要执行的行动和仍待确认的问题。"
        response = c.post("/api/jobs", json={"source_text": source, "video_form": "information_motion", "duration_mode": "content", "sources": SOURCES})
        assert response.status_code == 202
        saved = json.loads((c.app.state.store.source_path(response.json()["job_id"]).parent / "input.json").read_text())
        assert saved["packaging_resolved"]["source_card_state"] == "enabled"
