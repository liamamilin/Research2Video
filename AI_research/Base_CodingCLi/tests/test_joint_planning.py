"""Joint assignments use the original author and existing angle/history claims."""
import copy
import json

import pytest

from core import report_material, report_meta
from core.video import author_input_gates, joint_planning as joint, audience_strategy, store, content_guard, story_pool
from core.video import autopilot_store as ap
from web.services import video_autopilot as auto, video_workflow as workflow
from tests.test_audience_rollout import chosen
from tests.test_video_scripts import video_env, llm, script_json  # noqa: F401
from tests.test_video_story_pool import event


@pytest.fixture
def batch(video_env, monkeypatch):
    monkeypatch.chdir(video_env)
    urls = [f'https://example.com/release-{i}' for i in range(3)]
    material = '\n\n'.join(f'### [产品{i}]({url})\n产品{i}发布了新版本。安装命令 install product{i}，输入表格后点击导出，得到可编辑文件。' for i, url in enumerate(urls))
    selection = audience_strategy.Selection(candidates=[chosen().candidates[0].model_copy(update={
        'title': f'产品{i}的新发布', 'source_ids': [f'S{i+1}'], 'source_roles': {},
        'material': f'产品{i}的新变化及操作结果'}) for i in range(3)], tradeoffs='')
    text = audience_strategy.render_report(selection, audience_strategy.packets(material), 'flash', original_report='# AI快讯')
    path = video_env/'output/flash/2026-11-16_ai_flash_discovery.md'
    path.parent.mkdir(parents=True); path.write_text(text)
    relative = path.relative_to(video_env).as_posix()
    ref = report_material.save(str(path), text, [material])
    report_meta.append_record(relative, 'monitoring/ai_flash_discovery', extra={
        'material': ref, 'audience_strategy': {'strategy': audience_strategy.MARKER, 'channel': 'flash'}})
    spec = {'job': 'monitoring/ai_flash_discovery', 'report': relative, 'picks': [
        {'column': 'flash-nr', 'production': 'flash_daily', 'whole': True, 'content_mode': 'episode'},
        {'column': 'flash-nr', 'production': 'flash_each', 'source': 'flash', 'all': True}]}
    old = auto.pick_topics(spec)
    ids = [i['topic']['id'] for i in old[1:]]
    (video_env/'config/joint_planning.yaml').write_text('enabled: true\nepisode_production: flash_daily\nsingle_production: flash_each\n')
    plan = {'allocations': [{'candidate_id': cid, 'reason': '变化放合集，操作仅在有完整材料时独立展开',
                            'episode': {'summary': f'产品{i}支持表格导出，使用者能得到可编辑文件。', 'source_ids': [f'S{i+1}']},
                            'single': None} for i, cid in enumerate(ids)]}
    plan['allocations'][0]['single'] = {
        'question': '怎样把表格变成可编辑文件', 'audience': '需要整理和交付文件的工作与学习人群', 'angle': 'howto',
        'incremental_value': '跟着入口、输入和导出步骤完成一个文件', 'material': '安装命令 install product0，输入表格后点击导出。',
        'source_ids': ['S1']}
    return spec, plan, urls


def test_one_plan_shared_before_writing_and_cached(batch, video_env, llm):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    notes = []
    items = auto.pick_topics(spec, notes)
    assert len(items) == 2
    episode, single = items
    assert episode['topic']['joint_plan']['id'] == single['topic']['joint_plan']['id']
    assert single['topic']['angle'] == 'howto'
    assert single['topic']['id'].endswith('~howto')
    assert single['topic']['fields']['目标受众'] == plan['allocations'][0]['single']['audience']
    assert '安装命令' not in joint.assignment(episode['topic'])['report']
    assert '安装命令' in joint.assignment(single['topic'])['material']
    single_direction = joint.assignment(single['topic'])['direction']
    episode_direction = joint.assignment(episode['topic'])['direction']
    assert '怎样把表格变成可编辑文件' in single_direction
    assert '跟着入口、输入和导出步骤完成一个文件' in single_direction
    assert '产品0支持表格导出' not in single_direction
    assert '完整操作步骤与费用比较不在这里展开' in episode_direction
    author_input_gates.require_clean(single_direction)
    author_input_gates.require_clean(episode_direction)
    again = auto.pick_topics(spec)
    assert len(llm.calls) == 1 and again == items
    assert '合集 3 项，单条 1 条' in notes[-1]
    # Saved plans, including rewrites, survive configuration changes.
    (video_env/'config/joint_planning.yaml').write_text('enabled: false\n')
    assert joint.assignment(single['topic'])['report'].startswith('# 怎样')
    assert len(auto.pick_topics(spec)) == 4


