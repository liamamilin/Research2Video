"""Programme production follows a frozen column/report pair, never topic count."""
import copy
from pathlib import Path

from core.video import content_columns, episodes, production, reports, store, writer
from tests.test_content_columns import column_env  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env, ROOT, llm, research, autovideo  # noqa: F401
from web.services import video_autopilot


def _pick(root):
    c = production.load_all(str(root / 'config'), str(root / 'jobs'))['flash']
    return {'job': c.job, 'report': 'output/flash/2026-10-19_ai_flash_discovery.md',
            'picks': [{'column': c.recipe, 'production': c.key, 'whole': True, 'all': True, 'content_mode': 'episode'}]}


def test_existing_columns_default_to_one_programme_and_single_mode_is_retained(column_env):
    root = column_env
    c = production.load_all(str(root / 'config'), str(root / 'jobs'))['flash']
    assert c.content_mode == 'episode' and c.split == 'whole' and not c.reverify
    column = content_columns.load_all(str(root / 'config'))['model_review']
    content_columns.save(str(root / 'config'), column.key, {**column.data(), 'content_mode': 'individual'}, expected_revision=column.revision)
    c = production.load_all(str(root / 'config'), str(root / 'jobs'))['flash']
    assert c.content_mode == 'individual' and c.split == 'each'


def test_multiple_report_items_make_one_automatic_work_item(column_env):
    items = video_autopilot.pick_topics(_pick(column_env))
    assert len(items) == 1
    assert items[0]['topic']['content_mode'] == 'episode'
    assert len(items[0]['topic']['episode']['items']) >= 2
    assert items[0]['topic']['from_report'].endswith('2026-10-19_ai_flash_discovery.md')
    assert not items[0]['topic'].get('from_item')


def test_a_frozen_programme_pick_survives_later_column_mode_changes(column_env):
    spec = _pick(column_env)
    column = content_columns.load_all(str(column_env / 'config'))['model_review']
    content_columns.save(str(column_env / 'config'), column.key, {**column.data(), 'content_mode': 'individual'}, expected_revision=column.revision)
    assert len(video_autopilot.pick_topics(spec)) == 1
    assert episodes.enabled(video_autopilot.pick_topics(spec)[0]['topic'])


def test_programme_ownership_uses_business_column_not_recipe(column_env):
    spec = _pick(column_env); item = video_autopilot.pick_topics(spec)[0]
    s = store.create_script(item['column_key'], item['topic'], created_by='test', production='flash',
                            content_context={'column': 'model_review'})
    assert video_autopilot.pick_topics(spec) == []
    # A different business column may use the same recipe and source report.
    store.update_script(s['id'], content_column_id='another_column')
    assert len(video_autopilot.pick_topics(spec)) == 1


def test_author_keeps_roles_and_concrete_detail_without_single_video_length_cap():
    parsed = writer._parse('{"title":"本期观察","sections":[{"title":"本期导读","episode_role":"opening","paragraphs":[{"text":"本期讲两个变化。"}]},{"title":"价格变化","episode_role":"item","paragraphs":[{"text":"价格从20变成25，增加5。"},{"text":"下面用一个具体工作过程解释影响。"}]}]}')
    sections, _ = writer._finalize(parsed, set(), 1)
    assert sections[1]['episode_role'] == 'item' and len(sections[1]['paragraphs']) == 2
    topic = {'content_mode': 'episode', 'from_report': 'report.md', 'episode': {'items': [{'id': 'one', 'title': '价格变化'}]}}
    prompt = episodes.brief(topic)
    assert '不作为整期篇幅上限' in prompt and '具体细节' in prompt
    manifest = episodes.manifest(topic, sections)
    assert [i['title'] for i in manifest['items']] == ['价格变化']
    assert manifest['items'][0]['chapter_index'] == 1


def test_mode_is_frozen_per_script_and_manual_single_topics_stay_single():
    assert episodes.snapshot_context({'content_mode': 'episode'}, {'title': '单个主题'})['content_mode'] == 'individual'
    assert episodes.snapshot_context({'content_mode': 'individual'}, {'content_mode': 'episode'})['content_mode'] == 'episode'


def test_new_run_reusing_a_report_path_is_a_new_programme_not_a_rewrite(column_env):
    spec = _pick(column_env); item = video_autopilot.pick_topics(spec)[0]
    store.create_script(item['column_key'], item['topic'], created_by='test', production='flash', content_context={'column': 'model_review'})
    assert video_autopilot.pick_topics(spec) == []
    path = column_env / spec['report']
    path.write_text(path.read_text() + '\n新一轮研究补充了具体过程。\n')
    assert len(video_autopilot.pick_topics(spec)) == 1


