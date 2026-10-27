"""Compose upstream narration into mixed, speech-led visuals without a content audit."""
from __future__ import annotations

import json
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

from g2 import information_motion as motion
from g2.card_editorial import groups
from g1.visual_language import component_schema, catalog, validate_references
from g2 import visual_language, process_diagram
from g2.material_shots import describe

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / 'g2/schemas/explanation-plan.schema.json'
COMPONENTS = (*motion.COMPONENTS, 'data_chart', 'scatter_chart', 'flow_diagram')


def enabled(saved: dict, form: str) -> bool:
    if saved.get('production_mode') == 'fast' or form == 'information_cards':
        from g2.production_plan import hybrid_enabled
        return bool(form == 'information_cards' and hybrid_enabled(saved)
                    and all(row.get('placements') for row in saved.get('image_materials', [])))
    return bool(saved.get('visual_storytelling') and (saved.get('chapters') or saved.get('revision_mode') == 'narration')
                and saved.get('production_mode') != 'fast'
                and form not in {'information_cards', 'chat_dialogue', 'image_slideshow'}
                and not (form == 'process_diagram' and saved.get('video_form') == 'process_diagram')
                and all(row.get('placements') for row in saved.get('image_materials', [])))


def messages(context: dict, correction: str | None = None) -> list[dict]:
    system = ('你是视频画面编排师。上游讲稿、数据和结论已经确定，直接把它们展示好。'
              '每个输入分组返回一页，保持id和顺序。分组已按实际配音时长拆合，可能是一长段的一部分或几个连续短段。'
              '每页围绕本组讲解重点，结合同章上下文保持对象名称和讲述关系连贯；不重复贴出整章内容。'
              '重点是观看体验：观众应看哪里、何时看到差异、如何跟上讲解。'
              '时间趋势用data_chart kind=trend；多组数据比较用kind=grouped；'
              '每个对象同时有两个数值维度时（例如成本和速度、价格和效果），优先用scatter_chart展示二维位置，points按讲述顺序排列。'
              '散点图分别填写x_label/x_unit、y_label/y_unit，每个点包含name/x/y，点数2到10个；可用group区分分组。'
              '用reveal_phrases对应各对象在讲稿中首次展开的短语。'
              '讲完各点后，如有先看谁、再比较谁的讲述，可用focus_steps指定indices、phrase和简短caption，让镜头依次强调对应对象。'
              '结尾用focus_indices强调上游结论对应的1到3个对象，takeaway_phrase填写引出该结论的讲稿短语。'
              'x_hint/y_hint可提示讲稿已说出的维度含义，例如越低越省或越高越快。'
              '单个数字用big_number；旧值到新值用price_change；简单类别比较沿用bar_chart；其他内容用现有文字或对比组件。'
              'comparison和bar_chart的大标题放在页级headline，不放进props。bar_chart.unit只写简短计量单位，'
              '例如%；相对基准、分母和其他说明放在headline，不挤进每条数字标签。'
              '从本章讲稿组织图表数据，让时间顺序、系列名称、单位易读。趋势与多组比较图限制2到8个横轴项、最多3个系列；'
              '长序列提炼讲稿提到的关键节点，缺失值用null。不要把编号、年份或步骤编号当测量值。'
              'data_chart.takeaway用讲稿已有结论，focus_index指出要强调的横轴项；'
              'grouped可用focus_series强调结论对应的整个系列，避免随意聚焦单个类别。'
              'reveal_phrases填写讲稿中引出每个时间点（trend）或每个系列（grouped）的短语；数量分别等于categories或series的数量。'
              'grouped按系列展开；不要在reveal_phrases里按月份列出短语，也不要把总结句放进去。'
              '有可用图片的普通说明会自动展示图片；讲到数据时优先用数字或图表。'
              '分组的image_materials给出已配好的图片和区域。为图片讲解填写material_cues，'
              '按旁白顺序指定image_index、可选region_index和讲稿中引出该对象或区域的短语phrase。'
              '同一张网页可按几个区域连续讲解；两张图片可先看左图、再看右图，最后一起比较。'
              '已有页面或输入输出图片时，普通页面说明用概念或对比组织简短文字，由现有图片呈现，不另画抽象流程替代这些图片。'
              'material_summary_phrase可填写引出总结或整体比较的讲稿原句。只安排画面，不改写旁白。'
              '画面文字简洁，不把整段旁白贴上屏。只返回符合required_schema的JSON。')
    system += ('明确的传递过程可以用flow_diagram：2到5个节点，edges连接已有节点，paths填空数组，附title和takeaway；布局由程序生成。' + visual_language.GUIDANCE)
    from g2.story_direction import GUIDANCE
    system += GUIDANCE
    if context.get('fast_cards'):
        system += ('本片采用快速混合编排：开场、普通说明和承接优先用title_card或concept_reveal，'
                   '这些普通卡片保持简洁，不添加visuals或actions。只在值得观众停下观看的重点处使用动画：'
                   '数字变化用price_change，趋势/多组关系用图表，传递过程用flow_diagram。'
                   '有已配图片的页面说明继续使用material_cues，让程序自动转为截图聚焦。'
                   '关键概念可以标importance=key并使用图标；不要为普通过渡段强行画流程图。'
                   '最后回顾前面的重点。用同一套讲述角色和对象名称串联卡片、截图、图表及流程。')
    schemas = {name: component_schema(name) for name in COMPONENTS}
    schemas['flow_diagram']['properties'].pop('layout', None)
    for field in ('nodes', 'edges'):
        schemas['flow_diagram']['properties'][field]['items']['properties']['id']['pattern'] = '^[a-z][a-z0-9_]{0,23}$'
    payload = {'title': context['title'], 'groups': context['groups'], 'visual_vocabulary': catalog(),
               'component_props_schemas': schemas,
               'required_schema': json.loads(SCHEMA.read_text())}
    if context.get('instruction'):
        payload['visual_preference'] = context['instruction']
    if context.get('chapter_context'):
        payload['chapter_context'] = context['chapter_context']
    if context.get('established_entities'):
        payload['established_entities'] = context['established_entities']
    if correction:
        payload['layout_feedback'] = correction
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]


