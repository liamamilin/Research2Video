"""Reproduce rollout review findings offline, without changing production."""
import json
from pathlib import Path

import pytest

from core import report_meta, knowledge_job
from core.video import audience_strategy as strategy, question_material as qm, reports, store
from tests.test_video_scripts import video_env, llm, script_json  # noqa: F401
from web.services import video_workflow as workflow


@pytest.mark.parametrize('status', ['enriched', 'unchanged', 'kept_original', 'disabled'])
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
        return report, '', ({} if status == 'disabled' else {'strategy':qm.MARKER, 'status':status, 'topics':[], 'usage':{}})
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


@pytest.mark.parametrize('failure', ['json', 'source_roles'])
def test_failed_selection_publishes_original_report_and_usage(tmp_path, monkeypatch, llm, failure):
    from core.engine import ResearchEngine
    from core.state import StateManager
    from core.search import SearchResult
    from core import ai_dossier_sources
    from tests.test_audience_rollout import chosen, MATERIAL, URL
    monkeypatch.chdir(tmp_path)
    name = 'monitoring/ai_flash_discovery'
    path = tmp_path/'jobs'/f'{name}.yaml'
    path.parent.mkdir(parents=True)
    path.write_text('name: 快讯\nenabled: true\nsearch: false\nsources: [rss:example]\nprompt: 研究\n'
                    'output: output/{date}_flash.md\ncontent_strategy: audience-v1\nquestion_material: question-v1\n')
    (tmp_path/'config').mkdir()
    (tmp_path/'config/system.yaml').write_text('{}')
    monkeypatch.setattr(report_meta, 'META_FILE', str(tmp_path/'state/report_meta.jsonl'))
    monkeypatch.setattr(ai_dossier_sources, 'wanted', lambda _: False)
    original = '# 2026-11-16 AI快讯\n### 1.1 已完成的研究\n完整解释 '+URL
    class Agent:
        def __init__(self, **kw): self.materials=[]; self.retrieved_urls={URL}
        def run_fetch_only(self, *a, **kw): return original
        def get_usage(self): return {'total_tokens':50, 'requests':1}
        def close(self): pass
    monkeypatch.setattr('core.engine.ResearchAgent', Agent)
    bad = chosen().model_dump()
    bad['candidates'][0]['source_roles'] = {'S999':'未选入的来源'}
    llm.replies = [('not json' if failure == 'json' else json.dumps(bad))] * 3
    engine = ResearchEngine(str(tmp_path/'config'), str(tmp_path/'jobs'), workspace_dir=str(tmp_path))
    monkeypatch.setattr(engine, '_fetch_sources_strict', lambda job: (MATERIAL, ([SearchResult('原文', URL)], {})))
    result = engine.run_job(name)
    assert result and (tmp_path/result).read_text().strip() == original
    state = StateManager.get(name)
    assert state['last_status'] == 'success'
    assert state['last_usage']['requests'] == 4 and state['last_usage']['total_tokens'] >= 50
    compact = state['last_usage']['audience_strategy']
    assert compact['status'] == 'kept_original'
    receipt = json.loads(Path(compact['receipt']).read_text())
    assert receipt['initial_report'] == original
    meta = json.loads((tmp_path/'state/report_meta.jsonl').read_text().splitlines()[-1])
    assert 'audience_strategy' not in meta  # fallback report isn't a fixed selection
    assert len(llm.calls) == 3  # no supplementary model calls after selection failed


def test_source_role_mismatch_is_repaired_inside_json_retry(llm):
    from tests.test_audience_rollout import chosen, MATERIAL
    bad = chosen().model_dump()
    bad['candidates'][0]['source_roles']['S99'] = '未选来源'
    good = chosen().model_dump_json()
    llm.replies = [json.dumps(bad), good, good]
    result = strategy.select(__import__('core.llm', fromlist=['LLMClient']).LLMClient(object()),
                             't05', strategy.packets(MATERIAL), '初稿')
    assert result.candidates and len(llm.calls) == 3
    assert '素材编号' in llm.calls[1][-1]['content']


