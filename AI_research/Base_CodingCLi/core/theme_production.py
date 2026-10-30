"""Read-only planning of today's recommended themes against durable column quotas."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

import yaml

from core import matrices, theme_context
from core.fileio import atomic_write, file_lock
from core.yaml_cache import load_yaml
from core.theme_store import normal

ZONE = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


def day(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, ZONE).date().isoformat()


def window(period: str, now: float) -> float:
    start = datetime.fromtimestamp(now, ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start -= timedelta(days=start.weekday())
    return start.timestamp()


def configuration(config_dir: str, matrix: str) -> dict:
    categories = theme_context.categories(config_dir, matrix)
    path = Path(config_dir) / "theme_production" / f"{matrix}.yaml"
    raw = load_yaml(path.read_text()) if path.is_file() else {}
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("推荐自动出片配置应为映射")
    configured = raw.get("columns") or {}
    if not isinstance(configured, dict) or any(not isinstance(rule, dict) for rule in configured.values()):
        raise ValueError("栏目配额应为映射")
    columns = {}
    for category in categories:
        kind = category["type"]
        default = {"period": "day" if kind == "news" else "week", "count": 2 if kind == "tutorial" else 1}
        rule = {**default, **configured.get(category["key"], {})}
        if rule["period"] not in ("day", "week") or type(rule["count"]) is not int or not 0 <= rule["count"] <= 5:
            raise ValueError("栏目配额应为每日/每周 0–5 条")
        columns[category["key"]] = {**rule, "name": category["name"]}
    enabled = raw.get("enabled", False)
    limit = raw.get("daily_limit", 2 if matrix in ("ai_media", "chatgpt_acquisition") else 1)
    minutes = raw.get("minutes", 4)
    if type(enabled) is not bool or type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("请选择是否启用及每天 1–5 条的推荐补充上限")
    if type(minutes) not in (int, float) or not 2 <= minutes <= 8 or minutes * 2 != int(minutes * 2):
        raise ValueError("参考时长应为 2–8 分钟，以半分钟递增")
    return {"enabled": enabled, "daily_limit": limit, "minutes": minutes,
            "starts_on": raw.get("starts_on", ""), "timezone": "Asia/Shanghai", "columns": columns}


def save(config_dir: str, matrix: str, fields: dict) -> dict:
    current = configuration(config_dir, matrix)
    unknown = set(fields.get("columns", {})) - set(current["columns"])
    if unknown:
        raise ValueError("配置中含已移除的栏目")
    for rule in fields.get("columns", {}).values():
        if set(rule) - {"period", "count"} or rule.get("period") not in ("day", "week") or type(rule.get("count")) is not int or not 0 <= rule["count"] <= 5:
            raise ValueError("栏目配额应为每日/每周 0–5 条")
    if type(fields.get("enabled")) is not bool or type(fields.get("daily_limit")) is not int or not 1 <= fields["daily_limit"] <= 5:
        raise ValueError("请选择是否启用及每天 1–5 条的推荐补充上限")
    minutes = fields.get("minutes", current["minutes"])
    if type(minutes) not in (int, float) or not 2 <= minutes <= 8 or minutes * 2 != int(minutes * 2):
        raise ValueError("参考时长应为 2–8 分钟，以半分钟递增")
    path = Path(config_dir) / "theme_production" / f"{matrix}.yaml"
    with file_lock(str(path)):
        record = {"enabled": fields["enabled"], "daily_limit": fields["daily_limit"], "minutes": minutes,
                  "starts_on": current["starts_on"] if current["enabled"] else day(),
                  "columns": {key: {k: rule[k] for k in ("period", "count")} for key, rule in current["columns"].items()}}
        record["columns"].update(fields.get("columns", {}))
        atomic_write(str(path), yaml.safe_dump(record, allow_unicode=True, sort_keys=False))
    return configuration(config_dir, matrix)


def reservations(state_dir: str) -> list[dict]:
    path = Path(state_dir) / "video.db"
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        return [{**dict(row), "topic": json.loads(row["topic"])} for row in db.execute(
            "SELECT i.id,i.topic,i.script_id,i.state,i.note,r.started_at FROM autopilot_items i"
            " JOIN autopilot_runs r ON r.id=i.run_id WHERE json_extract(i.topic,'$.theme_task_id') IS NOT NULL")]


def covered_questions(config_dir: str, state_dir: str, matrix: str, now: float) -> dict[str, str]:
    """Avoid researching an identical known question; semantic event matching still runs before writing."""
    path = Path(state_dir) / 'video.db'
    if not path.is_file():
        return {}
    owner = matrices.load_all(config_dir).get(matrix)
    if owner is None:
        return {}
    cutoff = datetime.fromtimestamp(now - 30 * 86400, ZONE).date().isoformat()
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT s.id,s.topic,v.title FROM scripts s LEFT JOIN script_versions v"
                          " ON v.script_id=s.id AND v.version=s.current_version WHERE s.created_at>=?"
                          " AND s.created_by NOT IN ('chain-validation','prompt-quality-validation','多矩阵质量验收')"
                          " AND COALESCE(json_extract(s.topic,'$.test_sample'),0)=0"
                          " AND ((s.archived=0 AND s.status!='FAILED') OR EXISTS (SELECT 1 FROM publications p WHERE p.script_id=s.id))"
                          " AND (s.content_matrix_id=? OR s.source_matrix_id=? OR json_extract(s.topic,'$.matrix_dedup.matrix')=?"
                          " OR json_extract(s.topic,'$.matrix_dedup.job') IN (SELECT value FROM json_each(?)))",
                          (cutoff, matrix, matrix, matrix, json.dumps(owner.jobs))).fetchall()
    covered = {}
    for row in rows:
        topic = json.loads(row['topic'])
        for text in (row['title'], topic.get('title'), topic.get('theme_question'), topic.get('theme_key')):
            if text and normal(text):
                covered[normal(text)] = row['id']
    return covered


def plan(config_dir: str, state_dir: str, matrix: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    config = configuration(config_dir, matrix)
    result = {"configuration": config, "selected": [], "states": {}, "daily_used": 0}
    path = Path(state_dir) / "themes.db"
    if not path.is_file():
        return result
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        themes = [dict(row) for row in db.execute("SELECT * FROM themes WHERE matrix=? AND day=? ORDER BY created_at,id", (matrix, day(now)))]
    records = reservations(state_dir)
    claimed = {row['topic']['theme_task_id']: row for row in records}
    mine = [row for row in records if (row['topic'].get('theme_auto') or {}).get('matrix') == matrix and row['state'] != 'CANCELLED']
    daily_used = sum(row['started_at'] >= window('day', now) for row in mine)
    result['daily_used'] = daily_used
    counts = {key: sum(row['started_at'] >= window(rule['period'], now) and
                       row['topic']['theme_auto']['category'] == key for row in mine)
              for key, rule in config['columns'].items()}
    last = {key: max((row['started_at'] for row in mine if row['topic']['theme_auto']['category'] == key), default=0)
            for key in config['columns']}
    for row in themes:
        row['ranking'] = json.loads(row.get('ranking') or '{}')
        row['feedback'] = json.loads(row.get('feedback') or '{}')
    themes.sort(key=lambda row: (row['feedback'].get('action') != 'interested', last.get(row['category'], 0),
                                 -row['ranking'].get('score', 0), row['created_at'], row['id']))
    remaining = config['daily_limit'] - daily_used
    covered = covered_questions(config_dir, state_dir, matrix, now)
    for theme in themes:
        key, ident = theme['category'], theme['id']
        if ident in claimed:
            item = claimed[ident]
            result['states'][ident] = {"state": "skipped" if item['state'] == 'CANCELLED' else "done" if item['state'] == 'DONE' else "attention" if item['state'] in ('FAILED', 'HELD') else "queued" if not item['script_id'] else "working", "note": item['note']}
            continue
        if theme['script_id'] or theme['used_at'] or theme['status'] not in ('ready', 'recommended') or theme['origin'] not in ('generated', 'discovered'):
            continue
        if not config['enabled'] or (config['starts_on'] and day(now) < config['starts_on']):
            result['states'][ident] = {"state": "disabled", "note": "推荐自动出片未开启，可手动研究制作"}
            continue
        if key not in config['columns']:
            result['states'][ident] = {"state": "disabled", "note": "该栏目已停用"}
            continue
        inspiration = json.loads(theme.get('inspiration') or '{}')
        peer = next((covered[normal(text)] for text in (theme['title'], inspiration.get('question', ''), inspiration.get('topic_key', ''))
                     if text and normal(text) in covered), None)
        if peer:
            result['states'][ident] = {"state": "skipped", "note": "同矩阵已有相同问题，已跳过重复研究", "script_id": peer}
            continue
        rule = config['columns'][key]
        if counts[key] >= rule['count'] or not rule['count']:
            result['states'][ident] = {"state": "quota", "note": f"本栏目{'今日' if rule['period'] == 'day' else '本周'}推荐配额已用完"}
        elif remaining <= 0:
            result['states'][ident] = {"state": "quota", "note": "今日推荐补充配额已用完"}
        else:
            result['selected'].append(ident)
            result['states'][ident] = {"state": "ready", "note": "等待自动领取，随后按制作队列执行"}
            remaining -= 1
            counts[key] += 1
    return result


def pending(config_dir: str, state_dir: str, now: float | None = None) -> list[dict]:
    found = []
    for matrix in matrices.load_all(config_dir).values():
        if matrix.archived or not (Path(config_dir) / 'theme_production' / f'{matrix.key}.yaml').is_file():
            continue
        try:
            snapshot = plan(config_dir, state_dir, matrix.key, now)
        except (OSError, ValueError, sqlite3.Error):
            logger.exception('推荐出片配置暂不可用：%s', matrix.key)
            continue
        found += [{"matrix": matrix.key, "theme_id": key, "minutes": snapshot['configuration']['minutes']}
                  for key in snapshot['selected']]
    return found
