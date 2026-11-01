"""Material lifecycle, versioned intent and isolated preparation regressions."""
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import shutil
import pytest

from core import source_supply as supply, source_coverage, source_discovery
from core.material_archive import MaterialArchive
from core.search import SearchResult
from core.source_supply_store import SupplyStore
from core.video import store
from tests.test_source_supply import setup as source_setup, item, JOB
from tests.test_video_scripts import video_env, _approved_script
from web.services import assistant_materials, video_workflow, video_render, video_executor


def _no_video_threads(monkeypatch):
    import threading
    start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, 'start', lambda self: None if self.name.startswith(('video-restyle-', 'video-rewrite-')) else start(self))


def test_adoption_report_must_remain_the_report_that_selected_it(tmp_path):
    storage = SupplyStore(tmp_path, 'p')
    candidate = {'id': 'M1', 'source': 'page:https://x.example/plans', 'key': 'k', 'fingerprint': 'f',
                 'role': 'state', 'item': {'title': 'Plans', 'url': 'https://x.example/plans', 'excerpts': ['price 20']}}
    pending = {'job': 'j', 'at': 100000, 'delivered': {}, 'sources': {}, 'candidates': [candidate]}
    storage.commit(pending, '<!-- material-use: {"selected":["M1"]} -->', 'A.md')
    storage.commit({**pending, 'at': 200000}, 'This report did not adopt M1.', 'B.md')
    result = storage.candidates('j')['M1']
    assert result['status'] == 'reported'
    assert result['report'] == 'A.md', result


def test_old_state_version_is_not_reoffered_after_replacement(source_setup, monkeypatch):
    engine, job, profile, storage = source_setup
    profile.update(retry_items=3, coverage=[{'key': 'price', 'jobs': [JOB],
        'sources': [{'spec': job['sources'][0], 'role': 'state'}]}])
    now = [100000.0]
    rows = [item(text='Current price: $20')]
    monkeypatch.setattr(supply.time, 'time', lambda: now[0])
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: rows)
    pending = supply.collect(engine, job, profile)[2]
    old_id = pending['candidates'][0]['id']
    storage.commit(pending, 'Not selected.', 'A.md')
    now[0] += 90000
    rows[:] = [item(text='Current price: $10')]
    got, _, pending, _ = supply.collect(engine, job, profile)
    assert old_id not in [r['id'] for r in pending['candidates']], [r.to_markdown() for r in got]


def test_empty_live_response_must_not_refresh_cached_state_coverage(source_setup, monkeypatch):
    engine, job, profile, storage = source_setup
    job['_issue_id'] = 'isolated-review'
    profile['coverage'] = [{'key': 'price', 'jobs': [JOB],
                           'sources': [{'spec': job['sources'][0], 'role': 'state'}]}]
    MaterialArchive(engine.workspace_dir + '/state').record(job['sources'][0], [item()], now=1)
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [])
    receipt = supply.collect(engine, job, profile)[3]
    assert receipt['coverage'][0]['status'] != 'current', receipt['coverage'][0]


def test_existing_pool_source_can_receive_new_coverage_assignment(source_setup, monkeypatch):
    from core.video import screenshots
    engine, _, profile, storage = source_setup
    monkeypatch.setattr(screenshots, 'public_url', lambda url: url)
    spec = 'rss:https://vendor.example/feed'
    storage.add({'spec': spec, 'name': 'Vendor updates', 'jobs': [JOB]})
    profile.update(coverage=[{'key': 'releases', 'jobs': [JOB], 'queries': ['vendor releases'], 'sources': []}])
    search = SimpleNamespace(calls=1, search=lambda *a, **k: [SearchResult('Vendor', 'https://vendor.example/story/1')])
    def choose(messages, **kwargs):
        candidates = json.loads(messages[1]['content'])['candidates']
        return SimpleNamespace(content=json.dumps({'sources': [{'id': r['id'], 'jobs': [JOB],
            'coverage': ['releases'], 'role': 'release'} for r in candidates]}))
    source_discovery.discover(engine, profile, search=search, llm=SimpleNamespace(chat=choose, usage={}),
        get_page=lambda *a: '<link rel="alternate" type="application/rss+xml" href="/feed">',
        probe=lambda *a, **k: [item(provider='rss')])
    assert source_coverage.CoverageStore(engine.workspace_dir + '/state', profile['key']).status(profile)