def test_viewer_takeaway_drops_programme_split_and_assignment_rebuilds_old_direction(video_env):
    assert joint.viewer_takeaway('跟着入口完成导出') == '跟着入口完成导出'
    assert joint.viewer_takeaway(
        '合集只报功能上线；单条给出操作路径：点麦克风、点波形，以及隐私开关'
    ) == '操作路径：点麦克风、点波形，以及隐私开关'
    assert joint.viewer_takeaway(
        '合集只说明计费变了；单条进一步区分服务端工具与独立接口'
    ) == '区分服务端工具与独立接口'
    single = {
        'question': 'ChatGPT 实时听写怎么用',
        'incremental_value': '合集只报功能上线；单条给出点麦克风后边说边出字的操作',
        'audience': '常用语音输入的人', 'angle': 'howto', 'material': '点麦克风', 'source_ids': ['S1'],
    }
    assert joint.writing_direction(single) == (
        '本条只回答：ChatGPT 实时听写怎么用。观众能带走：点麦克风后边说边出字的操作。')
    assert '合集' not in joint.writing_direction(single)
    assert '发布概况' not in joint.writing_direction(role='episode')
    plan_id = 'a' * 64
    path = joint._path(plan_id)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        'id': plan_id, 'status': 'planned',
        'assignments': {
            'item-1': {
                'report': '# q', 'material': 'm', 'single': single,
                'direction': ('本条只回答：旧。观众收获：旧。同批合集仅介绍：旧摘要。'
                              '这条展开已分配的独立问题，发布概况只作必要背景。'),
            },
            'episode': {
                'report': '# 本期', 'material': '', 'items': [],
                'direction': '这是与单条共同规划的合集材料。按摘要讲。',
            },
        },
    }, ensure_ascii=False))
    rebuilt = joint.assignment({'joint_plan': {'id': plan_id, 'entry': 'item-1'}})
    assert rebuilt['direction'] == joint.writing_direction(single)
    assert '同批合集' not in rebuilt['direction']
    assert '旧摘要' not in rebuilt['direction']
    assert joint.assignment({'joint_plan': {'id': plan_id, 'entry': 'episode'}})['direction'] == (
        joint.writing_direction(role='episode'))


@pytest.mark.parametrize('legacy_report', [False, True])
def test_joint_episode_then_independent_single_keeps_external_history(batch, video_env, llm, legacy_report):
    spec, plan, urls = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    episode = store.create_script('flash-nr', items[0]['topic'], production='flash_daily')
    llm.replies = [json.dumps({'title': '今日快讯', 'sections': [
        {'title': '开场', 'episode_role': 'opening', 'paragraphs': [{'text': '今天介绍三项变化。', 'citations': []}]},
        *[{'title': f'产品{i}的新发布', 'episode_role': 'item', 'paragraphs': [{'text': f'产品{i}现在能导出可编辑文件。', 'citations': [urls[i]]}]} for i in range(3)],
        {'title': '结尾', 'episode_role': 'closing', 'paragraphs': [{'text': '本期变化就是这些。', 'citations': []}]}]})]
    assert workflow.run(episode['id'], llm_config=object()) == 'DRAFT'
    assert 'install product0' not in llm.calls[-1][1]['content']
    store.update_script(episode['id'], status='APPROVED')
    if legacy_report:
        items[1]['topic'].pop('audience_strategy', None)
        for key in ('选题策略', '选题入口'):
            items[1]['topic']['fields'].pop(key, None)
    single = store.create_script('flash-nr', items[1]['topic'], production='flash_each')
    llm.replies = [json.dumps({'events': [event(title='怎样把表格变成可编辑文件', subject='产品0', action='release', edition='v2', primary=['S1'])]}),
                   script_json(350, citations={(0, 0): [urls[0]]})]
    assert workflow.run(single['id'], llm_config=object()) == 'DRAFT'
    version = store.get_version(single['id'], 1)
    assert version['meta']['story']['angle'] == 'howto'
    assert version['meta']['joint_plan'] == items[1]['topic']['joint_plan']
    assert 'install product0' in llm.calls[-1][1]['content']
    assert 'install product1' not in llm.calls[-1][1]['content']
    assert '已选题见请求中的 audience_brief' in llm.calls[-2][0]['content']
    assert '"audience_brief"' in llm.calls[-2][1]['content']
    author_request = llm.calls[-1][1]['content']
    assert '本条只回答：怎样把表格变成可编辑文件' in author_request
    assert '观众能带走：跟着入口、输入和导出步骤完成一个文件' in author_request
    author_input_gates.require_clean(version['meta']['creative_direction'])
    assert '同批合集' not in author_request
    assert '发布概况只作必要背景' not in author_request
    content_guard.check(store.get_script(single['id']), version['sections'], str(video_env/'config'), str(video_env/'jobs'))
    # Existing how-to coverage is still protected; there is no blanket shared-plan bypass.
    story = version['meta']['story']
    peer = {'script_id': 'older-howto', 'title': story['title'], 'story': story, 'sources': set(story['primary_sources'])}
    assert story_pool.duplicate(story, [peer])
    assert not store.list_renders(single['id'])


