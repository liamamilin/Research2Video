"""Column programme rules are shared across matrix and supplied-material entry points."""
import copy
import json

import pytest

from core.video import columns, content_columns, episodes, production, store, writer
from tests.test_content_columns import column_env  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import ROOT, llm, video_env  # noqa: F401
from web.services import assistant_materials, video_render, video_workflow
from web.services.assistant_tools.video import VideoCreate


def authored():
    return json.dumps({'title': '两项变化及其影响', 'sections': [
        {'title': '导读', 'episode_role': 'opening', 'paragraphs': [{'text': '本期讲两个具体变化。'}]},
        {'title': '内容甲', 'episode_role': 'item', 'paragraphs': [{'text': '先讲第一个变化与实际用法。'}]},
        {'title': '内容乙', 'episode_role': 'item', 'paragraphs': [{'text': '再讲第二个变化与结果。'}]},
        {'title': '收尾', 'episode_role': 'closing', 'paragraphs': [{'text': '感谢收看，下期见。'}]},
    ]}, ensure_ascii=False)


@pytest.mark.parametrize('kind', ['news', 'tutorial', 'comparison'])
@pytest.mark.parametrize('mode', ['episode', 'individual'])
def test_material_entry_inherits_column_mode_greeting_and_render_navigation(column_env, llm, monkeypatch, kind, mode):
    config = str(column_env / 'config')
    owner = content_columns.load_all(config)['model_review']
    content_columns.save(config, owner.key, {**owner.data(), 'type': kind, 'content_mode': mode,
        'greeting': '大家好，欢迎收看栏目更新。'}, expected_revision=owner.revision)
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    llm.replies.append(authored())
    made = assistant_materials.create(VideoCreate(column='point_short', production='flash',
        title='用户指定的补充标题', text='两项具体变化及用法。', goal='完整讲清两项变化。'),
        None, {'id': 1, 'username': 'admin'})
    sid = made['script_id']
    assert video_workflow.run(sid, started_by='admin') == 'DRAFT'
    script = store.get_script(sid)
    version = store.get_version(sid, 1)
    assert script['topic']['content_mode'] == mode == script['content_context']['content_mode']
    assert script['topic']['title'] == '用户指定的补充标题'
    assert version['sections'][0]['paragraphs'][0]['text'] == '大家好，欢迎收看栏目更新。'
    assert len(llm.calls) == 1
    if mode == 'episode':
        assert len(version['meta']['episode']['items']) == 2
        assert ('完整教程' if kind == 'tutorial' else '整期节目') in llm.calls[0][1]['content']
    else:
        assert 'episode' not in version['meta']
    store.update_script(sid, status='APPROVED', approved_version=1)
    render = video_render.create(store.get_script(sid))
    assert bool(render['request'].get('episode')) is (mode == 'episode')
    if mode == 'episode':
        assert [r['chapter_indexes'] for r in render['request']['episode']['items']] == [[1], [2]]


def test_original_narration_in_a_programme_column_remains_verbatim(column_env, llm, monkeypatch):
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    prose = '这是已经确定的第一段原稿。\n\n这是第二段，不增加问候。'
    made = assistant_materials.create(VideoCreate(column='point_short', production='flash',
        title='原稿', text=prose, mode='narration'), None, {'id': 1, 'username': 'admin'})
    assert video_workflow.run(made['script_id'], started_by='admin') == 'DRAFT'
    version = store.get_version(made['script_id'], 1)
    assert '\n\n'.join(p['text'] for s in version['sections'] for p in s['paragraphs']) == prose
    assert not llm.calls and 'episode' not in version['meta']