def test_image_preparation_failure_does_not_escape_batch_advance(video_env, monkeypatch):
    from core import user_materials
    from io import BytesIO
    from PIL import Image
    buffer = BytesIO(); Image.new('RGB', (640, 360)).save(buffer, 'PNG')
    image = user_materials.save(str(video_env / 'state'), '1', name='图.png', raw=buffer.getvalue())
    script = _approved_script()
    store.update_script(script['id'], topic={**script['topic'], 'user_materials': {'ids': [image['id']], 'owner': '1', 'mode': 'adapt'}})
    def invalid(*a, **k):
        raise ValueError('placement model returned invalid JSON')
    monkeypatch.setattr(assistant_materials, 'render_images', invalid)
    monkeypatch.setattr(video_render, 'start', lambda *a, **k: None)
    for _ in range(video_executor.RENDER_RETRIES + 1):
        outcome = video_executor.advance_auto(store.get_script(script['id']), {}, can_start_script=True, can_start_render=True)
        rid = outcome['item']['render_id']
        assert store.get_render(rid)['status'] == 'SUBMITTING'
        client = SimpleNamespace(session=lambda: nullcontext())
        assert video_render.process(rid, client=client) == 'FAILED'
        failed = store.get_render(rid)
        assert failed['error_code'] == 'MATERIAL_PREPARATION_FAILED' and not failed['submit_started_at']
    outcome = video_executor.advance_auto(store.get_script(script['id']), {}, can_start_script=True, can_start_render=True)
    assert outcome['hold'] and len(store.list_renders(script['id'])) == video_executor.RENDER_RETRIES + 1


def test_material_script_supports_requested_restyle(video_env, monkeypatch):
    from web.services.assistant_tools.video import VideoCreate
    from tests import golden_video_cells
    from core.video import writer
    golden_video_cells.install_recipes(video_env / 'config')
    shutil.copy(Path(__file__).parents[1] / 'config/video_styles.yaml', video_env / 'config/video_styles.yaml')
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    monkeypatch.setattr(writer, 'write', lambda **k: writer.WrittenScript('稿', [{'title': '节', 'paragraphs': [{'text': '正文'}]}], {}, {}))
    made = assistant_materials.create(VideoCreate(column='deep_dive', mode='adapt',
        text='提供的资料。', goal='根据材料制作'), None, {'id': 1, 'username': 'admin'})
    assert video_workflow.run(made['script_id']) == 'DRAFT'
    _no_video_threads(monkeypatch)
    video_workflow.restyle(made['script_id'])
    assert video_workflow.run_restyle(made['script_id'], 'intermediate', 'explainer') == 'DRAFT'
    version = store.get_version(made['script_id'], 2)
    assert version['meta']['user_materials']['style']['tone'] == 'explainer'
    assert version['meta']['user_materials']['revision'] == 2


def test_research_receives_the_user_goal(video_env, monkeypatch):
    from core import engine
    from core.video import writer
    from web.services.assistant_tools.video import VideoCreate
    import yaml
    marker = '只比较过去三十天的团队席位计费变化'
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    seen = []
    class FakeEngine:
        def __init__(self, *a, **k):
            pass
        def run_job(self, name):
            seen.append(yaml.safe_load((video_env / 'jobs' / (name + '.yaml')).read_text())['prompt'])
            path = video_env / 'output/research.md'
            path.parent.mkdir(exist_ok=True)
            path.write_text('A supplemental report.')
            return str(path)
    monkeypatch.setattr(engine, 'ResearchEngine', FakeEngine)
    monkeypatch.setattr(writer, 'write', lambda **k: writer.WrittenScript('稿', [{'title': '节', 'paragraphs': [{'text': '正文'}]}], {}, {}))
    made = assistant_materials.create(VideoCreate(column='deep_dive', mode='research',
        text='产品套餐资料', goal=marker), None, {'id': 1, 'username': 'admin'})
    assert video_workflow.run(made['script_id']) == 'DRAFT'
    assert marker in seen[0], seen[0]