def test_selection_cancellation_never_becomes_success(monkeypatch):
    from core.errors import CancelledError
    class Client:
        usage = {}
        def __init__(self, *a, **kw): pass
        def close(self): pass
    monkeypatch.setattr('core.llm.LLMClient', Client)
    def cancel(*a): raise CancelledError('cancel')
    monkeypatch.setattr(strategy, 'select', cancel)
    with pytest.raises(CancelledError):
        strategy.prepare_report({'_file':'monitoring/ai_flash_discovery', 'content_strategy':strategy.MARKER},
                                '原稿', '', object())


def test_flash_keeps_more_than_four_candidates_and_issue_title(llm):
    from core.llm import LLMClient
    from tests.test_audience_rollout import chosen, MATERIAL
    base = chosen().candidates[0]
    selection = strategy.Selection(candidates=[base.model_copy(update={'title':f'独立发布 {i}'}) for i in range(11)], tradeoffs='')
    llm.replies = [selection.model_dump_json()] * 2
    report = '# 2026-11-16 晚间 AI 快讯\n原始报告'
    result = strategy.select(LLMClient(object()), 'flash', strategy.packets(MATERIAL), report)
    rendered = strategy.render_report(result, strategy.packets(MATERIAL), 'flash', original_report=report)
    assert len(result.candidates) == 11 and rendered.startswith(report.splitlines()[0]+'\n')
    assert '最多4个' not in llm.calls[0][0]['content']
    assert '数量由本期材料决定' in llm.calls[0][1]['content']


@pytest.mark.parametrize('text,expected', [
    ('# 选题说明（写在前面）\n分析\n# 真正主问题\n正文', '真正主问题'),
    ('# 选题说明\n分析\n## 本期主问题：怎样判断充电速度\n正文', '怎样判断充电速度'),
    ('# 2026-11-16 早间快讯\n## 第一条', '2026-11-16 早间快讯'),
])
def test_all_report_title_consumers_agree(tmp_path, text, expected):
    from web.indexer.scanner import extract_title
    p = tmp_path/'2026-11-16_issue.md'; p.write_text(text)
    assert extract_title(str(p)) == reports.title_of(str(p)) == expected
    assert knowledge_job.past_titles({'output':'{date}_issue.md'}, str(tmp_path)) == [expected]


@pytest.mark.parametrize('field', ['subject', 'summary'])
def test_tutorial_missing_event_field_repairs_and_releases_client(video_env, llm, field):
    from core.video import story_pool
    from tests.test_video_story_pool import event
    bad = event(); bad.pop(field)
    llm.replies = [json.dumps({'events':[bad]}), json.dumps({'events':[event()]})]
    packets = story_pool.packets('### 教程\nhttps://example.com/tutorial')
    events, usage = story_pool.organize('教程原稿', packets, [], object(), tutorial=True)
    assert len(events) == 1 and usage['requests'] == 2
    assert field in llm.calls[1][-1]['content']


def test_tutorial_persistent_format_failure_saves_actual_response(video_env, llm):
    from core.video import story_pool
    llm.replies = [json.dumps({'events':[{'title':'操作教程'}]})] * 3
    with pytest.raises(story_pool.SelectionError, match='subject, summary'):
        story_pool.organize('原教程', story_pool.packets('### 教程\nhttps://example.com/tutorial'), [], object(), tutorial=True)
    diagnostics = list((Path(store.db_path()).parent/'story-pool-errors').glob('*.json'))
    assert len(diagnostics) == 1
    saved = json.loads(diagnostics[0].read_text())
    assert len(saved['attempts']) == 3 and json.loads(saved['attempts'][0]['reply'])['events'][0]['title'] == '操作教程'
    original = diagnostics[0].read_bytes()
    llm.replies = [json.dumps({'events':[{'title':'操作教程'}]})] * 3
    with pytest.raises(story_pool.SelectionError):
        story_pool.organize('原教程', story_pool.packets('### 教程\nhttps://example.com/tutorial'), [], object(), tutorial=True)
    assert len(list(diagnostics[0].parent.glob('*.json'))) == 2
    assert diagnostics[0].read_bytes() == original



