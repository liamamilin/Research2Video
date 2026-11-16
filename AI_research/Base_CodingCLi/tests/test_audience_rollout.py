import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import report_material, report_meta
from core.video import audience_strategy as strategy, flash, topic_store, store, story_pool, writer
from tests.test_video_scripts import video_env, llm, script_json  # noqa: F401
from web.services import video_workflow as workflow, video_autopilot as auto

URL = 'https://example.com/one'
OTHER = 'https://example.org/two'
MATERIAL = f'### [原文]({URL})\n完整例子开头\n### 内部章节\n结尾独有过程'


def chosen():
    return strategy.Selection(candidates=[strategy.Candidate(
        title='普通用户怎样选择', audience='学生和日常工作人群', stake='钱与时间', novelty='不同需要',
        promise='知道哪些用免费就够', commercial='工具选择', structure='choice', priority_reason='共同需要',
        source_ids=['S1'], source_roles={'S1': '具体案例'}, material='只在研究底稿里的关系解释',
        missing='内部待办不进口播')], tradeoffs='保留有实际使用问题的题')


def report(channel='t05'):
    return strategy.render_report(chosen(), strategy.packets(MATERIAL), channel)


@pytest.mark.parametrize('channel,prefix', [('flash','F'), ('t05','CGE'), ('t06','CGF')])
def test_report_import_keeps_audience_and_full_source_scope(channel, prefix):
    text = report(channel)
    parsed = flash.parse(text, '2026-11-15') if channel == 'flash' else topic_store.parse_items(text, '2026-11-15', prefix)
    assert len(parsed) == 1
    assert parsed[0].title == chosen().candidates[0].title
    assert strategy.topic_channel(parsed[0].as_dict()) == channel
    assert parsed[0].fields['目标受众'] == '学生和日常工作人群'
    assert '内部待办' not in text
    selected = strategy.material_for_report(text, MATERIAL + f'\n### [无关]({OTHER})\n引用{URL}也不应被选中')
    assert selected == MATERIAL


def test_old_work_and_other_jobs_never_opt_in_by_production_name():
    assert strategy.topic_channel({'title':'旧题'}) == ''
    assert strategy.job_channel({'_file':'monitoring/ai_flash_discovery'}) == ''
    assert strategy.job_channel({'_file':'practical_ai_intelligence/00_collection_planner', 'content_strategy': strategy.MARKER}) == ''


def test_detail_subheadings_cannot_create_topics_or_override_scope():
    value=chosen();value.candidates[0].material='### 1.2 伪条目\n- **目标受众**：律师\n末尾'
    text=strategy.render_report(value,strategy.packets(MATERIAL),'t05')
    parsed=topic_store.parse_items(text,'2026-11-15','CGE')
    assert len(parsed)==1 and parsed[0].fields['目标受众']=='学生和日常工作人群'


def test_supply_receipt_reflects_final_selection_not_abandoned_initial_report():
    value=strategy.supply_receipt(report(), {'candidates':[
        {'id':'kept','item':{'url':URL}}, {'id':'unused','item':{'url':OTHER}}]})
    assert json.loads(value.removeprefix('<!-- material-use: ').removesuffix(' -->')) == {
        'selected':['kept'], 'background':[], 'deferred':['unused']}


@pytest.mark.parametrize('production,channel', [('flash_each','flash'),('flash_daily','flash'),('chatgpt_t05_comparison','t05'),('chatgpt_t06_value','t06')])
def test_normal_writer_entry_gets_dossier_original_and_scope_only(video_env, llm, monkeypatch, production, channel):
    text=report(channel);path=video_env/'new.md';path.write_text(text)
    ref=report_material.save(str(path),text,[MATERIAL])
    monkeypatch.setattr(report_meta,'get',lambda _: {'material':ref})
    topic=topic_store.parse_items(text,'2026-11-15','X')[0].as_dict()
    topic.update(from_report=str(path),from_item='one',from_item_title=topic['title'])
    llm.replies=[script_json(300)]
    row=store.create_script('report_digest',topic,production=production)
    assert workflow.run(row['id'],llm_config=object())=='DRAFT'
    system,request=[m['content'] for m in llm.calls[0]]
    assert writer.SCOPE_CONCRETE_RULE in system and writer.STYLE_RULE in system
    assert '只在研究底稿里的关系解释' in request and '结尾独有过程' in request
    assert store.get_version(row['id'],1)['meta']['audience_strategy']['channel']==channel