def validate(plan: dict, context: dict) -> None:
    """Only rendering structure: pages, registered props and chart array dimensions."""
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(plan)
    if [p['id'] for p in plan['pages']] != [g['id'] for g in context['groups']]:
        raise ValueError('画面页需要按顺序覆盖输入分组')
    for page in plan['pages']:
        group = next(g for g in context['groups'] if g['id'] == page['id'])
        materials = group.get('image_materials', [])
        for cue in page.get('material_cues', []):
            if (cue['image_index'] >= len(materials) or
                    (cue.get('region_index') is not None and cue['region_index'] >= len(materials[cue['image_index']]['regions']))):
                raise ValueError('图片镜头需要对应本段已有图片和区域')
        props = page['props']
        if page['component'] in {'comparison', 'bar_chart'} and isinstance(props.get('title'), str) and props['title'].strip():
            # These components already render their heading outside props.
            title = props.pop('title')
            heading = page.get('headline', title)
            page['headline'] = heading if title in heading else f'{heading} · {title}'
        if (context.get('fast_cards') and page['component'] == 'concept_reveal'
                and page.get('importance') != 'key' and 'visuals' not in props):
            # A keyword in a bridge sentence must not turn the whole paragraph
            # into a browser-rendered scene through the legacy icon fallback.
            props = {**props, 'visuals': []}
        props = page['props'] = visual_language.enrich(page['component'], props)
        Draft202012Validator(component_schema(page['component'])).validate(props)
        if 'visuals' in props:
            validate_references(page['component'], props)
        if page['component'] == 'flow_diagram':
            ids = {n['id'] for n in props['nodes']}
            if (len(ids) != len(props['nodes']) or len(ids) > 5
                    or len({e['id'] for e in props['edges']}) != len(props['edges'])
                    or any(len(item['id']) > 24 for item in [*props['nodes'], *props['edges']])
                    or any(e['from'] not in ids or e['to'] not in ids for e in props['edges'])):
                raise ValueError('流程图使用2到5个独立节点，连线连接已有节点')
            if not props.get('title') or not props.get('takeaway'):
                raise ValueError('流程图需要标题和讲解结语')
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
        if page['component'] not in {'data_chart', 'scatter_chart', 'flow_diagram'}:
            scene = {'id': page['id'], 'component': page['component'], 'props': props}
            try:
                motion.validate_text_fit(scene, False)
            except motion.InformationFault as exc:
                unit = props.get('unit', '')
                if page['component'] != 'bar_chart' or 'the value label' not in str(exc):
                    raise
                # Keep the supplied numbers and the complete basis visible,
                # using the existing heading rather than repeating it per bar.
                heading = page.get('headline', group['title'])
                page['headline'] = heading if unit in heading else f'{heading} · {unit}'
                props['unit'] = '%' if unit.replace('％', '%').count('%') == 1 else ''
                motion.validate_text_fit(scene, False)


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
        if group['image_indices']:
            group['image_materials'] = describe([saved['image_materials'][i] for i in group['image_indices']])
    return result


def plan_storyboard(pipeline, narration: dict, saved: dict, alignment: dict | None = None) -> dict:
    input_groups = paragraph_groups(pipeline, narration, saved)
    original = input_groups
    assembly = None
    if alignment is not None:
        from g2.scene_assembly import arrange
        input_groups, assembly = arrange(input_groups, alignment, saved)
        pipeline.write_json(pipeline.ARTIFACTS / 'scene-assembly.json', assembly)
    pages = []
    # One request per chapter; ordinary paragraphs still share the chapter's data context.
    for chapter in dict.fromkeys(g['chapter_id'] for g in input_groups):
        local = [g for g in input_groups if g['chapter_id'] == chapter]
        context = {'title': narration['title'], 'groups': local}
        from g2.production_plan import hybrid_enabled
        context['fast_cards'] = hybrid_enabled(saved)
        context['established_entities'] = list(dict.fromkeys(e['name'] for p in pages for e in p.get('entities', [])))
        if assembly:
            context['chapter_context'] = [{'id': g['id'], 'text': g['text']} for g in original if g['chapter_id'] == chapter]
        def check(value, ctx):
            try:
                validate(value, ctx)
            except (ValueError, TypeError, KeyError, ValidationError) as exc:
                raise pipeline.G2Fault('EXPLANATION_LAYOUT', str(exc)) from exc
        plan = pipeline.generate_validated_plan(f'{chapter}-explanation', messages, SCHEMA, check, context)
        pages.extend(plan['pages'])
    return {'pages': pages, 'groups': input_groups, 'direction_version': 4,
            **({'assembly': assembly} if assembly else {})}


