"""Compose upstream narration into mixed, speech-led visuals without a content audit."""
from __future__ import annotations

import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

from g2 import information_motion as motion
from g2.card_editorial import groups

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / 'g2/schemas/explanation-plan.schema.json'
COMPONENTS = (*motion.COMPONENTS, 'data_chart', 'scatter_chart')


def enabled(saved: dict, form: str) -> bool:
    return bool(saved.get('visual_storytelling') and (saved.get('chapters') or saved.get('revision_mode') == 'narration')
                and saved.get('production_mode') != 'fast'
                and form not in {'information_cards', 'chat_dialogue', 'image_slideshow'}
                and not (form == 'process_diagram' and saved.get('video_form') == 'process_diagram')
                and all(row.get('placements') for row in saved.get('image_materials', [])))


def messages(context: dict, correction: str | None = None) -> list[dict]:
    system = ('你是视频画面编排师。上游讲稿、数据和结论已经确定，直接把它们展示好。'
              '每个输入分组返回一页，保持id和顺序。重点是观看体验：观众应看哪里、何时看到差异、如何跟上讲解。'
              '时间趋势用data_chart kind=trend；多组数据比较用kind=grouped；'
              '每个对象同时有两个数值维度时（例如成本和速度、价格和效果），优先用scatter_chart展示二维位置，points按讲述顺序排列。'
              '散点图分别填写x_label/x_unit、y_label/y_unit，每个点包含name/x/y，点数2到10个；可用group区分分组。'
              '用reveal_phrases对应各对象在讲稿中首次展开的短语。'
              '讲完各点后，如有先看谁、再比较谁的讲述，可用focus_steps指定indices、phrase和简短caption，让镜头依次强调对应对象。'
              '结尾用focus_indices强调上游结论对应的1到3个对象，takeaway_phrase填写引出该结论的讲稿短语。'
              'x_hint/y_hint可提示讲稿已说出的维度含义，例如越低越省或越高越快。'
              '单个数字用big_number；旧值到新值用price_change；简单类别比较沿用bar_chart；其他内容用现有文字或对比组件。'
              '从本章讲稿组织图表数据，让时间顺序、系列名称、单位易读。趋势与多组比较图限制2到8个横轴项、最多3个系列；'
              '长序列提炼讲稿提到的关键节点，缺失值用null。不要把编号、年份或步骤编号当测量值。'
              'data_chart.takeaway用讲稿已有结论，focus_index指出要强调的横轴项；'
              'grouped可用focus_series强调结论对应的整个系列，避免随意聚焦单个类别。'
              'reveal_phrases填写讲稿中引出每个时间点（trend）或每个系列（grouped）的短语，帮助画面跟随旁白。'
              '有可用图片的普通说明会自动展示图片；讲到数据时优先用数字或图表。'
              '画面文字简洁，不把整段旁白贴上屏。只返回符合required_schema的JSON。')
    payload = {'title': context['title'], 'groups': context['groups'],
               'component_props_schemas': {name: json.loads((ROOT / f'schemas/components/{name}.schema.json').read_text()) for name in COMPONENTS},
               'required_schema': json.loads(SCHEMA.read_text())}
    if context.get('instruction'):
        payload['visual_preference'] = context['instruction']
    if correction:
        payload['layout_feedback'] = correction
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]


def validate(plan: dict, context: dict) -> None:
    """Only rendering structure: pages, registered props and chart array dimensions."""
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(plan)
    if [p['id'] for p in plan['pages']] != [g['id'] for g in context['groups']]:
        raise ValueError('画面页需要按顺序覆盖输入分组')
    for page in plan['pages']:
        props = page['props']
        Draft202012Validator(json.loads((ROOT / f"schemas/components/{page['component']}.schema.json").read_text())).validate(props)
        if page['component'] == 'data_chart':
            if any(len(s['values']) != len(props['categories']) for s in props['series']):
                raise ValueError('每个图表系列需要与横轴项数量一致')
            if not any(v is not None for s in props['series'] for v in s['values']):
                raise ValueError('图表需要可显示的数据')
            if props.get('focus_series', 0) >= len(props['series']):
                raise ValueError('图表聚焦系列不存在')
            if props.get('focus_index', 0) >= len(props['categories']):
                raise ValueError('图表聚焦项超出横轴范围')
        elif page['component'] == 'scatter_chart':
            indices = [*props.get('focus_indices', []), *[i for step in props.get('focus_steps', []) for i in step['indices']]]
            if any(i >= len(props['points']) for i in indices):
                raise ValueError('散点图强调对象需要在本图中存在')
        elif page['component'] == 'bar_chart' and len(props['categories']) != len(props['values']):
            raise ValueError('条形图需要每项对应一个数值')
        if page['component'] not in {'data_chart', 'scatter_chart'}:
            motion.validate_text_fit({'id': page['id'], 'component': page['component'], 'props': props}, False)