def test_invalid_image_positions_are_not_cached_as_silent_omission(video_env, monkeypatch):
    from core import user_materials
    from io import BytesIO
    from PIL import Image
    from web.settings import get_settings
    from core.llm import ChatResponse
    raw = BytesIO()
    Image.new('RGB', (640, 360)).save(raw, 'PNG')
    state = get_settings().paths.state_dir
    row = user_materials.save(state, '1', name='图.png', raw=raw.getvalue())
    user_materials.prepare(state, row['id'], '1', read_url=None, describe_image=lambda r: '套餐价格20')
    class Model:
        def __init__(self, *a, **k):
            pass
        def chat(self, *a, **k):
            return ChatResponse(content='{"caption":"套餐","placements":[{"chapter_index":99,"paragraph_index":99}]}')
        def close(self):
            pass
    monkeypatch.setattr(assistant_materials, 'LLMClient', Model)
    script = {'id': 'a' * 32, 'topic': {'user_materials': {'ids': [row['id']], 'owner': '1', 'goal': '必须展示这张图'}}}
    version = {'sections': [{'title': '价格', 'paragraphs': [{'text': '套餐价格20'}]}]}
    client = SimpleNamespace(base_url='http://engine', upload_creative_image=lambda *a: {'image_id': 'id'})
    with pytest.raises(assistant_materials.MaterialPreparationError, match='超出'):
        assistant_materials.render_images(script, version, client)
    assert not list(user_materials.folder(state, row['id']).glob('placements_*.json'))


def test_adoption_history_survives_background_delivery_and_replay(tmp_path):
    storage = SupplyStore(tmp_path, 'p')
    row = {'id': 'M1', 'source': 's', 'key': 'k', 'fingerprint': 'f', 'item': {}, 'role': 'state'}
    pending = {'job': 'j', 'at': 100, 'sources': {}, 'delivered': {}, 'candidates': [row]}
    storage.commit(pending, '<!-- material-use: {"selected":["M1"]} -->', 'A.md')
    second = {**pending, 'at': 200}
    for _ in range(2):
        storage.commit(second, '<!-- material-use: {"background":["M1"]} -->', 'B.md')
    current = storage.candidates('j')['M1']
    assert current['status'] == 'reported' and current['report'] == 'A.md'
    assert current['last_report'] == 'B.md' and current['offers'] == 2
    assert [r['status'] for r in current['history']] == ['reported', 'background']


def test_legacy_flat_adoption_is_preserved_without_inventing_a_report_link(tmp_path):
    storage = SupplyStore(tmp_path, 'p')
    path = storage._material_path('j'); path.parent.mkdir(parents=True)
    legacy = {'id': 'M1', 'status': 'reported', 'report': 'possibly-overwritten.md'}
    path.write_text(json.dumps({'M1': legacy}))
    row = storage.candidates('j')['M1']
    assert row['status'] == 'unknown' and not row['report']
    assert row['legacy'] == legacy


def test_disappeared_state_section_is_retired_without_erasing_its_adoption(tmp_path):
    storage = SupplyStore(tmp_path, 'p')
    row = {'id': 'M1', 'source': 's', 'key': 'k', 'fingerprint': 'f', 'item': {}, 'role': 'state'}
    storage.observe('j', [{'source': 's', 'key': 'k', 'fingerprint': 'f'}], at=100)
    storage.commit({'job': 'j', 'at': 100, 'sources': {}, 'delivered': {}, 'candidates': [row]},
                   '<!-- material-use: {"selected":["M1"]} -->', 'A.md')
    storage.observe('j', [], at=200, complete_sources={'s'})
    current = storage.candidates('j')['M1']
    assert current['lifecycle'] == 'superseded' and current['report'] == 'A.md'


def test_update_task_appends_with_revision_and_preserves_old_version(client, video_env, monkeypatch):
    from tests.test_assistant_api import login
    from web.services.assistant_tools.video import VideoCreate
    from core.video import material_context
    headers = login(client)
    user = {'id': 1, 'username': 'admin'}
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    monkeypatch.setattr(video_workflow, 'rewrite', lambda sid, **k: store.update_script(sid, status='WRITING'))
    made = assistant_materials.create(VideoCreate(column='deep_dive', mode='narration', text='原来的正文。', goal='原要求'), None, user)
    sid = made['script_id']
    assert video_workflow.run(sid) == 'DRAFT'
    old = store.get_version(sid, 1)
    response = client.patch(f'/api/video/scripts/{sid}/materials', headers=headers,
        json={'text': '补充的正文。', 'goal': '新要求', 'base_revision': 1})
    assert response.status_code == 200, response.text
    assert response.json()['script_id'] == sid and response.json()['revision'] == 2
    assert len(response.json()['material_ids']) == 2
    assert video_workflow.run_rewrite(sid) == 'DRAFT'
    current = store.get_version(sid, 2)
    assert current['meta']['user_materials']['goal'] == '新要求'
    assert '补充的正文' in json.dumps(current, ensure_ascii=False)
    assert store.get_version(sid, 1) == old
    assert '补充的正文' not in assistant_materials.source_of(store.get_script(sid), material_context.get(store.get_script(sid), old))
    stale = client.patch(f'/api/video/scripts/{sid}/materials', headers=headers,
        json={'goal': '过时的更新', 'base_revision': 1, 'regenerate': False})
    assert stale.status_code == 409
    client.post('/api/auth/logout', headers=headers)
    other = login(client, 'editor')
    denied = client.patch(f'/api/video/scripts/{sid}/materials', headers=other,
        json={'goal': '其他人修改', 'base_revision': 2, 'regenerate': False})
    assert denied.status_code == 403