@pytest.mark.parametrize('all_covered', [True, False])
def test_split_selected_question_keeps_whole_question_and_coverage(video_env, llm, all_covered):
    from core.video import story_pool
    from tests.test_video_story_pool import event
    packets = story_pool.packets('### 甲产品\nhttps://example.com/a\n\n### 乙产品\nhttps://example.com/b')
    brief = {'title':'甲乙两款产品怎样选择', 'promise':'理解两种选择的区别'}
    previous = [{'ref':'H1','script_id':'previous','title':brief['title'],'sources':[]}]
    llm.replies = [json.dumps({'events':[event(title='只说甲', same_as=['H1'] if all_covered else []), event(title='只说乙', same_as=['H1'])]})]
    events, usage = story_pool.organize('比较底稿', packets, previous, object(), audience_brief=brief)
    assert len(events) == 1 and usage['requests'] == 1
    assert events[0]['title'] == events[0]['subject'] == brief['title']
    assert set(events[0]['primary']) == {'S1','S2'}
    assert events[0]['previous'] == (['previous'] if all_covered else [])
    assert events[0]['changes'] == []


@pytest.mark.parametrize('typed,recipient,allowed', [
    ('把报告发给 notboss@example.com', 'boss@example.com', False),
    ('把报告发给 boss@example.com.evil', 'boss@example.com', False),
    ('把报告发给 boss+reports@example.com', 'reports@example.com', False),
    ('发给 Boss@Example.com。', 'boss@example.com', True),
    ('发给 <boss+reports@example.com>', 'boss+reports@example.com', True),
])
def test_report_email_matches_full_user_address(monkeypatch, typed, recipient, allowed):
    from types import SimpleNamespace
    import web.deps as deps
    from web.services.assistant_tools import reports as tool
    monkeypatch.setattr(deps, 'require_editor', lambda request, user=None: None)
    sent=[]
    monkeypatch.setattr(tool.reports, 'email_report', lambda body, request, user=None: sent.append(body) or {'ok':True})
    request=SimpleNamespace(state=SimpleNamespace(assistant_message=typed))
    args=SimpleNamespace(action='email',path='output/test.md',recipients=[recipient],ttl_hours=24,jti='')
    if allowed:
        assert tool.distribute(args,request,{'role':'editor'})['ok']
        assert sent[0]['to'] == [recipient]
    else:
        with pytest.raises(ValueError): tool.distribute(args,request,{'role':'editor'})
        assert sent == []


@pytest.mark.parametrize('action', ['create', 'tutorial', 'set_primary_folder', 'configure', 'release', 'analysis', ''])
def test_tutorial_action_is_operation_analysis_not_a_generated_news_enum(video_env, llm, action):
    from core.video import story_pool
    from tests.test_video_story_pool import event
    llm.replies = [json.dumps({'events':[event(action=action)]})]
    events, usage = story_pool.organize('原教程', story_pool.packets('### 教程\nhttps://example.com/tutorial'), [], object(), tutorial=True)
    assert events[0]['action'] == 'analysis' and usage['requests'] == 1
    assert '"action":"analysis"' in llm.calls[0][0]['content']


@pytest.mark.parametrize('persistent', [False, True])
def test_publication_queue_read_retries_transient_open_failure_without_losing_receipts(tmp_path, monkeypatch, persistent):
    import sqlite3
    from core.video import content_guard
    path = tmp_path/'platforms.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE uploads (script_id TEXT, status TEXT, bvid TEXT)')
        conn.execute("INSERT INTO uploads VALUES ('already-uploading', 'QUEUED', '')")
    real_connect = sqlite3.connect
    attempts, closed = [], []
    class Unavailable:
        def execute(self, *a): raise sqlite3.OperationalError('unable to open database file')
        def close(self): closed.append(True)
    def connect(*args, **kwargs):
        attempts.append(args)
        if persistent or len(attempts) == 1:
            return Unavailable()
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    if persistent:
        with pytest.raises(sqlite3.OperationalError): content_guard.upload_protected(tmp_path)
        assert len(attempts) == len(closed) == 3
    else:
        assert content_guard.upload_protected(tmp_path) == {'already-uploading'}
        assert len(attempts) == 2 and len(closed) == 1
