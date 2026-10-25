"""Explainable, opt-in suggestions from supplied material, without inference calls."""
from __future__ import annotations

from g2.creative_library import recipe


def recommend(source_text: str, recipe_id: str | None, images: list[dict], music: list[dict]) -> dict:
    definition = recipe(recipe_id) if recipe_id else None
    forms = []
    if images:
        roles = {row.get('role') for row in images}
        if roles & {'overall', 'detail', 'comparison'}:
            forms.append({'id': 'object_showcase', 'name': '对象展示',
                          'reason': '已有整体、细节或对比标注，可围绕同一对象组织图片；按提交顺序展示。'})
        forms.append({'id': 'dynamic_collage', 'name': '动态拼贴',
                      'reason': f'已有 {len(images)} 张图片，可与讲稿要点组合成版面，无需录屏或拍摄。'})
        if not roles & {'overall', 'detail', 'comparison'}:
            forms.append({'id': 'image_slideshow', 'name': '图片轮播', 'reason': '图片尚无对象标注，可按顺序做图文讲解。'})
    else:
        default = (definition or {}).get('defaults', {}).get('video_form', 'kinetic_typography')
        names = {'kinetic_typography': '动态排版', 'process_diagram': '流程动态图解', 'auto': '按素材自动选择'}
        forms.append({'id': default, 'name': names.get(default, default),
                      'reason': '当前只有文稿，优先使用已有文字／图解形式；对象展示和动态拼贴需要图片。'})
    target = (definition or {}).get('name', '')
    style = (definition or {}).get('music', {}).get('generation_style', 'ambient')
    default_asset = (definition or {}).get('music', {}).get('asset_id', 'ambient_pad')
    candidates = []
    for asset in music:
        if not asset.get('available'):
            continue
        generation = asset.get('generation', {}).get('request', {})
        # Imports remain manually selectable. They are not assumed to be instrumental.
        if asset.get('kind') != 'builtin' and not (asset.get('kind') == 'generated' and generation.get('instrumental') is True):
            continue
        score = 0
        reasons = []
        curated = asset.get('curation') or {}
        review = curated.get('review') or {}
        technical = curated.get('technical') or {}
        if review.get('status') == 'needs_work' or technical.get('status') == 'needs_work':
            continue
        if curated.get('common') and recipe_id in curated.get('recipe_ids', []):
            score += 10; reasons.append('已整理为本题材常用配乐')
        if review.get('status') == 'passed':
            score += 2; reasons.append('已完成试听确认')
        topics = asset.get('topics', [])
        if target and target.replace(' ', '') in {t.replace(' ', '') for t in topics}:
            score += 4; reasons.append('题材标签与配方匹配')
        if asset['id'] == default_asset:
            score += 3; reasons.append('配方已有的无词配乐')
        if generation.get('style') == style:
            score += 2; reasons.append('生成风格与配方方向匹配')
        if any(topic in source_text for topic in topics if len(topic) >= 2):
            score += 1; reasons.append('文稿包含相关题材词')
        if not reasons:
            reasons.append('已有无词配乐，可试听后复用')
        candidates.append({'id':asset['id'], 'name':asset['name'], 'preview_url':asset['preview_url'],
                           'kind':asset['kind'], 'reason':'；'.join(reasons),
                           'review_status':review.get('status','pending'), '_score':score})
    candidates.sort(key=lambda item:(-item['_score'],item['id']))
    return {'schema_version':1, 'forms':forms, 'music':[{k:v for k,v in item.items() if k!='_score'} for item in candidates[:3]],
            'basis': '基于已提交图片的数量／标注、配方和音乐标签；未识别图片内容，未调用模型。',
            'music_note': '优先复用已入库的无词配乐；生成条件不等于人工听审，使用前可试听。',
            'automatic_changes':False}
