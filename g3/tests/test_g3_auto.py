from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings
SOURCE="任务交接要说明目标背景和标准。背景说明给谁看和现有记录，缺失信息应标为待确认，不能猜测补齐。"*2
READY=lambda _:{"ready":True,"checks":{},"blockers":[]}

def test_auto_selection_analysis_persists_separately_from_request(tmp_path):
    settings=Settings(data_dir=tmp_path/"data")
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        response=c.post("/api/jobs",json={"source_text":SOURCE,"video_form":"auto","duration_mode":"content"})
        assert response.status_code==202
        jid=response.json()["job_id"];store=c.app.state.store
        claim=store.claim_next();assert claim["video_form"]=="auto"
        brief={"requested_form":"auto","selected_form":"information_motion","recommended_form":"information_motion","selection_mode":"automatic","status":"ready","reason":"并列内容","issues":[],"ordered_unit_ids":["u1"],"excluded_units":[]}
        store.record_input_analysis(jid,1,brief)
        view=c.get(f"/api/jobs/{jid}").json()
        assert view["video_form"]=="auto" and view["resolved_video_form"]=="information_motion"
        assert c.post("/api/jobs",json={"source_text":SOURCE,"video_form":"auto","use_draw_things":True}).status_code==422
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        assert c.get(f"/api/jobs/{jid}").json()["input_analysis"]["reason"]=="并列内容"