@pytest.mark.parametrize('bad', ['bad_json', 'unknown_source', 'missing_candidate', 'shared_without_angle'])
def test_planner_format_failure_retries_then_retains_old_queue(batch, llm, bad):
    spec, plan, _ = batch
    broken = copy.deepcopy(plan)
    if bad == 'unknown_source': broken['allocations'][0]['episode']['source_ids'] = ['S999']
    if bad == 'missing_candidate': broken['allocations'].pop()
    if bad == 'shared_without_angle': broken['allocations'][0]['single']['angle'] = ''
    reply = 'not json' if bad == 'bad_json' else json.dumps(broken)
    llm.replies = [reply] * 3
    notes = []
    result = auto.pick_topics(spec, notes)
    assert len(result) == 4 and not any(i['topic'].get('joint_plan') for i in result)
    assert len(llm.calls) == 3
    assert '保留原选题流程' in notes[-1]


def test_planned_duplicate_does_not_replace_its_question_with_raw_topic(batch, llm):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    run = ap.create_run(spec, auto.pick_topics(spec))
    item = run['items'][1]
    auto._replace_candidate(item, {}, '已讲过同一操作')
    updated = ap.get_run(run['id'])['items'][1]
    assert updated['state'] == 'CANCELLED'
    assert not updated['topic'].get('_material_reselection')


def test_episode_only_batch_never_calls_joint_planner(batch, llm):
    spec, _, _ = batch
    spec['picks'] = spec['picks'][:1]
    assert len(auto.pick_topics(spec)) == 1
    assert not llm.calls


@pytest.mark.parametrize('keep_raw', [True, False])
def test_unused_material_is_planned_before_writing_and_keeps_heat_policy(batch, video_env, llm, monkeypatch, keep_raw):
    spec, plan, _ = batch
    path = video_env/spec['report']
    meta = report_meta.get(spec['report'])
    material = report_material.read(meta['material'], str(path), path.read_text())
    material += '\n\n### [另一个服务](https://example.com/other-service)\n新服务提供图片生成，按月收费 30 元，含 100 次生成，额外一次 1 元。'
    reference = report_material.save(str(path), path.read_text(), [material])
    report_meta.append_record(spec['report'], spec['job'], extra={'material': reference, 'audience_strategy': meta['audience_strategy']})
    plan['allocations'][0]['single'] = None
    calls = []
    def chat(self, messages, **kwargs):
        request = json.loads(messages[1]['content'])
        raw = [c for c in request['candidates'] if c.get('raw_material')]
        assert len(raw) == 1
        response = copy.deepcopy(plan)
        response['allocations'].append({'candidate_id': raw[0]['id'], 'reason': '报告未使用的独立费用变化', 'episode': None,
            'single': {'question': '生成图片每月花多少', 'audience': '需要生成图片的人', 'angle': '',
                       'incremental_value': '按使用频次选择套餐', 'material': '30 元包含 100 次，额外 1 元一次。',
                       'source_ids': [raw[0]['source_id']]}})
        from core.llm import ChatResponse
        return ChatResponse(content=json.dumps(response))
    monkeypatch.setattr(llm, 'chat', chat)
    def rank(pick, column, candidates, taken):
        if candidates and candidates[0]['id'].startswith('flash-raw:'):
            calls.append(candidates)
            assert not audience_strategy.topic_channel(candidates[0])
            return (candidates if keep_raw else []), '原始材料沿用热度设置'
        return candidates, ''
    monkeypatch.setattr(auto, '_heat_rank', rank)
    other = store.create_script('flash-nr', {'title': '另一条正在写的文稿'}, production='other')
    store.update_script(other['id'], status='WRITING')
    before = store.get_script(other['id'])
    notes = []
    items = auto.pick_topics(spec, notes)
    assert len(items) == (2 if keep_raw else 1)
    assert len(calls) == 1 and '原始材料沿用热度设置' in notes
    assert store.get_script(other['id']) == before
    if keep_raw:
        assert '另一个服务' in joint.assignment(items[1]['topic'])['material']
        assert 'product0' not in joint.assignment(items[1]['topic'])['material']


def test_malformed_joint_config_falls_back_without_model(batch, video_env, llm):
    spec, _, _ = batch
    (video_env/'config/joint_planning.yaml').write_text('enabled: [broken')
    notes = []
    assert len(auto.pick_topics(spec, notes)) == 4
    assert not llm.calls and '配置不可读' in notes[-1]


