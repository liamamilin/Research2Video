import hashlib
import json
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

SOURCE="任务交接需要讲清楚目标、背景和完成标准。目标说明交付物，背景说明给谁看和已有材料，标准列出必须完成的内容。没有记录的信息应标明待确认，不能猜测补齐。"
READY=lambda _:{"ready":True,"checks":{"diagram_layout":True},"blockers":[]}


def create(client, **kwargs):
    v=client.post("/api/jobs",json={"source_text":SOURCE,"duration_mode":"content","video_form":"information_motion",**kwargs})
    assert v.status_code==202
    return v.json()["job_id"]


def finish(client,jid):
    store=client.app.state.store;claim=store.claim_next();assert claim["job_id"]==jid
    attempt=store.data_dir/claim["workspace_rel_path"];attempt.mkdir(parents=True)
    path=attempt/"narration.json";raw=json.dumps({"segments":[{"text":SOURCE}]}).encode();path.write_bytes(raw)
    store.mark_completed(jid,1,[{"name":"narration.json","relative_path":"narration.json","content_type":"application/json","sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)}])


def test_preferences_optional_custom_and_persistent(tmp_path):
    settings=Settings(data_dir=tmp_path/"data")
    custom={"audience":"第一次做交接的新同事","purpose":"我的自定义用途","tone":"简洁但亲切","focus":"保留待确认的处理方式"}
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        a=create(c);b=create(c,creative_intent=custom)
        assert not any(c.get(f"/api/jobs/{a}").json()["creative_intent"].values())
        assert c.get(f"/api/jobs/{b}").json()["creative_intent"]==custom
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        assert c.get(f"/api/jobs/{b}/editable-content").json()["creative_intent"]==custom


def test_source_revision_keeps_parent_and_options_and_links_versions(tmp_path):
    settings=Settings(data_dir=tmp_path/"data")
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        jid=create(c,creative_intent={"audience":"新同事"});finish(c,jid)
        original=c.get(f"/api/jobs/{jid}/editable-content").json()
        updated=SOURCE.replace("目标、背景和完成标准","交付目标、必要背景和明确的完成标准")
        response=c.post(f"/api/jobs/{jid}/revisions",json={"mode":"source","text":updated})
        assert response.status_code==202
        child=response.json()["job_id"];view=c.get(f"/api/jobs/{child}").json()
        assert view["parent_job_id"]==jid and view["revision_mode"]=="source"
        assert view["creative_intent"]["audience"]=="新同事"
        assert c.get(f"/api/jobs/{jid}/editable-content").json()==original
        assert c.get(f"/api/jobs/{child}/editable-content").json()["source_text"]==updated
        assert c.get(f"/api/jobs/{jid}/revisions").json()["items"][0]["job_id"]==child


def test_narration_revision_retains_exact_text_and_uses_natural_duration(tmp_path):
    settings=Settings(data_dir=tmp_path/"data")
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        jid=create(c);finish(c,jid)
        text="先说明交付目标。\n\n需要确认的内容，不要凭猜测补齐。"
        v=c.post(f"/api/jobs/{jid}/revisions",json={"mode":"narration","text":text,"creative_intent":{"tone":"自然直接"}})
        assert v.status_code==202
        child=v.json()["job_id"];draft=c.get(f"/api/jobs/{child}/editable-content").json()
        assert draft["source_text"]==text
        assert draft["options"]["duration_mode"]=="content"
        claim=c.app.state.store.claim_next();assert claim["operation_type"]=="content_revision"
        saved=json.loads((c.app.state.store.source_path(child).parent/"input.json").read_text())
        assert saved["revision_mode"]=="narration" and saved["creative_intent"]["tone"]=="自然直接"


def test_revision_rejects_active_parent_missing_script_and_corrupt_source(tmp_path):
    settings=Settings(data_dir=tmp_path/"data")
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        jid=create(c)
        assert c.post(f"/api/jobs/{jid}/revisions",json={"mode":"source","text":SOURCE}).status_code==409
        store=c.app.state.store;store.claim_next();store.mark_failed(jid,1,"PLAN","INPUT_REQUIRES_CLARIFICATION","补充材料",False)
        assert c.post(f"/api/jobs/{jid}/revisions",json={"mode":"narration","text":SOURCE}).status_code==409
        assert c.post(f"/api/jobs/{jid}/revisions",json={"mode":"source","text":SOURCE}).status_code==202
        store.source_path(jid).write_text("changed")
        assert c.get(f"/api/jobs/{jid}/editable-content").status_code==409


def test_source_revision_preserves_legacy_fixed_input_rules(tmp_path):
    settings=Settings(data_dir=tmp_path/"data")
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        jid=create(c,duration_mode="fixed");finish(c,jid)
        assert c.post(f"/api/jobs/{jid}/revisions",json={"mode":"source","text":SOURCE*50}).status_code==422