def compile_storyboard(pipeline, plan: dict, alignment: dict, saved: dict, assets: list[dict]) -> dict:
    """Combine paragraph pictures and native data/text scenes on the same measured timeline."""
    scenes = []
    data = {'data_chart', 'scatter_chart', 'big_number', 'price_change', 'bar_chart', 'flow_diagram'}
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
                       'headline': page.get('headline', props.get('title', group['title'])),
                       'narration_segment_ids': group['segment_ids']})
    diagrams = [s for s in scenes if s['component'] == 'flow_diagram']
    if diagrams:
        request = pipeline.ARTIFACTS / 'object-layout-request.json'
        pipeline.write_json(request, process_diagram.layout_request({'scenes': [{**s['props'], 'id': s['id']} for s in diagrams]}))
        raw = pipeline._run_fixed_command('object-layout', [pipeline.NODE, process_diagram.LAYOUT_SCRIPT, request], pipeline.PROJECT, timeout=60)
        layouts = json.loads(raw)
        pipeline.write_json(pipeline.ARTIFACTS / 'object-layout.json', layouts)
        by_id = {s['id']: s['layout'] for s in layouts['scenes']}
        for scene in diagrams:
            scene['props'] = {**scene['props'], 'layout': by_id[scene['id']]}
    board = motion.compile_storyboard({'scenes': scenes}, alignment, pipeline)
    board.update(image_assets=assets, presentation='explanation')
    from g2.shot_director import attach
    attach(board, saved, pipeline, alignment, plan['pages'])
    if plan.get('assembly'):
        from g2.scene_assembly import connect
        connect(board, plan['groups'], saved)
    if plan.get('direction_version') == 4 or any(p.get('story_role') for p in plan['pages']):
        from g2.story_direction import attach as attach_story
        attach_story(board, plan['pages'], plan['groups'], pipeline, saved.get('_story_entities'))
    pipeline.write_json(pipeline.ARTIFACTS / 'explanation-plan.json', plan)
    pipeline.write_json(pipeline.ARTIFACTS / 'storyboard-plan.json', board)
    return board


def replan_scene(pipeline, old: dict, narration: dict, ir: dict, instruction: str) -> dict:
    """Keep the existing screen revision entry point usable for mixed native scenes."""
    ids = old['narration_segment_ids']
    speech = [s for s in ir['narration']['segments'] if s['id'] in ids]
    group = {'id': old['id'], 'title': old['props'].get('title', old['purpose']),
             'text': ''.join(s['text'] for s in speech), 'segment_ids': ids, 'narration': speech, 'image_indices': []}
    images = old['props'].get('images', []) if old['component'] in {'object_showcase', 'material_collage'} else []
    if images:
        group.update(image_indices=list(range(len(images))), image_materials=describe(images))
    context = {'title': narration['title'], 'groups': [group], 'instruction': instruction}
    def check(value, ctx):
        try:
            validate(value, ctx)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            raise pipeline.G2Fault('EXPLANATION_LAYOUT', str(exc)) from exc
    plan = pipeline.generate_validated_plan(f"{old['id']}-explanation-revision", messages, SCHEMA, check, context)
    saved = {'_shot_profile': old['props'].get('shot_plan', {}).get('profile', 'balanced'),
             'image_materials': images,
             '_story_entities': [e for s in ir.get('scenes', []) for e in s['props'].get('story', {}).get('entities', [])]}
    if old['props'].get('story'):
        page = plan['pages'][0]
        page.setdefault('story_role', old['props']['story']['role'])
        page.setdefault('importance', old['props']['story']['importance'])
        page.setdefault('entities', [{k: e[k] for k in ('name', 'targets')} for e in old['props']['story']['entities']])
    assets = [{'id': image['image_asset_id'], 'path': image['image_path']} for image in images]
    result = compile_storyboard(pipeline, {**plan, 'groups': [group]}, {'rows': speech}, saved, assets)['scenes'][0]
    if old['props'].get('scene_flow'):
        result['props']['scene_flow'] = {**old['props']['scene_flow'],
            'entry': ('dissolve' if old['props']['scene_flow']['entry']=='carry'
                      and result['component']!=old['component'] else old['props']['scene_flow']['entry'])}
        if (result['component']==old['component'] and old['props'].get('shot_plan', {}).get('closing')=='hold'
                and result['props'].get('shot_plan')):
            result['props']['shot_plan']['closing'] = 'hold'
    if (old['props'].get('story', {}).get('recap')
            and result['props'].get('story', {}).get('role') == 'recap'):
        result['props']['story']['recap'] = old['props']['story']['recap']
    return result