def test_replace_inputs_changes_only_current_context_and_blocks_active_update(video_env, monkeypatch):
    from web.routes.video_materials import MaterialUpdateIn
    from web.services.assistant_tools.video import VideoCreate
    from core.video.material_context import ContextConflict
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    user = {'id': 1, 'username': 'admin'}
    made = assistant_materials.create(VideoCreate(column='deep_dive', mode='narration', text='第一份正文'), None, user)
    sid = made['script_id']
    with pytest.raises(ContextConflict):
        assistant_materials.update(sid, MaterialUpdateIn(text='第二份正文', base_revision=1, regenerate=False), None, user)
    assert video_workflow.run(sid) == 'DRAFT'
    got = assistant_materials.update(sid, MaterialUpdateIn(text='第二份正文', base_revision=1,
        replace_materials=True, regenerate=False), None, user)
    assert len(got['material_ids']) == 1 and got['material_ids'] != made['material_ids']
    assert store.get_version(sid, 1)['meta']['user_materials']['ids'] == made['material_ids']


def test_research_reuses_scope_but_not_changed_goal_or_materials(video_env, monkeypatch):
    from core import engine
    from core.video import writer
    from web.services.assistant_tools.video import VideoCreate
    from web.routes.video_materials import MaterialUpdateIn
    from tests import golden_video_cells
    golden_video_cells.install_recipes(video_env / 'config')
    shutil.copy(Path(__file__).parents[1] / 'config/video_styles.yaml', video_env / 'config/video_styles.yaml')
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    _no_video_threads(monkeypatch)
    calls = []
    class Engine:
        def __init__(self, *a, **k):
            pass
        def run_job(self, name):
            calls.append(name)
            path = video_env / f'output/{len(calls)}.md'
            path.parent.mkdir(exist_ok=True)
            path.write_text('补充研究内容')
            return str(path)
    monkeypatch.setattr(engine, 'ResearchEngine', Engine)
    monkeypatch.setattr(writer, 'write', lambda **k: writer.WrittenScript('稿', [{'title': '节', 'paragraphs': [{'text': '正文'}]}], {}, {}))
    user = {'id': 1, 'username': 'admin'}
    sid = assistant_materials.create(VideoCreate(column='deep_dive', text='正文', goal='比较价格', mode='research'), None, user)['script_id']
    assert video_workflow.run(sid) == 'DRAFT'
    assert video_workflow.run_restyle(sid, 'intermediate', 'explainer') == 'DRAFT'
    assert video_workflow.run_rewrite(sid) == 'DRAFT'
    assert len(calls) == 1
    assert store.get_version(sid, 3)['meta']['style']['tone'] == 'explainer'
    for change in ({'goal': '比较额度'}, {'text': '新增材料'}):
        revision = store.get_script(sid)['topic']['user_materials']['revision']
        assistant_materials.update(sid, MaterialUpdateIn(base_revision=revision, **change), None, user)
        assert video_workflow.run_rewrite(sid) == 'DRAFT'
    assert len(set(calls)) == 3


