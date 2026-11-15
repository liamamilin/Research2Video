import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from core.config import load_job
from core.video import audience_strategy as strategy, audience_profiles as profiles, production, reports, writer
from tests.test_audience_rollout import chosen, MATERIAL, URL, video_env, llm  # noqa: F401
from tests.test_video_scripts import script_json
from core import report_meta
from core.video import store
from web.services import video_workflow as workflow

ROOT = Path(__file__).resolve().parents[1]


def test_all_content_jobs_and_production_bindings_are_covered_without_enabling_auto():
    assert len(profiles.JOBS) == 45
    bindings = production.load_all(str(ROOT/'config'), str(ROOT/'jobs'))
    for job, channel in profiles.JOBS.items():
        loaded = load_job(str(ROOT/'jobs'),job)
        assert strategy.job_channel(loaded) == channel
        assert channel in profiles.CHANNELS
        own = [p for p in bindings.values() if p.job == job and (p.content_column_id or p.auto)]
        assert own or channel in profiles.KNOWLEDGE
        assert all(profiles.PRODUCTIONS.get(p.key) == channel for p in own)
    for job in ('practical_ai_intelligence/00_collection_planner','source_discovery/science',
                'system/material_collection','research/assistant_ops_acceptance_20261025'):
        assert not strategy.job_channel(load_job(str(ROOT/'jobs'),job))


def test_single_question_keeps_original_explanation_and_source_material(monkeypatch):
    value = chosen();value.candidates *= 3
    monkeypatch.setattr(strategy,'call_json',lambda *a,**k:value.model_copy(deep=True))
    result = strategy.select(None,'science_weekly',strategy.packets(MATERIAL),'原有主问题')
    assert len(result.candidates)==1
    original='# 为什么会这样\n## 实验过程\n原始长解释结尾 '+URL
    report = strategy.render_report(result,strategy.packets(MATERIAL),'science_weekly',original_report=original)
    assert '原始长解释结尾' in report
    assert report.count('### 1.1 ')==1 and '### 1.2 ' not in report
    assert '#### 实验过程' in report
    assert strategy.material_for_report(report,MATERIAL)==MATERIAL


def test_knowledge_job_preserves_document_and_does_not_turn_headlines_into_evidence(monkeypatch):
    monkeypatch.setattr('core.llm.LLMClient',lambda *a,**k:pytest.fail('knowledge document must not be reselected from headlines'))
    original='# 手机为什么发热\n## 核心机制\n原稿完整过程'
    report, receipt=strategy.prepare_report({'_file':'consumer/consumer_illustrated','content_strategy':strategy.MARKER},
                                           original,'往期标题与选题参考',None)
    assert report==original and receipt['selection_mode']=='knowledge_dossier'
    assert receipt['usage']=={}


def test_knowledge_whole_report_marker_reaches_original_writer(video_env, llm, monkeypatch):
    monkeypatch.chdir(video_env)
    path=video_env/'output/knowledge.md';path.parent.mkdir(exist_ok=True)
    path.write_text('# 手机为什么发热\n完整原稿里的原理和例子')
    report_meta.append_record('output/knowledge.md','consumer/consumer_illustrated',extra={
        'audience_strategy':{'strategy':strategy.MARKER,'channel':'consumer_illustrated'}})
    topic=reports.topic_for({'report_path':'output/knowledge.md'},str(video_env))
    assert topic['title']=='手机为什么发热' and topic['audience_strategy']=='consumer_illustrated'
    topic['from_report']='output/knowledge.md'
    row=store.create_script('report_digest',topic,production='consumer_illustrated')
    llm.replies=[script_json(300)]
    assert workflow.run(row['id'],llm_config=object())=='DRAFT'
    assert writer.SCOPE_CONCRETE_RULE in llm.calls[0][0]['content']
    assert '完整原稿里的原理和例子' in llm.calls[0][1]['content']
    assert '## 对应原始材料' not in llm.calls[0][1]['content']


