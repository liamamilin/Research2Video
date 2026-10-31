"""Read-only, current-version progress for the topics visible in the picker."""
from __future__ import annotations

import json

from core.video import recipes, store
from web.services import video_render

_HEADERS = "s.id, s.column_key, s.topic, s.status, s.current_version, s.created_at, s.archived"


def selection_skips(scripts: list[dict]) -> dict[str, dict]:
    """Explain explicit dedup archives, including legacy FAILED rows, without rewriting history."""
    skipped = {s['id']: (s['topic'].get('content_archive') or {}) for s in scripts
               if s.get('archived') and s['topic'].get('content_archive')}
    if not skipped:
        return {}
    ids = sorted({sid for archive in skipped.values() for sid in archive.get('duplicate_of', [])})
    with store.connect() as conn:
        originals = [dict(r) for r in conn.execute(
            f"SELECT {_HEADERS}, COALESCE(v.title,json_extract(s.topic,'$.title'),'已有内容') AS title"
            " FROM scripts s LEFT JOIN script_versions v ON v.script_id=s.id AND v.version=s.current_version"
            " WHERE s.id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))]
    progress = _progress_rows(originals)
    titles = {r['id']: r['title'] for r in originals}
    return {sid: {'kind': 'duplicate' if archive.get('duplicate_of') else 'exhausted',
                  'reason': archive.get('reason', ''),
                  'originals': [{**progress[original], 'title': titles[original]}
                                for original in dict.fromkeys(archive.get('duplicate_of', [])) if original in progress]}
            for sid, archive in skipped.items()}


def scripts_by_topic(topic_ids: list[str]) -> dict[str, list[dict]]:
    """Include failed drafts for display without changing automatic topic selection.

    Only read matching script headers and render metadata, never narration or
    frozen requests. A roundup belongs to each of its selected topics.
    """
    if not topic_ids:
        return {}
    ids = set(topic_ids)
    encoded = json.dumps(sorted(ids))
    with store.connect() as conn:
        scripts = [dict(r) for r in conn.execute(
            f"SELECT {_HEADERS} FROM scripts s"
            " WHERE json_extract(topic, '$.id') IN (SELECT value FROM json_each(?))"
            " OR EXISTS (SELECT 1 FROM json_each(topic, '$.items')"
            " WHERE value IN (SELECT value FROM json_each(?))) ORDER BY created_at DESC, id DESC",
            (encoded, encoded),
        )]
    for script in scripts:
        script['topic'] = json.loads(script['topic'])
    progress = _progress_rows(scripts)
    skips = selection_skips(scripts)
    covered: dict[str, list[dict]] = {}
    for script in scripts:
        row = progress[script['id']]
        if script['id'] in skips:
            row['selection_skip'] = skips[script['id']]
        topic = script['topic']
        for topic_id in ({topic.get('id'), *(topic.get('items') or [])} - {None}) & ids:
            covered.setdefault(topic_id, []).append(row)
    return covered


def _progress_rows(scripts: list[dict]) -> dict[str, dict]:
    """Only expose current-version progress and playable files for these headers."""
    if not scripts:
        return {}
    with store.connect() as conn:
        renders: dict[str, list[dict]] = {}
        for row in conn.execute(
            "SELECT id, script_id, version, status, artifacts FROM renders"
            " WHERE script_id IN (SELECT value FROM json_each(?)) ORDER BY created_at DESC, id DESC",
            (json.dumps([s['id'] for s in scripts]),),
        ):
            renders.setdefault(row['script_id'], []).append(dict(row))
    result = {}
    for script in scripts:
        records = renders.get(script['id'], [])
        current = [r for r in records if r['version'] == script['current_version']]
        active = next((r for r in current if r['status'] in store.ACTIVE_RENDER_STATUSES), None)
        # Existing current-version video survives a failed retry or another render in flight.
        def playable(record: dict) -> bool:
            if record['status'] != 'COMPLETED':
                return False
            record['artifacts'] = json.loads(record['artifacts']) if isinstance(record['artifacts'], str) else record['artifacts']
            return video_render.safe_file(record, 'video.mp4') is not None

        video = next((r for r in current if playable(r)), None)
        previous = next((r for r in records if r['version'] < script['current_version'] and playable(r)), None)
        render = active or video or next(iter(current), None)
        state = ('done' if video else 'working' if active else
                 'failed' if render and render['status'] in ('FAILED', 'CANCELLED', 'COMPLETED')
                 else 'none')
        row = {k: script[k] for k in ('id', 'column_key', 'status', 'created_at', 'archived')}
        variant = recipes.split_variant_key(script['column_key'])
        row.update(cell_key=variant[0] if variant else script['column_key'],
                   version=script['current_version'], render_state=state,
                   render={k: render[k] for k in ('id', 'status', 'version')} if render else None,
                   video_url=f"/api/video/renders/{video['id']}/files/video.mp4" if video else None,
                   previous_video={'version': previous['version'],
                                   'url': f"/api/video/renders/{previous['id']}/files/video.mp4"} if previous else None)
        result[script['id']] = row
    return result