def test_history_keeps_every_question_without_repeating_full_manuscripts():
    peers = [{'script_id': str(i), 'title': f'已讲问题{i}', 'summary': '已讲收获' * 1000,
              'text': '完整旧稿' * 10000, 'story': {'angle': 'howto'}, 'sources': {'example.com'}} for i in range(120)]
    context = joint.history_context(peers)
    assert len(context) == 120
    assert all(len(row['summary']) <= 400 and row['angle'] == 'howto' for row in context)
    assert '完整旧稿' not in json.dumps(context, ensure_ascii=False)


def test_second_production_pair_uses_its_own_column_scope(batch, video_env, llm, monkeypatch):
    spec, plan, _ = batch
    config = video_env/'config/joint_planning.yaml'
    config.unlink()
    items = auto.pick_topics(spec)
    spec['job'] = 'chatgpt_acquisition/t09_updates'
    for item in items:
        item['production'] = ('chatgpt_t09_updates' if item['production'] == 'flash_daily' else 'chatgpt_t09_updates_each')
    config.write_text('enabled: true\npairs:\n  - episode_production: flash_daily\n    single_production: flash_each\n  - episode_production: chatgpt_t09_updates\n    single_production: chatgpt_t09_updates_each\n')
    from types import SimpleNamespace
    from core.video import content_columns
    monkeypatch.setattr(content_columns, 'for_production', lambda *_: SimpleNamespace(
        name='ChatGPT 动态', promise='ChatGPT 最近有什么变化'))
    llm.replies = [json.dumps(plan)]
    result = joint.apply(spec, items, str(video_env/'config'), str(video_env/'jobs'), str(video_env), lambda key: object())
    assert len(result) == 2
    assert result[1]['production'] == 'chatgpt_t09_updates_each'
    request = json.loads(llm.calls[0][1]['content'])
    assert 'T09' in request['column'] and 'ChatGPT' in request['scope']


def test_slow_joint_planner_does_not_block_other_batches_or_repeat(batch, monkeypatch):
    import threading
    from web.services import theme_workbench
    spec, _, _ = batch
    run = ap.create_run(spec, [])
    other = ap.create_run({}, [{"column_key": "flash-nr", "topic": {"id": "ongoing", "title": "已有任务"}}])
    entered, release = threading.Event(), threading.Event()
    calls, advanced = [], []
    def picking(spec, notes):
        calls.append(spec)
        entered.set()
        assert release.wait(5)
        notes.append("共同规划已完成")
        return [{"column_key": "flash-nr", "topic": {"id": "planned", "title": "独立问题"}}]
    monkeypatch.setattr(auto, "pick_topics", picking)
    monkeypatch.setattr(auto, "fresh_topics", lambda *_: True)
    for name in ("fire_auto", "fire_due_plans", "reopen_approved"):
        monkeypatch.setattr(auto, name, lambda: None)
    monkeypatch.setattr(story_pool, "reconcile", lambda: None)
    monkeypatch.setattr(theme_workbench, "enqueue_recommendations", lambda: None)
    monkeypatch.setattr(auto, "advance", lambda item, **kw: advanced.append(item['run_id']) or {"script": 0, "render": 0})
    key = (store.db_path(), run['id'])
    try:
        auto.tick()
        assert entered.wait(2)
        assert other['id'] in advanced and run['id'] not in advanced
        auto.tick()
        assert len(calls) == 1
        assert ap.get_run(run['id'])['stage'] == 'TOPICS'
        release.set()
        auto._topic_picks[key].result(timeout=5)
        auto.tick()
        assert ap.get_run(run['id'])['stage'] == 'ITEMS'
        assert len(ap.get_run(run['id'])['items']) == 1
        assert ap.get_run(run['id'])['note'] == '共同规划已完成'
        auto.tick()
        assert len(calls) == 1 and len(ap.get_run(run['id'])['items']) == 1
    finally:
        release.set()
        if key in auto._topic_picks:
            auto._topic_picks.pop(key).result(timeout=5)


def test_cancelled_planning_result_is_not_attached(batch, monkeypatch):
    spec, _, _ = batch
    run = ap.create_run(spec, [])
    monkeypatch.setattr(auto, 'pick_topics', lambda *_: ([], []))
    assert auto._background_topics(run) is None
    key = (store.db_path(), run['id'])
    auto._topic_picks[key].result(timeout=5)
    ap.update_run(run['id'], status='CANCELLED')
    other = ap.create_run(spec, [])
    other_key = (store.db_path(), other['id'])
    try:
        assert auto._background_topics(other) is None
        assert key not in auto._topic_picks
        assert not ap.get_run(run['id'])['items']
    finally:
        auto._topic_picks.pop(other_key).result(timeout=5)