def test_image_omission_requires_reason_and_approved_context_stays_frozen(video_env, monkeypatch):
    from core import user_materials
    from io import BytesIO
    from PIL import Image
    raw = BytesIO(); Image.new('RGB', (640, 360)).save(raw, 'PNG')
    row = user_materials.save(str(video_env / 'state'), '1', name='图.png', raw=raw.getvalue())
    script = _approved_script()
    old = {'ids': [row['id']], 'owner': '1', 'goal': '旧要求', 'revision': 1}
    with store.connect(write=True) as conn:
        conn.execute('UPDATE script_versions SET meta=? WHERE script_id=?',
            (json.dumps({'user_materials': old}), script['id']))
    store.update_script(script['id'], topic={**script['topic'], 'user_materials': {**old, 'goal': '新要求', 'revision': 2}})
    monkeypatch.setattr(assistant_materials, 'prepare', lambda *a: pytest.fail('dispatcher must not prepare material'))
    render = video_render.create(store.get_script(script['id']))
    assert render['visuals']['material_context']['goal'] == '旧要求'
    assert '旧要求' in render['request']['creative_intent']['purpose']
    assert '新要求' not in render['request']['creative_intent']['purpose']
    seen = []
    def skipped(script, version, client, on_decision):
        seen.append(version['meta']['user_materials'])
        on_decision({'material_id': row['id'], 'status': 'skipped', 'reason': '当前旁白没有对应内容', 'placements': []})
        return {}
    monkeypatch.setattr(assistant_materials, 'render_images', skipped)
    frozen = assistant_materials.prepare_render(render, SimpleNamespace())
    assert seen == [render['visuals']['material_context']]
    assert seen[0]['goal'] == '旧要求' and seen[0]['ids'] == old['ids']
    assert '当前旁白没有对应内容' in frozen['visuals']['reason']
    assert frozen['submission_request']
    assert assistant_materials.prepare_render(frozen, SimpleNamespace()) == frozen
    with pytest.raises(assistant_materials.MaterialPreparationError):
        assistant_materials._selection({'placements': []}, {(0, 0)})


def test_dispatch_errors_are_bounded_per_item_without_starving_other_items(video_env, monkeypatch):
    from core.video import autopilot_store as ap
    from web.services import video_autopilot as pilot
    monkeypatch.setattr(pilot, 'fresh_topics', lambda *a: True)
    run = ap.create_run({'refresh': False}, [{'column_key': 'deep_dive', 'topic': {'title': str(i)}} for i in range(2)])
    calls = []
    def advance(item, **kwargs):
        calls.append(item['slot'])
        if item['slot'] == 0:
            raise ValueError('broken material preparation')
        ap.update_item(item['id'], state='DONE')
        return {'script': 0, 'render': 0}
    monkeypatch.setattr(pilot, 'advance', advance)
    for _ in range(3):
        pilot._tick_run(ap.get_run(run['id']), {'scripts': 0, 'renders': 0})
    result = ap.get_run(run['id'])
    assert result['status'] == 'DONE'
    assert [r['state'] for r in result['items']] == ['FAILED', 'DONE']
    assert result['items'][0]['retries']['dispatch'] == 3
    assert 1 in calls


def test_dispatch_errors_are_bounded_per_batch_without_starving_next_batch(video_env, monkeypatch):
    from core.video import autopilot_store as ap
    from web.services import video_autopilot as pilot
    runs = [ap.create_run({}, [{'column_key': 'deep_dive', 'topic': {'title': str(i)}}]) for i in range(2)]
    calls = []
    def tick(run, counts):
        calls.append(run['id'])
        if run['id'] == runs[0]['id']:
            raise ValueError('broken batch')
        return 1
    monkeypatch.setattr(pilot, '_tick_run', tick)
    for _ in range(3):
        assert pilot.tick() == 1
    assert ap.get_run(runs[0]['id'])['status'] == 'DONE'
    assert ap.get_run(runs[0]['id'])['dispatch_failures'] == 3
    assert calls.count(runs[1]['id']) == 3


def test_source_assignment_merges_coverage_even_at_pool_capacity(source_setup, monkeypatch):
    from core.video import screenshots
    engine, _, profile, storage = source_setup
    monkeypatch.setattr(screenshots, 'public_url', lambda url: url)
    spec = 'rss:https://vendor.example/feed'
    storage.add({'spec': spec, 'jobs': [JOB], 'coverage': ['old-goal'], 'sample': 'recent release sample',
                 'last_verified_at': 1000000})
    monkeypatch.setattr(source_discovery.time, 'time', lambda: 1000001)
    profile.update(pool_limit=1, coverage=[{'key': 'new-goal', 'jobs': [JOB], 'sources': []}])
    def choose(messages, **kwargs):
        candidates = json.loads(messages[1]['content'])['candidates']
        return SimpleNamespace(content=json.dumps({'sources': [{'id': candidates[0]['id'], 'jobs': [JOB],
            'coverage': ['new-goal'], 'role': 'release'}]}))
    source_discovery.discover(engine, profile, search=SimpleNamespace(calls=0, search=lambda *a, **k: []),
        llm=SimpleNamespace(chat=choose, usage={}), probe=lambda *a, **k: pytest.fail('fresh sample should be reusable'))
    pool = storage.pool()['sources']
    assert len(pool) == 1 and pool[0]['coverage'] == ['old-goal', 'new-goal']