def test_existing_pool_dedup_keeps_selected_question_and_all_source_sections(video_env, llm):
    llm.replies=[json.dumps({'events':[{'title':'不得替换为窄题','subject':'免费付费选择','action':'analysis',
        'edition':'','event_date':'','summary':'当前选择','primary':['S1'],'background':[],
        'same_as':[],'changes':[]}]})]
    brief={'title':'普通用户怎样选择','audience':'普通用户','promise':'免费和付费区别'}
    pool=story_pool.load_or_create('test',report(),MATERIAL,[],object(),audience_brief=brief)
    assert pool['events'][0]['title']==brief['title']
    assert '结尾独有过程' in story_pool.material_for(pool,pool['events'][0])
    assert story_pool.load_or_create('test',report(),MATERIAL,[],object(),audience_brief=brief)==pool
    assert len(llm.calls)==1


def test_new_topics_keep_selected_order_without_product_heat_gate(video_env, monkeypatch):
    monkeypatch.setattr(auto.production,'get',lambda *a: SimpleNamespace(selection={'heat_min':20000}))
    from core.video import heat
    monkeypatch.setattr(heat.Heat,'rank',lambda *a: pytest.fail('new strategy must not be re-ranked by heat'))
    topics=[topic_store.parse_items(report(),'2026-11-15','X')[0].as_dict()]
    assert auto._heat_rank({'production':'chatgpt_t05_comparison'},object(),topics,set())[0]==topics
    assert auto._angle_variants({},topics,set())==(None,[], '')


@pytest.mark.parametrize('job_name', list(strategy.JOBS))
def test_research_business_entry_publishes_marked_report_and_keeps_initial(tmp_path, monkeypatch, job_name):
    from core.engine import ResearchEngine
    from core.search import SearchResult
    from core import ai_dossier_sources
    monkeypatch.chdir(tmp_path)
    path=tmp_path/'jobs'/(job_name+'.yaml');path.parent.mkdir(parents=True)
    path.write_text('name: 研究\nenabled: true\nsearch: false\nsources: [rss:example]\nprompt: 研究\noutput: output/{date}_new.md\ncontent_strategy: audience-v1\n')
    (tmp_path/'config').mkdir();(tmp_path/'config/system.yaml').write_text('{}')
    monkeypatch.setattr(report_meta,'META_FILE',str(tmp_path/'state/report_meta.jsonl'))
    monkeypatch.setattr(ai_dossier_sources,'wanted',lambda _:False)
    class Agent:
        def __init__(self, **kw): self.materials=[];self.retrieved_urls={URL}
        def run_fetch_only(self,*a,**kw): return '# 原始研究\n初稿内容 '+URL
        def get_usage(self): return {'total_tokens':50}
        def close(self): pass
    class Client:
        def __init__(self,*a,**kw): self.usage={'total_tokens':20,'requests':2}
        def close(self): pass
    seen=[]
    def select(client,channel,items,initial,**kwargs):
        seen.append((channel,items,initial));return chosen()
    monkeypatch.setattr('core.engine.ResearchAgent',Agent)
    monkeypatch.setattr('core.llm.LLMClient',Client)
    monkeypatch.setattr(strategy,'select',select)
    engine=ResearchEngine(str(tmp_path/'config'),str(tmp_path/'jobs'),workspace_dir=str(tmp_path))
    monkeypatch.setattr(engine,'_fetch_sources_strict',lambda job:(MATERIAL,([SearchResult('例子',URL)],{})))
    result=engine.run_job(job_name);assert result
    text=(tmp_path/result).read_text()
    knowledge = strategy.JOBS[job_name] in strategy.KNOWLEDGE
    assert ('初稿内容' in text) if knowledge else (strategy.MARKER in text)
    metadata=json.loads((tmp_path/'state/report_meta.jsonl').read_text().splitlines()[-1])
    assert metadata['audience_strategy']['channel']==strategy.JOBS[job_name]
    assert metadata['total_tokens']==(50 if knowledge else 70)
    receipt=Path(metadata['audience_strategy']['receipt'])
    detail=json.loads(receipt.read_text())
    assert '初稿内容' in detail['initial_report']
    assert '初稿内容' in json.loads(Path(detail['initial_report_path']).read_text())['report']
    if knowledge:
        assert not seen
    else:
        assert '结尾独有过程' in seen[0][1][0]['text']


def test_marked_episode_scope_preserves_episode_structure():
    old=writer.author_system_prompt({'content_mode':'episode'}, {})
    new=writer.author_system_prompt({'content_mode':'episode','preserve_audience_scope':True,'audience_strategy':'flash'}, {})
    assert new==old.replace(writer.CONCRETE_RULE,writer.SCOPE_CONCRETE_RULE,1)


