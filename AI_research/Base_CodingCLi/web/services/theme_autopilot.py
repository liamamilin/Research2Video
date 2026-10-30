"""Reserve recommended themes in the existing durable video queue."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import time
import logging

from core import theme_production
from core.fileio import file_lock
from core.video import autopilot_store as ap
from web.services import theme_workbench
from web.settings import get_settings


def fire(now: float | None = None) -> int:
    paths = get_settings().paths
    try:
        with file_lock(str(Path(paths.state_dir) / 'theme-auto-claim'), timeout=0):
            return _fire(now)
    except TimeoutError:
        return 0


def _fire(now: float | None = None) -> int:
    paths = get_settings().paths
    now = time.time() if now is None else now
    count = 0
    for candidate in theme_production.pending(paths.config_dir, paths.state_dir, now):
        try:
            count += _claim(candidate, now)
        except (OSError, ValueError, LookupError, sqlite3.Error):
            logging.getLogger(__name__).exception('推荐领取暂未完成：%s', candidate['theme_id'])
    return count


def _claim(candidate: dict, now: float) -> int:
    paths = get_settings().paths
    key, theme_id = candidate['matrix'], candidate['theme_id']
    with file_lock(str(Path(paths.state_dir) / 'theme-start' / theme_id)):
        current = theme_production.plan(paths.config_dir, paths.state_dir, key, now)
        if theme_id not in current['selected']:
            return 0
        theme = theme_workbench.require_theme(key, theme_id)
        recipe, topic = theme_workbench.prepare(key, theme, candidate['minutes'])
        topic['theme_auto'] = {'matrix': key, 'category': theme['category'], 'day': theme_production.day(now)}
        item = {'column_key': recipe, 'topic': topic, 'production': topic.get('_production', '')}
        try:
            ap.create_run({'picks': [], 'refresh': False, 'recommended_theme': theme_id}, [item],
                          started_by='autopilot', stage='ITEMS')
        except sqlite3.IntegrityError:
            return 0
        theme_workbench.storage().mark(theme_id, 'queued')
        return 1