def test_native_report_entry_writes_one_programme_and_passes_whole_report_to_author(client, column_env, llm):
    import json
    from tests.test_video_scripts import _login, _csrf, _wait
    llm.replies.append(json.dumps({'title': '本期两个变化', 'sections': [
        {'title': '导读', 'episode_role': 'opening', 'paragraphs': [{'text': '本期讲两个具体变化。'}]},
        {'title': '第一个变化', 'episode_role': 'item', 'paragraphs': [{'text': '先看第一个变化的具体过程。'}]},
        {'title': '第二个变化', 'episode_role': 'item', 'paragraphs': [{'text': '再看第二个变化及其影响。'}]}]}, ensure_ascii=False))
    _login(client)
    pending = client.get('/api/production/flash/pending').json()
    assert len(pending['reports']) == 1
    path = pending['reports'][0]['path']
    response = client.post('/api/production/flash/write', json={'report_path': path}, headers=_csrf(client))
    assert response.status_code == 202, response.text
    assert len(response.json()) == 1
    script = _wait(client, response.json()[0]['id'], {'DRAFT', 'FAILED'})
    assert script['status'] == 'DRAFT', script.get('error')
    assert script['topic']['content_mode'] == 'episode'
    assert len(script['version_shown']['meta']['episode']['items']) == 2
    assert script['version_shown']['meta']['input_kind'] == 'report'
    assert '整期节目' in llm.calls[0][1]['content']
    repeated = client.post('/api/production/flash/write', json={'report_path': path}, headers=_csrf(client))
    assert repeated.status_code == 409


def test_episode_automatic_pick_collapses_multiple_recipe_bindings_in_one_column(column_env):
    from core.video import auto
    import os,time,yaml
    root=column_env; config=root/'config'; job='monitoring/ai_flash_discovery'
    production.save(str(config),'flash_extra',job=job,recipe='point_short',auto=1)
    production.set_auto(str(config),'flash',1)
    owner=content_columns.load_all(str(config))['model_review']
    content_columns.save(str(config),owner.key,{**owner.data(),'production_ids':['flash','flash_extra']},expected_revision=owner.revision)
    now=time.time()+300
    os.utime(root/'output/flash/2026-10-19_ai_flash_discovery.md',(now-120,now-120))
    rows=auto.pending(str(config),str(root/'jobs'),str(root/'state'),str(root),now)
    picks=[p for r in rows if r['job']==job for p in r['picks'] if p['production'] in {'flash','flash_extra'}]
    assert len(picks)==1 and picks[0]['content_mode']=='episode'


def test_episode_length_is_measured_but_never_flagged_against_single_item_minutes(llm):
    from tests.test_video_scripts import script_json
    llm.replies.append(script_json())
    from core.llm import LLMConfig
    written=writer.write(report='原始素材',citation_check=None,topic={'title':'本期节目','content_mode':'episode'},
        column_style=episodes.brief({'content_mode':'episode'}),target_minutes=.5,llm_config=LLMConfig())
    assert written.length_check['status'] is None and written.length_check['budget'] is None
    assert written.length_check['estimated_seconds'] > 0
    assert '多个独立内容' in llm.calls[0][0]['content']


def test_programme_column_keeps_multiple_research_jobs_as_separate_report_suppliers(column_env):
    config = str(column_env / 'config')
    owner = content_columns.load_all(config)['model_review']
    content_columns.save(config, owner.key, {**owner.data(), 'production_ids': ['flash', 'deep_dive']},
                         expected_revision=owner.revision)
    bindings = production.load_all(config, str(column_env / 'jobs'))
    assert len({bindings[key].job for key in ['flash', 'deep_dive']}) == 2
    assert all(bindings[key].content_mode == 'episode' and bindings[key].split == 'whole'
               and bindings[key].content_column_id == owner.key and bindings[key].usable
               for key in ['flash', 'deep_dive'])


def test_publishing_uses_final_branded_programme_chapter_times():
    from core.video import publish
    final = {'timeline': 'branded', 'chapters': [
        {'id': 'c1', 'title': '本期导读', 'start_ms': 1500},
        {'id': 'c2', 'title': '内容甲', 'start_ms': 13500},
        {'id': 'c3', 'title': '内容乙', 'start_ms': 111500},
    ]}
    lines = publish.chapter_lines(final)
    assert [(c['title'], c['seconds'], c['time']) for c in lines] == [
        ('本期导读', 0, '00:00'), ('内容甲', 13.5, '00:13'), ('内容乙', 111.5, '01:51')]
    assert publish.chapter_lines({**final, 'timeline': 'content'}) == []