def test_tutorial_keeps_steps_series_and_original_writer(monkeypatch):
    value=chosen();value.candidates[0].series='Qoder';value.candidates[0].topic_kind='怎么做一件事'
    value.candidates[0].demonstration='同一份输入，打开入口，执行动作，检查输出'
    text=strategy.render_report(value,strategy.packets(MATERIAL),'ai_tutorial')
    from core.video import topic_store
    item=topic_store.parse_items(text,'2026-11-15','ATU')[0]
    assert item.fields['系列']=='Qoder' and item.fields['题材分类']=='怎么做一件事'
    assert '同一份输入' in item.fields['操作与演示']
    old=writer.author_system_prompt({'content_mode':'episode'},{},tutorial=True)
    new=writer.author_system_prompt({'content_mode':'episode','preserve_audience_scope':True,
                                    'audience_strategy':'t03'},{},tutorial=True)
    assert new==old.replace(writer.CONCRETE_RULE,writer.SCOPE_CONCRETE_RULE,1)
    assert '保留实际步骤顺序' in new


def test_selected_tutorial_keeps_overview_and_section_links_without_unrelated_sources():
    value=chosen();material=f'### [操作]({URL}#step)\n具体步骤'
    text=strategy.render_report(value,strategy.packets(material),'ai_tutorial',
        original_report=f'具体操作 {URL}#step\n概览 {URL}\n另一页 https://example.org/unrelated')
    from core.video import topic_store
    item=topic_store.parse_items(text,'2026-11-15','ATU')[0]
    assert item.fields['来源网址'].splitlines()==[URL+'#step',URL]
    assert 'https://example.org/unrelated' not in text


def test_no_new_topic_result_does_not_reselect_or_generate_empty_whole_episode(monkeypatch):
    monkeypatch.setattr('core.llm.LLMClient',lambda *a,**k:pytest.fail('do not revive an explicitly empty research result'))
    text,receipt=strategy.prepare_report({'_file':'company/company_daily','content_strategy':strategy.MARKER},
                                        '采集范围：本期已有材料\n本次暂无新题材',MATERIAL,None)
    assert receipt['candidate_count']==0
    from core.video import topic_store
    monkeypatch.setattr(report_meta,'get',lambda path:{'audience_strategy':receipt})
    assert not topic_store.report_has_content(None,'output/empty.md','/tmp')
    monkeypatch.setattr(report_meta,'get',lambda path:{})
    assert topic_store.report_has_content(None,'output/old.md','/tmp')


def test_platform_discovery_preserves_editorial_order_and_real_metrics(monkeypatch):
    from core import theme_discovery,theme_ranking
    monkeypatch.setattr(theme_ranking,'rank',lambda actual,r:{'score':actual[0]['metrics']['views']})
    category=[{'key':'tools','audience':'工作与学习用户'}]
    signals=[{'id':'s1','category':'tools','metrics':{'views':10}},
             {'id':'s2','category':'tools','metrics':{'views':99999}}]
    raw=json.dumps({'themes':[{'category':'tools','title':'普通用户怎样处理长文件','reason':'共同问题','source_ids':['s1']},
                              {'category':'tools','title':'超热门专业内核优化','reason':'专业演示','source_ids':['s2']}]})
    new=theme_discovery.select(raw,category,signals,audience_first=True)
    assert new[0]['title']=='普通用户怎样处理长文件'
    assert new[0]['signals'][0]['metrics']=={'views':10}
    assert theme_discovery.select(raw,category,signals)[0]['title']=='超热门专业内核优化'


def test_all_job_yaml_and_source_supply_entries_keep_their_contracts():
    for job,channel in profiles.JOBS.items():
        data=yaml.safe_load((ROOT/'jobs'/(job+'.yaml')).read_text())
        assert isinstance(data['prompt'],str) and data['output']
        if channel in profiles.KNOWLEDGE:
            assert data['knowledge'] is True and data['search'] is False
            assert '## 核心机制' in data['prompt']
    for p in (ROOT/'config/source_supply').glob('*.yaml'):
        for job,row in yaml.safe_load(p.read_text())['jobs'].items():
            assert isinstance(row['query'],(str,list)),job