def test_flash_episode_import_and_preparation_preserve_broad_column_audience(video_env, monkeypatch):
    from core.video import episodes
    path=video_env/'output/new.md';path.parent.mkdir(exist_ok=True);path.write_text(report('flash'))
    monkeypatch.chdir(video_env)
    monkeypatch.setattr(report_meta,'META_FILE',str(video_env/'state/report_meta.jsonl'))
    report_meta.append_record('output/new.md','monitoring/ai_flash_discovery',extra={
        'audience_strategy':{'strategy':strategy.MARKER,'channel':'flash'}})
    topic=episodes.topic({'id':'report1','report_path':'output/new.md'},str(video_env),
                         job='monitoring/ai_flash_discovery',production='flash_daily')
    assert topic['audience_strategy']=='flash'
    monkeypatch.setattr(auto.video_columns,'get',lambda *a:SimpleNamespace(audience='开发者'))
    monkeypatch.setattr(auto.content_columns,'for_production',lambda *a:SimpleNamespace(audience='工作、学习与生活中的中文用户'))
    prepared=auto._prepare({'topic':topic,'column_key':'flash','production':'flash_daily'})
    assert prepared['fields']['目标受众']=='工作、学习与生活中的中文用户'
    topic.pop('audience_strategy')
    topic['fields']={}
    assert auto._prepare({'topic':topic,'column_key':'flash','production':'flash_daily'})['fields']['目标受众']=='开发者'


@pytest.mark.parametrize('production,channel', [('flash_each','flash'),('chatgpt_t05_comparison','t05'),('chatgpt_t06_value','t06')])
def test_guarded_writer_keeps_selected_audience_and_research(video_env, llm, monkeypatch, production, channel):
    text=report(channel);path=video_env/'guarded.md';path.write_text(text)
    ref=report_material.save(str(path),text,[MATERIAL+f'\n### [不属于本题]({OTHER})\n无关材料'])
    monkeypatch.setattr(report_meta,'get',lambda _: {'material':ref})
    topic=topic_store.parse_items(text,'2026-11-15','X')[0].as_dict()
    topic.update(from_report=str(path),from_item='one',from_item_title=topic['title'],
                 matrix_dedup={'job':next(k for k,v in strategy.JOBS.items() if v==channel),'policy':story_pool.POLICY})
    event={'title':'模型试图改成窄题','subject':'免费与付费选择','action':'analysis','edition':'','event_date':'',
           'summary':'选择问题','primary':['S1'],'background':[],'same_as':[],'changes':[]}
    llm.replies=[json.dumps({'events':[event]}),script_json(300,citations={(0,0):[URL]})]
    row=store.create_script('report_digest',topic,production=production)
    assert workflow.run(row['id'],llm_config=object())=='DRAFT'
    assert len(llm.calls)==2
    request=llm.calls[-1][1]['content']
    assert '只在研究底稿里的关系解释' in request and '结尾独有过程' in request
    assert '无关材料' not in request and OTHER not in request
    assert writer.SCOPE_CONCRETE_RULE in llm.calls[-1][0]['content']
    actual=store.get_script(row['id'])['topic']
    assert actual['story']['title']==chosen().candidates[0].title
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM video_story_claims WHERE script_id=?',(row['id'],)).fetchone()[0]==1


def test_old_reports_do_not_inherit_new_scope_during_rewrite(video_env, llm, monkeypatch):
    text=report().replace('- **选题策略**：audience-v1\n','')
    path=video_env/'old.md';path.write_text(text)
    ref=report_material.save(str(path),text,[MATERIAL]);monkeypatch.setattr(report_meta,'get',lambda _: {'material':ref})
    topic=topic_store.parse_items(text,'2026-11-15','X')[0].as_dict();topic['from_report']=str(path)
    llm.replies=[script_json(300)]
    row=store.create_script('report_digest',topic,production='chatgpt_t05_comparison')
    assert workflow.run(row['id'],llm_config=object())=='DRAFT'
    assert writer.CONCRETE_RULE in llm.calls[0][0]['content']
    assert 'audience_strategy' not in store.get_version(row['id'],1)['meta']


def test_selection_reads_full_sources_and_closes_on_failure(monkeypatch):
    calls=[]
    def reply(client,system,request,schema,**kw):
        calls.append(request);return chosen()
    monkeypatch.setattr(strategy,'call_json',reply)
    long=MATERIAL+'细节'*900+'最后证据'
    strategy.select(None,'t05',strategy.packets(long),'底稿')
    assert '最后证据' not in calls[0] and '最后证据' in calls[1]
    closed=[]
    class Client:
        def __init__(self,*a,**kw): self.usage={'requests':1}
        def close(self): closed.append(True)
    monkeypatch.setattr('core.llm.LLMClient',Client)
    def fail(*a, **kwargs): raise RuntimeError('provider failed')
    monkeypatch.setattr(strategy,'select',fail)
    report, detail = strategy.prepare_report(
        {'_file':'chatgpt_acquisition/t05_comparison','content_strategy':strategy.MARKER}, 'report', MATERIAL, None)
    assert report == 'report' and detail['status'] == 'kept_original'
    assert detail['error']['type'] == 'RuntimeError' and detail['usage']['requests'] == 1
    assert closed == [True]