def test_legacy_material_rewrite_freezes_missing_mode_without_modifying_prior_version(column_env, llm, monkeypatch):
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    llm.replies.extend([authored(), authored()])
    made = assistant_materials.create(VideoCreate(column='point_short', production='flash',
        title='补充更新', text='公开材料。'), None, {'id': 1, 'username': 'admin'})
    sid = made['script_id']
    assert video_workflow.run(sid, started_by='admin') == 'DRAFT'
    original = store.get_version(sid, 1)
    original['meta'].pop('episode')
    store.update_version_audit(sid, 1, sections=original['sections'], meta=original['meta'])
    previous = copy.deepcopy(store.get_version(sid, 1))
    script = store.get_script(sid)
    legacy = {k: v for k, v in script['topic'].items() if k != 'content_mode'}
    store.update_script(sid, topic=legacy, content_context={**script['content_context'], 'content_mode': 'individual'})
    assert video_workflow.run_rewrite(sid, started_by='admin') == 'DRAFT'
    assert store.get_version(sid, 1) == previous
    assert store.get_script(sid)['topic']['content_mode'] == 'episode'
    assert len(store.get_version(sid, 2)['meta']['episode']['items']) == 2
    store.update_script(sid, status='APPROVED', approved_version=1)
    old_render = video_render.create(store.get_script(sid))
    assert 'episode' not in old_render['request']
    store.update_render(old_render['id'], status='CANCELLED')
    store.update_script(sid, approved_version=2)
    assert len(video_render.create(store.get_script(sid))['request']['episode']['items']) == 2


def test_failed_legacy_rewrite_keeps_its_original_mode_and_version(column_env, llm, monkeypatch):
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    llm.replies.append(authored())
    made = assistant_materials.create(VideoCreate(column='point_short', production='flash', text='材料。'),
                                      None, {'id': 1, 'username': 'admin'})
    sid = made['script_id']
    assert video_workflow.run(sid, started_by='admin') == 'DRAFT'
    script = store.get_script(sid)
    topic = {k: v for k, v in script['topic'].items() if k != 'content_mode'}
    context = {**script['content_context'], 'content_mode': 'individual'}
    store.update_script(sid, topic=topic, content_context=context)
    original = copy.deepcopy(store.get_version(sid, 1))
    llm.replies.extend(['invalid JSON', 'invalid JSON'])
    assert video_workflow.run_rewrite(sid, started_by='admin') == 'DRAFT'
    assert store.get_script(sid)['topic'] == topic
    assert store.get_script(sid)['content_context'] == context
    assert store.get_version(sid, 1) == original
    assert store.get_script(sid)['current_version'] == 1


def test_explicit_individual_mode_is_not_replaced_by_column_default(column_env, llm, monkeypatch):
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    made = assistant_materials.create(VideoCreate(column='point_short', production='flash', text='材料。'),
                                      None, {'id': 1, 'username': 'admin'})
    script = store.get_script(made['script_id'])
    store.update_script(script['id'], topic={**script['topic'], 'content_mode': 'individual'},
                        content_context={**script['content_context'], 'content_mode': 'individual'})
    llm.replies.append(authored())
    assert video_workflow.run(script['id'], started_by='admin') == 'DRAFT'
    assert store.get_script(script['id'])['topic']['content_mode'] == 'individual'
    assert 'episode' not in store.get_version(script['id'], 1)['meta']


def test_every_shipped_column_binding_uses_its_own_shared_writer_settings(monkeypatch):
    config, jobs = str(ROOT / 'config'), str(ROOT / 'jobs')
    bindings = production.load_all(config, jobs)
    checked = []
    def author(**kwargs):
        return kwargs
    monkeypatch.setattr(writer, 'write', author)
    for key, binding in bindings.items():
        if not binding.usable:
            continue
        context = content_columns.snapshot(config, key, binding.job)
        if not context.get('column'):
            continue
        recipe = content_columns.apply_script_context(columns.get(config, binding.recipe), context)
        topic = episodes.material_topic({'title': ' supplied update', 'user_materials': {'mode': 'adapt'}}, context)
        options = writer.write_for_column(column=recipe, content_context=context, topic=topic,
            report='固定上游材料', citation_check=None, llm_config=object())
        assert options['report'] == '固定上游材料'
        assert options['greeting'] == episodes.greeting(topic, context)
        assert options['voice'] == (recipe.voice_id or None)
        assert options['tutorial'] is (context['type'] == 'tutorial')
        assert topic['content_mode'] == context['content_mode']
        if context['content_mode'] == 'episode':
            assert ('完整教程' if context['type'] == 'tutorial' else '整期节目') in options['column_style']
        checked.append(context['matrix'])
    assert len(checked) > 10 and len(set(checked)) > 3
