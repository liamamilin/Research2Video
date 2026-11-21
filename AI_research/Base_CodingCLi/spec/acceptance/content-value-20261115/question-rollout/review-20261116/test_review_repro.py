"""Reproduce rollout review findings offline, without changing production."""
import json
from pathlib import Path

import pytest

from core import report_meta, knowledge_job
from core.video import audience_strategy as strategy, question_material as qm, reports, store
from tests.test_video_scripts import video_env, llm, script_json  # noqa: F401
from web.services import video_workflow as workflow


@pytest.mark.parametrize('status', ['enriched', 'unchanged', 'kept_original'])
def test_knowledge_fallback_preserves_original_dossier(video_env, llm, monkeypatch, status):
    from core.engine import ResearchEngine
    monkeypatch.chdir(video_env)
    p = video_env/'jobs/consumer/consumer_illustrated.yaml'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('name: 知识\nenabled: true\nknowledge: true\nsearch: false\nprompt: 解释原理\n'
                 'output: output/consumer/{date}_consumer_illustrated.md\n'
                 'content_strategy: audience-v1\nquestion_material: question-v1\n')
    (video_env/'config/system.yaml').write_text('question_material:\n  enabled: true\n')
    class Agent:
        def __init__(self, **kw): self.materials=[]; self.retrieved_urls=set()
        def run_fetch_only(self, *a, **kw): return '# 降噪耳机的原主问题\n原始底稿不可丢失的机制解释'
        def get_usage(self): return {}
        def close(self): pass
    monkeypatch.setattr('core.engine.ResearchAgent', Agent)
    monkeypatch.setattr(knowledge_job, 'block', lambda _: '### [只是选题参考](https://example.org/ideas)\n无关参考新闻不能代替底稿')
    def enhance(job, report, *a, **kw):
        return report, '', {'strategy':qm.MARKER, 'status':status, 'topics':[], 'usage':{}}
    monkeypatch.setattr(qm, 'enhance', enhance)
    engine = ResearchEngine(str(video_env/'config'), str(video_env/'jobs'), workspace_dir=str(video_env))
    path = engine.run_job('consumer/consumer_illustrated'); assert path
    topic = reports.topic_for({'report_path':path}, str(video_env))
    topic['from_report']=path
    llm.replies=[script_json(300)]
    script=store.create_script('report_digest', topic, production='consumer_illustrated')
    assert workflow.run(script['id'], llm_config=object())=='DRAFT'
    sent=llm.calls[0][1]['content']
    assert '原始底稿不可丢失的机制解释' in sent
    assert '无关参考新闻不能代替底稿' not in sent


def test_knowledge_history_records_actual_question_not_editorial_preface(tmp_path):
    folder=tmp_path/'output/consumer';folder.mkdir(parents=True)
    report='# 选题说明（写在前面的几句话）\n选定：降噪耳机是怎么消掉噪音的？\n\n# 降噪耳机是怎么消掉噪音的？\n完整解释'
    p=folder/'2026-11-15_consumer_illustrated.md';p.write_text(report)
    job={'output':'output/consumer/{date}_consumer_illustrated.md'}
    assert knowledge_job.past_titles(job,str(tmp_path))==['降噪耳机是怎么消掉噪音的？']
    assert reports.title_of(str(p))=='降噪耳机是怎么消掉噪音的？'
