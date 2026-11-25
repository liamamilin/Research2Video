import pytest

from core.video import writer


@pytest.mark.parametrize('flag', [None, False, 'true', 1])
def test_existing_prompt_unchanged_without_explicit_boolean_opt_in(flag):
    assert writer.author_system_prompt({'preserve_audience_scope': flag}, {}) == writer.SYSTEM_PROMPT


def test_scope_edit_preserves_established_writing_rules_and_persona(monkeypatch):
    monkeypatch.setattr(writer.narrator, 'prompt', lambda persona: '\n讲述者标记')
    topic = {'preserve_audience_scope': True, 'next_episode': '下一期主题'}
    old = writer.author_system_prompt({'next_episode': '下一期主题'}, {}, greeting='固定问候')
    actual = writer.author_system_prompt(topic, {}, greeting='固定问候')
    assert actual == old.replace(writer.CONCRETE_RULE, writer.SCOPE_CONCRETE_RULE, 1)
    for rule in (writer.TITLE_RULE, writer.OPENING_RULE, writer.STYLE_RULE, writer.ENDING_RULE):
        assert rule in actual
    assert '若选题本来只面向专业人群' in actual


@pytest.mark.parametrize('tutorial', [False, True])
def test_episode_and_tutorial_prompts_unchanged(tutorial):
    old = writer.author_system_prompt({'content_mode': 'episode'}, {}, tutorial=tutorial)
    actual = writer.author_system_prompt({'content_mode': 'episode', 'preserve_audience_scope': True}, {}, tutorial=tutorial)
    assert actual == old


def test_scope_handoff_only_replaces_single_task_throughline():
    topic = {'title': '国内工具怎样搭配', 'fields': {'目标受众': '学生与日常办公用户'}}
    args = dict(column_style='原栏目风格', target_minutes=3, budget=0,
                topic_fields=('操作与演示',), original_material='完整原始材料及其结尾')
    old = writer._request('研究底稿', topic, **args)
    actual = writer._request('研究底稿', topic | {'preserve_audience_scope': True}, **args)
    assert actual == old.replace(
        '让同一个具体任务在中段持续推进，参数贴着它解释的作用出现，结尾回到任务的结果与本期形成的认识。',
        '中段沿本期问题展开，让具体例子中的动作和结果帮助目标观众理解；'
        '例子不限定整篇受众，结尾回到目标观众共同关心的问题与本期形成的认识。', 1)
    for value in ('约 3 分钟', '学生与日常办公用户', '原栏目风格', '完整原始材料及其结尾'):
        assert value in actual


def test_narrow_topic_is_not_relabelled_as_general_audience():
    topic = {'title': '研究生文献综述', 'fields': {'目标受众': '食品科学研究生'}, 'preserve_audience_scope': True}
    request = writer._request('真实论文案例', topic, '', 3, 0, original_material='论文原文')
    assert '## 目标观众\n食品科学研究生' in request
    assert '普通观众' not in request


def test_episode_and_report_only_requests_unchanged():
    for topic, material in [({'content_mode': 'episode'}, '多事件全文'), ({}, None)]:
        old = writer._request('底稿', topic, '', 3, 0, original_material=material)
        actual = writer._request('底稿', topic | {'preserve_audience_scope': True}, '', 3, 0, original_material=material)
        assert actual == old