def test_coverage_keeps_last_live_time_when_later_reads_use_cache(tmp_path):
    coverage = source_coverage.CoverageStore(tmp_path, 'p')
    coverage.record({'s': {'live_items': 3, 'live_status': 'read'}}, at=1000)
    coverage.record({'s': {'live_items': 0, 'live_status': 'empty', 'cache_items': 3}}, at=2000)
    coverage.record({'s': {'live_items': 3, 'live_status': 'read'}}, at=1500)
    row = coverage.observations()['s']
    assert row['read_at'] == 1500 and row['attempted_at'] == 2000 and row['live_status'] == 'empty'


def test_legacy_pending_sections_can_be_retired_after_complete_live_read(tmp_path):
    storage = SupplyStore(tmp_path, 'p')
    path = storage._material_path('j'); path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'M1': {'id': 'M1', 'source': 's', 'key': 'removed', 'fingerprint': 'f',
        'status': 'offered', 'last_offered': 100, 'first_seen': 100, 'offers': 1}}))
    storage.observe('j', [{'source': 's', 'key': 'retained', 'fingerprint': 'new'}], at=200000, complete_sources={'s'})
    assert not storage.retry_candidates('j', now=200000, limit=10)


def test_material_update_receipt_tracks_the_new_video_version(video_env):
    from web.services import assistant_tasks
    script = _approved_script()
    for name, args in [('update_video_materials', {'script_id': script['id'], 'make_video': True}),
                       ('operate_application', {'action_id': 'PATCH /api/video/scripts/{script_id}/materials',
                         'path': {'script_id': script['id']}, 'body': {'make_video': True}})]:
        task = assistant_tasks.capture(name, args)
        assert task['step'] == 'workflow' and task['target_version'] == 2
    assert assistant_tasks.capture('update_video_materials', {'script_id': script['id'], 'regenerate': False}) is None
    assert assistant_tasks.capture('create_video', {'make_video': True})['step'] == 'workflow'


def test_partial_dispatch_failure_refreshes_worker_capacity(video_env, monkeypatch):
    from core.video import autopilot_store as ap
    from web.services import video_autopilot as pilot
    run = ap.create_run({}, [{'column_key': 'deep_dive', 'topic': {'title': str(i)}} for i in range(2)])
    allowed = []
    def advance(item, **kwargs):
        if item['slot'] == 0:
            script = store.create_script('deep_dive', {'title': '已启动的任务'})
            store.update_script(script['id'], status='WRITING')
            ap.update_item(item['id'], script_id=script['id'], state='WORKING')
            raise ValueError('failed after starting work')
        allowed.append(kwargs['can_start_script'])
        return {'script': 0, 'render': 0}
    monkeypatch.setattr(pilot, 'MAX_SCRIPTS', 1)
    monkeypatch.setattr(pilot, 'advance', advance)
    pilot._tick_run(run, {'scripts': 0, 'renders': 0})
    assert allowed == [False]


def test_cannot_retarget_a_task_while_its_old_video_is_in_flight(video_env, monkeypatch):
    from web.routes.video_materials import MaterialUpdateIn
    from web.services.assistant_tools.video import VideoCreate
    from core.video.material_context import ContextConflict
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    user = {'id': 1, 'username': 'admin'}
    sid = assistant_materials.create(VideoCreate(column='deep_dive', mode='narration', text='完整正文'), None, user)['script_id']
    assert video_workflow.run(sid) == 'DRAFT'
    store.update_script(sid, status='APPROVED', approved_version=1)
    video_render.create(store.get_script(sid))
    with pytest.raises(ContextConflict):
        assistant_materials.update(sid, MaterialUpdateIn(base_revision=1, goal='新的制作要求', make_video=True), None, user)
    assert store.get_script(sid)['topic']['user_materials']['revision'] == 1


def test_state_source_keeps_its_role_when_assigned_without_a_coverage_cell(source_setup, monkeypatch):
    from dataclasses import replace
    engine, job, profile, storage = source_setup
    storage.add({'spec': job['sources'][0], 'jobs': [JOB], 'role': 'state'})
    job['_collection_window'] = {'days': 1, 'start': '2026-10-30T00:00:00+08:00',
        'end': '2026-10-31T00:00:00+08:00', 'timezone': 'Asia/Shanghai'}
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [replace(item(text='current price $20'), publish_date='2020-01-01')])
    rows, _, pending, _ = supply.collect(engine, job, profile)
    assert 'current price $20' in rows[0].to_markdown()
    assert pending['candidates'][0]['role'] == 'state'