def paragraph_groups(pipeline, narration: dict, saved: dict) -> list[dict]:
    from g2.authored_script import split_script
    starts, pictures = set(), {}
    for ci, chapter in enumerate(saved.get('chapters') or []):
        count = 0
        for pi, paragraph in enumerate(chapter['paragraphs']):
            sid = f'c{ci + 1}_n{count + 1}'
            starts.add(sid)
            pictures[sid] = [i for i, image in enumerate(saved.get('image_materials', []))
                             if {'chapter_index': ci, 'paragraph_index': pi} in image.get('placements', [])]
            count += len(split_script(paragraph))
    if not saved.get('chapters'):
        path = pipeline.ARTIFACTS / 'paragraph-starts.json'
        starts = set(json.loads(path.read_text())['segment_ids']) if path.exists() else set()
    result = groups(narration, starts)
    texts = {s['id']: s['text'] for s in narration['segments']}
    for group in result:
        group['narration'] = [{'id': sid, 'text': texts[sid]} for sid in group['segment_ids']]
        group['image_indices'] = pictures.get(group['segment_ids'][0], [])
    return result


def plan_storyboard(pipeline, narration: dict, saved: dict) -> dict:
    input_groups = paragraph_groups(pipeline, narration, saved)
    pages = []
    # One request per chapter; ordinary paragraphs still share the chapter's data context.
    for chapter in dict.fromkeys(g['chapter_id'] for g in input_groups):
        local = [g for g in input_groups if g['chapter_id'] == chapter]
        context = {'title': narration['title'], 'groups': local}
        def check(value, ctx):
            try:
                validate(value, ctx)
            except (ValueError, TypeError, KeyError, ValidationError) as exc:
                raise pipeline.G2Fault('EXPLANATION_LAYOUT', str(exc)) from exc
        plan = pipeline.generate_validated_plan(f'{chapter}-explanation', messages, SCHEMA, check, context)
        pages.extend(plan['pages'])
    return {'pages': pages, 'groups': input_groups}


def compile_storyboard(pipeline, plan: dict, alignment: dict, saved: dict, assets: list[dict]) -> dict:
    """Combine paragraph pictures and native data/text scenes on the same measured timeline."""
    scenes = []
    data = {'data_chart', 'scatter_chart', 'big_number', 'price_change', 'bar_chart'}
    rows = saved.get('image_materials', [])
    for page, group in zip(plan['pages'], plan['groups']):
        component, props = page['component'], page['props']
        if component == 'price_change':
            props = {**props, 'staged': True}
        indices = group['image_indices']
        if indices and component not in data:
            images = [{**{'image_path': assets[i]['path'], 'image_asset_id': assets[i]['id']},
                       **{k: rows[i][k] for k in ('caption', 'role', 'focus') if k in rows[i]}} for i in indices]
            component = 'material_collage' if len(images) == 2 else 'object_showcase'
            props = {'title': group['title'], 'text': (' '.join(props.get('lines', [])) or props.get('subtitle') or group['text'])[:80], 'images': images, 'focus_indices': list(range(len(images)))}
        scenes.append({'id': page['id'], 'component': component, 'props': props,
                       'headline': props.get('title', group['title']), 'narration_segment_ids': group['segment_ids']})
    board = motion.compile_storyboard({'scenes': scenes}, alignment, pipeline)
    board.update(image_assets=assets, presentation='explanation')
    pipeline.write_json(pipeline.ARTIFACTS / 'explanation-plan.json', plan)
    pipeline.write_json(pipeline.ARTIFACTS / 'storyboard-plan.json', board)
    return board


def replan_scene(pipeline, old: dict, narration: dict, ir: dict, instruction: str) -> dict:
    """Keep the existing screen revision entry point usable for mixed native scenes."""
    ids = old['narration_segment_ids']
    speech = [s for s in ir['narration']['segments'] if s['id'] in ids]
    group = {'id': old['id'], 'title': old['props'].get('title', old['purpose']),
             'text': ''.join(s['text'] for s in speech), 'segment_ids': ids, 'narration': speech, 'image_indices': []}
    context = {'title': narration['title'], 'groups': [group], 'instruction': instruction}
    def check(value, ctx):
        try:
            validate(value, ctx)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise pipeline.G2Fault('EXPLANATION_LAYOUT', str(exc)) from exc
    plan = pipeline.generate_validated_plan(f"{old['id']}-explanation-revision", messages, SCHEMA, check, context)
    return compile_storyboard(pipeline, {**plan, 'groups': [group]}, {'rows': speech}, {}, [])['scenes'][0]
