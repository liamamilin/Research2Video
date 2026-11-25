from types import SimpleNamespace

import pytest

from core.video import audience_strategy as strategy, writer


def candidate(**patch):
    return strategy.Candidate(**({
        'title': '订阅到底买到了什么', 'audience': '已有订阅的普通用户', 'stake': '钱与使用权益',
        'novelty': '相同月费不同权益', 'promise': '知道哪些差异影响自己的使用',
        'commercial': '订阅选择，尚无转化数据', 'structure': 'choice', 'priority_reason': '共同需求',
        'source_ids': ['S1'], 'source_roles': {'S1': '比较背景'}, 'material': '套餐不同',
    } | patch))


def test_packets_keep_full_body_and_do_not_merge_distinct_sources():
    text = '采集说明\n### [甲](https://example.com/a)\n' + '完整正文' * 300 + '\n### [乙](https://example.com/b)\n第二例'
    items = strategy.packets(text)
    assert len(items) == 2
    assert items[0]['text'].endswith('完整正文' * 300)
    assert items[1]['id'] == 'S2'
    assert len(strategy.source_index(items, 80)) < 500
    assert strategy.author_material(candidate(), items) == items[0]['text']


@pytest.mark.parametrize('patch', [
    {'source_ids': ['S9']}, {'source_roles': {'S2': '未入选'}}
])
def test_unknown_source_or_role_does_not_reach_author(patch):
    selection = strategy.Selection(candidates=[candidate(**patch)], tradeoffs='')
    with pytest.raises(ValueError):
        strategy.validate_sources(selection, [{'id': 'S1'}])


def test_empty_selection_does_not_trigger_fulltext_call(monkeypatch):
    calls = []
    def reply(*args, **kwargs):
        calls.append(args)
        return strategy.Selection(candidates=[], tradeoffs='材料不足，暂不做')
    monkeypatch.setattr(strategy, 'call_json', reply)
    assert not strategy.select(None, 'flash', [], '').candidates
    assert len(calls) == 1


def test_second_pass_reads_complete_selected_source_only(monkeypatch):
    calls = []
    def reply(client, system, request, schema, **kwargs):
        calls.append(request)
        return strategy.Selection(candidates=[candidate()], tradeoffs='')
    monkeypatch.setattr(strategy, 'call_json', reply)
    text = '原文' * 600 + '结尾独有材料'
    items = [{'id':'S1', 'title':'A', 'url':'https://example.com/a', 'text':text},
             {'id':'S2', 'title':'B', 'url':'https://example.com/b', 'text':'未选全文'}]
    strategy.select(None, 't05', items, '研究报告')
    assert '结尾独有材料' not in calls[0]
    assert '结尾独有材料' in calls[1]
    assert '未选全文' not in calls[1]


def test_existing_author_path_unchanged_without_opt_in():
    assert writer.author_system_prompt({}, {}) == writer.SYSTEM_PROMPT
    assert writer.author_system_prompt({'content_mode': 'episode'}, {}) == writer.EPISODE_PROMPT


def test_brief_and_material_survive_recipe_field_filtering():
    topic = {'title':'选题', 'audience_brief':candidate().model_dump(), 'fields':{'目标受众':'普通用户'}}
    prompt = writer.author_system_prompt(topic, {})
    request = writer._request('报告', topic, '', 3, 0, topic_fields=('操作与演示',), original_material='完整入选原文')
    assert prompt == strategy.AUTHOR_RULES
    assert '知道哪些差异影响自己的使用' in request
    assert '订阅选择，尚无转化数据' not in request
    assert '约 3 分钟' in request
    assert '完整入选原文' in request
    assert '让同一个具体任务在中段持续推进' not in request


def test_missing_author_material_fails_instead_of_silent_drop():
    with pytest.raises(ValueError):
        strategy.author_material(candidate(), [])
