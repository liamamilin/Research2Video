"""Read saved manuscript and render versions side by side, without regenerating them."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException

from core.video import store
from web.services.assistant_video_state import duration


def _snapshot(version: dict) -> dict:
    meta = version.get('meta') or {}
    cell = meta.get('cell') or {}
    return {'recipe': cell.get('recipe'), 'recipe_sha256': cell.get('resolved_sha256') or cell.get('sha256'),
            'parts': cell.get('uses') or {}, 'creative_direction': meta.get('creative_direction'),
            'report_path': meta.get('report_path')}


def _text(section: dict) -> str:
    return '\n'.join(p.get('text') or '' for p in section.get('paragraphs', []))


def compare(script: dict, before: int, after: int, renders: list[dict], before_render: str = '', after_render: str = '') -> dict:
    versions = [store.get_version(script['id'], number) for number in (before, after)]
    if any(v is None for v in versions):
        raise HTTPException(404, '对比的文稿版本不存在')

    def side(version, render_id):
        number = version['version']
        choices = [r for r in renders if r['version'] == number]
        if render_id:
            selected = next((r for r in choices if r['id'] == render_id), None)
            if selected is None:
                raise HTTPException(422, '所选成片不属于这篇文稿的对应版本')
        else:
            selected = next((r for r in choices if r['status'] == 'COMPLETED'), choices[0] if choices else None)
        snapshot = _snapshot(version)
        report = snapshot['report_path'] or script.get('report_path')
        return {'version': number, 'title': version['title'], 'sections': version['sections'],
                'created_at': version['created_at'], 'duration': duration(version), 'configuration': snapshot,
                'report': {'path': report, 'href': '/reports/view?path=' + quote(report, safe='')} if report else None,
                'render': selected, 'renders': choices,
                'script_href': f"/video/{script['id']}?tab=draft&version={number}"}

    left, right = side(versions[0], before_render), side(versions[1], after_render)
    sections = []
    for i in range(max(len(left['sections']), len(right['sections']))):
        a = left['sections'][i] if i < len(left['sections']) else None
        b = right['sections'][i] if i < len(right['sections']) else None
        changed = a is None or b is None or a.get('title') != b.get('title') or _text(a) != _text(b)
        sections.append({'index': i, 'changed': changed, 'before': a, 'after': b})
    old, new = left['configuration'], right['configuration']
    changed = [key for key in ('recipe', 'recipe_sha256', 'creative_direction', 'parts') if old[key] != new[key]]
    href = f"/video/{script['id']}?tab=compare&compare={before}&version={after}"
    if left['render']:
        href += '&before_render=' + left['render']['id']
    if right['render']:
        href += '&render=' + right['render']['id']
    return {'script_id': script['id'], 'before': left, 'after': right, 'sections': sections,
            'changes': {'title': left['title'] != right['title'], 'changed_sections': sum(s['changed'] for s in sections),
                        'configuration_fields': changed, 'same_report': left['report']['path'] == right['report']['path'] if left['report'] and right['report'] else None},
            'href': href, 'note': '返回已保存的正文、配置及成片记录和播放链接；未分析视频画面或音频。时间和文本变化供阅读参考，不据此评定内容质量。'}
