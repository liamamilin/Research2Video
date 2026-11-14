"""Describe delivered pictures for reviews, keeping requests separate from observed scene use."""
from __future__ import annotations

from collections import Counter
import hashlib
import json


def snapshot(version: dict, request: dict, board: dict | None) -> dict:
    meta = version.get('meta') or {}
    direction = meta.get('programme_direction', meta.get('creative_direction')) or ''
    prompt = meta.get('prompt_version')
    signature = hashlib.sha256(json.dumps({'prompt': prompt, 'direction': direction},
                                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]
    programme = f'p{prompt}-{signature}' if prompt is not None else ''
    scenes = board.get('scenes', []) if board is not None else None
    images, diagram_chapters = set(), set()
    material_scenes = 0
    for scene in scenes or []:
        props = scene.get('props') or {}
        if props.get('layout') == 'diagram':
            diagram_chapters.add(props.get('chapter_index'))
        if scene.get('component') in {'object_showcase', 'material_collage', 'photo_slide'}:
            material_scenes += 1
            for image in [props.get('image') or {}, *(props.get('images') or [])]:
                if key := image.get('image_asset_id') or image.get('image_path'):
                    images.add(key)
    return {'programme': programme, 'writer_prompt_version': prompt,
            'requested_images': len(request.get('image_materials') or []),
            'material_scenes': material_scenes if scenes is not None else None,
            'used_images': len(images) if scenes is not None else None,
            'components': dict(Counter(s.get('component', '') for s in scenes or [])),
            'diagram_chapters': len(diagram_chapters) if scenes is not None else None,
            'evidence': 'storyboard' if scenes is not None else 'unavailable',
            'material_usage': ('未读取成片分镜' if scenes is None else '含素材画面' if material_scenes else '无素材画面')}
