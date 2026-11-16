"""Question-led acquisition keeps fixed topics, survives unreadable pages, and reaches authors."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.errors import CancelledError
from core.search import SearchResult
from core import report_material, report_meta
from core.video import question_material as qm, audience_strategy as strategy, topic_store, store
from tests.test_audience_rollout import chosen, MATERIAL, URL
from tests.test_video_scripts import video_env, llm, script_json  # noqa: F401
from web.services import video_workflow as workflow

NEW_URL = 'https://example.org/new-material'
NEW_TEXT = '独有的取消分享操作过程与结果'
SYSTEM = {'question_material': {'enabled': True}}
GAP = qm.Gap(question='取消之后会怎样', viewer_change='理解分享的生命周期', queries=['取消分享 操作结果'])


def job(channel='t05'):
    return {'_file': next(k for k, v in strategy.JOBS.items() if v == channel),
            'content_strategy': strategy.MARKER, 'question_material': qm.MARKER}


def detail(value=None):
    value = value or chosen()
    return {'candidate_count': len(value.candidates), 'selection': value.model_dump(),
            'initial_report': '# 完整底稿\n原始机制解释'}


@pytest.fixture
def providers(monkeypatch):
    calls = []
    class Client:
        def __init__(self, *a, **kw): self.usage = {'total_tokens': 7}; self.closed = False
        def close(self): self.closed = True
    class Search:
        calls = 0
        def __init__(self, *a, **kw): pass
        def search(self, *a, **kw):
            self.calls += 1
            return [SearchResult('新例子', NEW_URL, excerpts=[NEW_TEXT])]
    def model(client, system, user, schema, **kw):
        calls.append((schema, json.loads(user)))
        if schema is qm.Plan: return qm.Plan(gaps=[GAP])
        if schema is qm.Pick: return qm.Pick(ids=['P1'])
        return qm.Addition(addition='### 2. 不能另起选题\n- **目标受众**：律师\n'+NEW_TEXT,
                           used_ids=['E1'], contributions=['理解撤销结果'])
    monkeypatch.setattr(qm, 'LLMClient', Client)
    monkeypatch.setattr(qm, 'SearchClient', Search)
    monkeypatch.setattr(qm, 'call_json', model)
    monkeypatch.setattr(qm, 'fetch_page', lambda *a, **kw: [SearchResult('新例子', NEW_URL, excerpts=[NEW_TEXT])])
    return calls


def test_explicit_opt_in_and_inventory():
    root = Path(__file__).resolve().parents[1]
    system = yaml.safe_load((root/'config/system.yaml').read_text())
    enabled = []
    for path in (root/'jobs').rglob('*.yaml'):
        config = yaml.safe_load(path.read_text()) or {}
        if not isinstance(config, dict): continue
        config['_file'] = path.relative_to(root/'jobs').with_suffix('').as_posix()
        if qm.enabled(config, system): enabled.append(config['_file'])
    assert set(enabled) == set(strategy.JOBS)
    assert len(enabled) == 45
    old = job(); old.pop('question_material')
    assert not qm.enabled(old, SYSTEM)
    assert qm.enabled(job(), {})  # tracked defaults work in a fresh checkout
    assert not qm.enabled(job(), {'question_material': {'enabled': False}})
    assert not qm.enabled({**job(), '_file':'analysis/ai_trends'}, SYSTEM)


@pytest.mark.parametrize('channel', ['flash', 't05', 't06', 'ai_tutorial', 'science_weekly'])
def test_fixed_topics_and_tutorial_fields_survive_enrichment(providers, channel):
    value = chosen(); value.candidates[0].series='原系列'; value.candidates[0].demonstration='原操作示例'
    original = strategy.render_report(value, strategy.packets(MATERIAL), channel, original_report=detail()['initial_report'])
    report, raw, receipt = qm.enhance(job(channel), original, MATERIAL, detail(value), SYSTEM)
    after = receipt['selection']['candidates'][0]
    before = value.candidates[0].model_dump()
    assert {k: v for k, v in after.items() if k != 'material'} == {k: v for k, v in before.items() if k != 'material'}
    parsed = topic_store.parse_items(report, '2026-11-15', 'X')
    assert len(parsed) == 1 and parsed[0].fields['目标受众'] == before['audience']
    assert NEW_TEXT in raw and NEW_URL in report
    assert NEW_TEXT in strategy.material_for_report(report, MATERIAL+'\n'+raw)
    assert receipt['usage']['total_tokens'] == 7
    assert receipt['usage']['searches'] == 1
    if channel in strategy.SINGLE:
        assert report.count('原始机制解释') == 1


def test_knowledge_keeps_original_question_and_all_body(providers):
    original = '# 为什么充电停在八成\n\n## 电池原理\n已有完整解释'
    report, raw, receipt = qm.enhance(job('consumer_illustrated'), original, '无关新闻参考',
                                    {'candidate_count': 1}, SYSTEM)
    assert report.startswith(original+'\n\n## 补充解释与例子')
    assert NEW_TEXT in report and NEW_TEXT in raw
    assert providers[0][1]['original_material'] == original
    assert 'selection' not in receipt


@pytest.mark.parametrize("channel", ["t05", "science_weekly", "consumer_illustrated"])
def test_related_evidence_replaces_its_prose_instead_of_repeating_it(providers, monkeypatch, channel):
    old = "已有的描述没有区分两类充电条件，所以需要新增正文澄清。"
    merged = "两类条件分别对应不同充电环境。" + NEW_TEXT
    knowledge = channel in strategy.KNOWLEDGE
    original = "# 原题\n\n" + old + "\n \n## 保留章节\n\n" + URL
    selection = chosen()
    selection.candidates[0].material = old + "\n\n## 原稿其余内容"
    data = {"candidate_count": 1} if knowledge else detail(selection)
    prior = qm.call_json

    def model(client, system, user, schema, **kw):
        if schema is qm.Addition:
            paragraphs = json.loads(user)["editable_paragraphs"]
            assert list(paragraphs.values()) == [old]
            return qm.Addition(addition="", used_ids=["E1"], replacements={next(iter(paragraphs)): merged})
        return prior(client, system, user, schema, **kw)

    monkeypatch.setattr(qm, "call_json", model)
    report, raw, receipt = qm.enhance(job(channel), original, MATERIAL, data, SYSTEM)
    assert old not in report and report.count(merged) == 1
    assert NEW_TEXT in raw and NEW_URL in report and receipt["status"] == "enriched"
    if knowledge:
        assert report.startswith("# 原题\n\n" + merged + "\n \n## 保留章节\n\n" + URL)
    else:
        assert "原稿其余内容" in report
        if channel in strategy.SINGLE:
            assert "原始机制解释" in report


def test_invalid_replacement_keeps_the_previous_report(providers, monkeypatch):
    prior = qm.call_json

    def model(client, system, user, schema, **kw):
        if schema is qm.Addition:
            return qm.Addition(addition="", used_ids=["E1"], replacements={"B999": NEW_TEXT})
        return prior(client, system, user, schema, **kw)

    monkeypatch.setattr(qm, "call_json", model)
    original = "# 原题\n\n原有完整内容"
    report, raw, receipt = qm.enhance(job("consumer_illustrated"), original, MATERIAL,
                                    {"candidate_count": 1}, SYSTEM)
    assert (report, raw) == (original, "")
    assert receipt["topics"][0]["status"] == "kept_original"


def test_news_prose_with_inline_citations_can_be_merged_without_losing_links(providers, monkeypatch):
    old = "简报此前只说明该功能开始分批推出，原有公布范围见 " + URL
    selection = chosen()
    selection.candidates[0].material = old
    prior = qm.call_json

    def model(client, system, user, schema, **kw):
        if schema is qm.Addition:
            paragraphs = json.loads(user)["editable_paragraphs"]
            assert paragraphs == {"B1": old}
            return qm.Addition(addition="", used_ids=["E1"], replacements={"B1": NEW_TEXT})
        return prior(client, system, user, schema, **kw)

    monkeypatch.setattr(qm, "call_json", model)
    report, _, receipt = qm.enhance(job("flash"), "初选报告", MATERIAL, detail(selection), SYSTEM)
    assert receipt["selection"]["candidates"][0]["material"].startswith(NEW_TEXT + "\n" + URL)
    assert old not in report


def test_each_flash_candidate_keeps_its_own_replaced_paragraph(providers, monkeypatch):
    selection = chosen()
    first = selection.candidates[0]
    first.material = "第一件新闻的原始说明，发布范围还要用新材料补足。"
    second = first.model_copy(update={"title": "另一份产品发布公告", "material": "第二件新闻的原始说明，费用范围还要用新材料补足。"})
    selection.candidates.append(second)
    replacements = {first.title: "第一件新闻新增的上线范围", second.title: "第二件新闻新增的付费条件"}
    prior = qm.call_json

    def model(client, system, user, schema, **kw):
        if schema is qm.Addition:
            request = json.loads(user)
            assert list(request["editable_paragraphs"]) == ["B1"]
            value = replacements[request["topic"]["title"]]
            return qm.Addition(addition="", used_ids=["E1"], replacements={"B1": value})
        return prior(client, system, user, schema, **kw)

    monkeypatch.setattr(qm, "call_json", model)
    report, _, receipt = qm.enhance(job("flash"), "初选报告", MATERIAL, detail(selection), SYSTEM)
    assert [t["status"] for t in receipt["topics"]] == ["enriched", "enriched"]
    first_section, second_section = report.split("### 1.2 ")
    assert replacements[first.title] in first_section and replacements[second.title] in second_section
    assert replacements[second.title] not in first_section and replacements[first.title] not in second_section


@pytest.mark.parametrize('mode', ['off', 'empty', 'sufficient', 'no_value', 'bad_ids', 'error'])
def test_optional_stage_never_resurrects_empty_or_requires_success(providers, monkeypatch, mode):
    config = job(); data = detail()
    if mode == 'off': config.pop('question_material')
    if mode == 'empty': data['candidate_count'] = 0
    original_model = qm.call_json
    def model(client, system, user, schema, **kw):
        if mode == 'sufficient': return qm.Plan(gaps=[])
        if mode == 'error': raise RuntimeError('private-provider-detail')
        if schema is qm.Addition:
            return qm.Addition(addition='' if mode == 'no_value' else '错误引用', used_ids=[] if mode == 'no_value' else ['E99'])
        return original_model(client, system, user, schema, **kw)
    monkeypatch.setattr(qm, 'call_json', model)
    report, raw, receipt = qm.enhance(config, '原文', MATERIAL, data, SYSTEM)
    assert (report, raw) == ('原文', '')
    assert 'private-provider-detail' not in json.dumps(receipt)
    if mode in ('off', 'empty', 'sufficient'): assert not providers


def test_failed_page_uses_returned_body_and_keeps_trying_alternatives(monkeypatch):
    results = [SearchResult('失败', 'https://example.com/fail'),
               SearchResult('正文片段', 'https://example.com/excerpt', excerpts=['实际正文'*60]),
               SearchResult('替代正文', NEW_URL)]
    search = SimpleNamespace(search=lambda *a, **kw: results)
    reads = []
    def fetch(url, *a, **kw):
        reads.append(url)
        if url != NEW_URL: raise ValueError('unreadable')
        return [SearchResult('替代正文', url, excerpts=[NEW_TEXT])]
    monkeypatch.setattr(qm, 'fetch_page', fetch)
    monkeypatch.setattr(qm, 'call_json', lambda *a, **kw: qm.Pick(ids=['P1','P2','P3']))
    cache = {}
    added, attempts = qm._collect(None, search, [GAP], {}, cache, None, None)
    assert len(added) == 2 and len(reads) == 3
    assert added[0]['retrieval_mode'] == 'search_returned_body_excerpt'
    assert added[1]['retrieval_mode'] == 'page_sections'
    assert attempts[0]['reads'][0]['status'] == 'failed'
    qm._collect(None, search, [GAP], {}, cache, None, None)
    assert len(reads) == 3  # Even unreadable pages are not re-read for another topic.


def test_one_failed_topic_does_not_block_remaining_topics(providers, monkeypatch):
    value = chosen()
    value.candidates.append(value.candidates[0].model_copy(update={'title':'另一道日常问题'}))
    original = qm.call_json
    def model(client, system, user, schema, **kw):
        if schema is qm.Plan and json.loads(user)['topic']['title'] == value.candidates[0].title:
            raise ValueError('unusable model result')
        return original(client, system, user, schema, **kw)
    monkeypatch.setattr(qm, 'call_json', model)
    report, raw, receipt = qm.enhance(job(), '原文', MATERIAL, detail(value), SYSTEM)
    assert [t['status'] for t in receipt['topics']] == ['kept_original','enriched']
    parsed = topic_store.parse_items(report, '2026-11-15', 'X')
    assert [p.title for p in parsed] == [c.title for c in value.candidates]
    assert NEW_TEXT in raw


@pytest.mark.parametrize('ids', [['P1','P1'], ['P99']])
def test_invalid_picks_skip_without_fetching(monkeypatch, ids):
    monkeypatch.setattr(qm, 'call_json', lambda *a, **kw: qm.Pick(ids=ids))
    monkeypatch.setattr(qm, 'fetch_page', lambda *a, **kw: pytest.fail('invalid picks must not fetch'))
    search = SimpleNamespace(search=lambda *a, **kw: [SearchResult('例子',NEW_URL)])
    added, attempts = qm._collect(None, search, [GAP], {}, {}, None, None)
    assert not added and attempts[0]['error']['type'] == 'ValueError'


@pytest.mark.parametrize('where', ['plan', 'search', 'page', 'organization'])
def test_cancellation_always_propagates(providers, monkeypatch, where):
    def cancelled(*a, **kw): raise CancelledError('cancel')
    original = qm.call_json
    def model(client, system, user, schema, **kw):
        if (where == 'plan' and schema is qm.Plan) or (where == 'organization' and schema is qm.Addition): cancelled()
        return original(client, system, user, schema, **kw)
    monkeypatch.setattr(qm, 'call_json', model)
    if where == 'search': monkeypatch.setattr(qm.SearchClient, 'search', cancelled)
    if where == 'page': monkeypatch.setattr(qm, 'fetch_page', cancelled)
    with pytest.raises(CancelledError): qm.enhance(job(), '原文', MATERIAL, detail(), SYSTEM)


@pytest.mark.parametrize('job_name', list(strategy.JOBS))
def test_engine_saves_final_material_and_accounts_usage_once(tmp_path, monkeypatch, job_name):
    from core.engine import ResearchEngine
    from core import ai_dossier_sources
    monkeypatch.chdir(tmp_path)
    from core import state as state_module
    monkeypatch.setattr(state_module, '_STATE_DIR', str(tmp_path/'state'))
    monkeypatch.setattr(state_module, '_HISTORY_DIR', str(tmp_path/'state/history'))
    path = tmp_path/'jobs'/(job_name+'.yaml'); path.parent.mkdir(parents=True)
    path.write_text('name: 研究\nenabled: true\nsearch: false\nsources: [rss:example]\nprompt: 研究\noutput: output/{date}_new.md\ncontent_strategy: audience-v1\nquestion_material: question-v1\n')
    (tmp_path/'config').mkdir(); (tmp_path/'config/system.yaml').write_text('question_material:\n  enabled: true\n')
    monkeypatch.setattr(report_meta, 'META_FILE', str(tmp_path/'state/report_meta.jsonl'))
    monkeypatch.setattr(ai_dossier_sources, 'wanted', lambda _: False)
    class Agent:
        def __init__(self, **kw): self.materials=[]; self.retrieved_urls={URL}
        def run_fetch_only(self, *a, **kw): return '# 原始研究\n完整底稿 '+URL
        def get_usage(self): return {'total_tokens':50}
        def close(self): pass
    monkeypatch.setattr('core.engine.ResearchAgent', Agent)
    channel = strategy.JOBS[job_name]
    monkeypatch.setattr(strategy, 'prepare_report', lambda *a, **kw: ('原已选正文', {
        'strategy': strategy.MARKER, 'channel': channel, 'candidate_count':1, 'usage':{'total_tokens':20}}))
    def enhance(config, report, material, data, system, **kw):
        assert qm.enabled(config, system)
        assert report == '原已选正文' and '完整底稿' in data['initial_report']
        return report+'\n'+NEW_URL, SearchResult('新正文', NEW_URL, excerpts=[NEW_TEXT]).to_markdown(), {
            'status':'enriched','usage':{'total_tokens':7,'searches':2},
            'topics':[{'retrieved_sources':[{'url':NEW_URL,'text':NEW_TEXT}]}]}
    monkeypatch.setattr(qm, 'enhance', enhance)
    engine = ResearchEngine(str(tmp_path/'config'), str(tmp_path/'jobs'), workspace_dir=str(tmp_path))
    monkeypatch.setattr(engine, '_fetch_sources_strict', lambda job: (MATERIAL, ([SearchResult('原文',URL)], {})))
    result = engine.run_job(job_name); assert result
    text = (tmp_path/result).read_text()
    meta = json.loads((tmp_path/'state/report_meta.jsonl').read_text().splitlines()[-1])
    assert meta['total_tokens'] == 77
    assert meta['searches'] == 2 and NEW_URL in engine._prompt_urls
    assert meta['audience_strategy']['question_material'] == qm.MARKER
    receipt = json.loads(Path(meta['audience_strategy']['receipt']).read_text())
    assert receipt['question_material']['status'] == 'enriched'
    assert NEW_TEXT in json.dumps(receipt, ensure_ascii=False)
    from core.state import StateManager
    state = StateManager.get(job_name)
    from core import state as state_module
    history = Path(state_module._history_path(job_name)).read_text()
    assert NEW_TEXT not in json.dumps(state, ensure_ascii=False) and NEW_TEXT not in history
    compact = state['last_usage']['audience_strategy']
    assert compact['receipt'] == meta['audience_strategy']['receipt']
    assert compact['question_material_status'] == 'enriched'
    assert 'selection' not in compact and 'question_material' not in compact
    saved = report_material.read(meta['material'], str(tmp_path/result), text)
    assert NEW_TEXT in saved and '结尾独有过程' in saved


def test_normal_author_receives_used_full_body(video_env, llm, monkeypatch, providers):
    report, raw, _ = qm.enhance(job(), '原文', MATERIAL, detail(), SYSTEM)
    path = video_env/'new.md'; path.write_text(report)
    ref = report_material.save(str(path), report, [MATERIAL, raw])
    monkeypatch.setattr(report_meta, 'get', lambda _: {'material':ref})
    topic = topic_store.parse_items(report, '2026-11-15', 'X')[0].as_dict()
    topic.update(from_report=str(path), from_item='one', from_item_title=topic['title'])
    llm.replies = [script_json(300)]
    row = store.create_script('report_digest', topic, production='chatgpt_t05_comparison')
    assert workflow.run(row['id'], llm_config=object()) == 'DRAFT'
    assert NEW_TEXT in llm.calls[0][1]['content']
    assert '结尾独有过程' in llm.calls[0][1]['content']


@pytest.mark.parametrize('channel', sorted(strategy.KNOWLEDGE))
def test_knowledge_author_keeps_dossier_instead_of_replacing_it_with_gap_sources(video_env, llm, monkeypatch, channel):
    original = '# 降噪耳机怎样消除声音\n原始降噪主问题与完整机制\n'
    report = original+'\n## 补充解释与例子\n补充的风噪解释 '+NEW_URL
    raw = SearchResult('风噪附带的另一话题', NEW_URL, excerpts=['不能代替原主问题的完整网页']).to_markdown()
    path = video_env/'knowledge.md'; path.write_text(report)
    ref = report_material.save(str(path), report, [raw])
    monkeypatch.setattr(report_meta, 'get', lambda _: {'material':ref, 'audience_strategy':{
        'strategy':strategy.MARKER, 'channel':channel, 'question_material':qm.MARKER}})
    topic = {'title':'降噪耳机怎样消除声音','audience_strategy':channel,'from_report':str(path)}
    llm.replies = [script_json(300)]
    row = store.create_script('report_digest', topic, production=channel)
    assert workflow.run(row['id'], llm_config=object()) == 'DRAFT'
    request = llm.calls[0][1]['content']
    assert '原始降噪主问题与完整机制' in request and '补充的风噪解释' in request
    assert '不能代替原主问题的完整网页' not in request
    assert store.get_version(row['id'],1)['meta']['input_kind'] == 'report'
