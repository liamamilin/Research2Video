"""Durable manual and discovered themes, separate from the existing feed topic imports."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import sqlite_util


def normal(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())


def similar(a: str, b: str) -> bool:
    a, b = normal(a), normal(b)
    if a == b:
        return True
    left, right = {a[i:i + 2] for i in range(len(a) - 1)}, {b[i:i + 2] for i in range(len(b) - 1)}
    return bool(left and right and len(left & right) / len(left | right) >= 0.64)


class ThemeStore:
    def __init__(self, state_dir: str):
        self.path = str(Path(state_dir) / "themes.db")
        with sqlite_util.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS themes (
                    id TEXT PRIMARY KEY, matrix TEXT NOT NULL, category TEXT NOT NULL,
                    title TEXT NOT NULL, angle TEXT NOT NULL, audience TEXT NOT NULL, notes TEXT NOT NULL,
                    origin TEXT NOT NULL, day TEXT NOT NULL, reason TEXT NOT NULL, signals TEXT NOT NULL,
                    status TEXT NOT NULL, script_id TEXT NOT NULL DEFAULT '', auto_video INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS themes_matrix_day ON themes(matrix,day,created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS themes_daily_category ON themes(matrix,category,day)
                    WHERE origin='discovered';
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    id TEXT PRIMARY KEY, matrix TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL,
                    started_at REAL NOT NULL, finished_at REAL, error TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS theme_feedback (
                    id INTEGER PRIMARY KEY, theme_id TEXT NOT NULL, action TEXT NOT NULL,
                    reason TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL
                );
            ''')
        with sqlite_util.connect(self.path, write=True) as db:
            columns = {r['name'] for r in db.execute('PRAGMA table_info(themes)')}
            for name, spec in (("ranking", "TEXT NOT NULL DEFAULT '{}'"), ("edited", "INTEGER NOT NULL DEFAULT 0"),
                               ("inspiration", "TEXT NOT NULL DEFAULT '{}'"), ("used_at", "REAL NOT NULL DEFAULT 0"),
                               ("feedback", "TEXT NOT NULL DEFAULT '{}'")):
                if name not in columns:
                    db.execute(f'ALTER TABLE themes ADD COLUMN {name} {spec}')
            db.execute("UPDATE themes SET used_at=updated_at WHERE script_id!='' AND used_at=0")

    @staticmethod
    def decode(row) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        value["signals"] = json.loads(value["signals"])
        value["auto_video"] = bool(value["auto_video"])
        value["ranking"] = json.loads(value.get("ranking") or "{}")
        value["inspiration"] = json.loads(value.get("inspiration") or "{}")
        value["feedback"] = json.loads(value.get("feedback") or "{}")
        return value

    def get(self, key: str) -> dict | None:
        with sqlite_util.connect(self.path) as db:
            return self.decode(db.execute("SELECT * FROM themes WHERE id=?", (key,)).fetchone())

    def list(self, matrix: str, *, days: int = 30, limit: int = 200) -> list[dict]:
        with sqlite_util.connect(self.path) as db:
            return [self.decode(row) for row in db.execute(
                "SELECT * FROM themes WHERE matrix=? AND created_at>=? ORDER BY created_at DESC LIMIT ?",
                (matrix, time.time() - days * 86400, limit))]

    def create(self, matrix: str, category: str, title: str, *, angle: str = "", audience: str = "",
               notes: str = "", origin: str = "manual", day: str = "", reason: str = "", signals=None) -> dict:
        now, key = time.time(), uuid.uuid4().hex
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("INSERT INTO themes(id,matrix,category,title,angle,audience,notes,origin,day,reason,signals,status,script_id,auto_video,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'',0,?,?)",
                       (key, matrix, category, title.strip(), angle, audience, notes, origin, day, reason,
                        json.dumps(signals or [], ensure_ascii=False), "recommended" if origin == "discovered" else "ready",
                        now, now))
        return self.get(key)

    def history(self, matrix: str, *, offset: int = 0, limit: int = 100) -> dict:
        """Used topics remain available across all dates, including work made elsewhere."""
        with sqlite_util.connect(self.path) as db:
            total = db.execute("SELECT count(*) FROM themes WHERE matrix=? AND used_at>0", (matrix,)).fetchone()[0]
            rows = [self.decode(r) for r in db.execute("SELECT * FROM themes WHERE matrix=? AND used_at>0 "
                    "ORDER BY used_at DESC,id LIMIT ? OFFSET ?", (matrix, limit, offset))]
        return {"themes": rows, "total": total, "next_offset": offset + limit if offset + limit < total else None}

    def exclusions(self, matrix: str) -> list[dict]:
        with sqlite_util.connect(self.path) as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM themes WHERE matrix=? ORDER BY used_at DESC,created_at DESC", (matrix,))]

    def mark_used(self, key: str) -> dict:
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE themes SET used_at=CASE WHEN used_at>0 THEN used_at ELSE ? END, "
                       "status=CASE WHEN script_id='' THEN 'used' ELSE status END,updated_at=? WHERE id=?", (time.time(), time.time(), key))
            self._record_feedback(db, key, "selected", "", "")
        return self.get(key)

    @staticmethod
    def _record_feedback(db, key: str, action: str, reason: str, note: str) -> None:
        latest = db.execute("SELECT action,reason,note FROM theme_feedback WHERE theme_id=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        if latest and tuple(latest) == (action, reason, note):
            return
        db.execute("INSERT INTO theme_feedback(theme_id,action,reason,note,created_at) VALUES (?,?,?,?,?)",
                   (key, action, reason, note, time.time()))

    def feedback(self, key: str, action: str, reason: str = "", note: str = "") -> dict:
        if action not in {"interested", "dismissed", "clear"}:
            raise ValueError("请选择支持的反馈")
        if reason not in {"", "not_relevant", "too_generic", "hard_to_show", "not_now"}:
            raise ValueError("请选择支持的忽略原因")
        if action != "dismissed":
            reason = ""
        now = time.time()
        with sqlite_util.connect(self.path, write=True) as db:
            changed = db.execute("UPDATE themes SET feedback=?,status=?,updated_at=? WHERE id=? AND script_id='' AND used_at=0",
                                (json.dumps({"action": action, "reason": reason, "note": note[:1000], "at": now}, ensure_ascii=False),
                                 "dismissed" if action == "dismissed" else "ready", now, key)).rowcount
            if not changed:
                raise ValueError("主题已进入制作")
            self._record_feedback(db, key, action, reason, note[:1000])
        return self.get(key)

    def feedback_profile(self, matrix: str) -> dict:
        """Count each topic once, so repeated clicks cannot amplify a preference."""
        groups: dict[tuple, dict] = {}
        count = 0
        for row in self.exclusions(matrix):
            feedback = row["feedback"]
            action = "selected" if row["used_at"] else feedback.get("action")
            if action not in {"selected", "interested", "dismissed"}:
                continue
            count += 1
            key = row["category"], row["inspiration"].get("pattern_id", "")
            item = groups.setdefault(key, {"category": key[0], "pattern_id": key[1], "selected": 0,
                                           "interested": 0, "reasons": {}, "notes": []})
            if action == "dismissed":
                reason = feedback.get("reason") or "not_now"
                item["reasons"][reason] = item["reasons"].get(reason, 0) + 1
            else:
                item[action] += 1
            if feedback.get("note") and len(item["notes"]) < 3:
                item["notes"].append(feedback["note"][:200])
        return {"topics": count, "groups": list(groups.values())}

    def publish_generated(self, matrix: str, day: str, candidates: list[dict]) -> list[dict]:
        """Keep all proposals; topic questions are unique while formats remain reusable."""
        made = []
        with sqlite_util.connect(self.path, write=True) as db:
            history = [self.decode(r) for r in db.execute("SELECT * FROM themes WHERE matrix=?", (matrix,))]
            for item in candidates:
                inspiration = item["inspiration"]
                question, topic_key = inspiration["question"], normal(inspiration["topic_key"])
                if any(similar(item["title"], old["title"]) or similar(question, old["inspiration"].get("question") or old["title"])
                       or (topic_key and topic_key == normal(old["inspiration"].get("topic_key", ""))) for old in history):
                    continue
                key, now = uuid.uuid4().hex, time.time()
                db.execute("INSERT INTO themes(id,matrix,category,title,angle,audience,notes,origin,day,reason,signals,status,"
                           "created_at,updated_at,inspiration) VALUES (?,?,?,?,?,?,?,'generated',?,?,?,'recommended',?,?,?)",
                           (key, matrix, item["category"], item["title"], item.get("angle", ""), item.get("audience", ""),
                            item.get("notes", ""), day, item["reason"], json.dumps(item.get("signals", []), ensure_ascii=False), now, now,
                            json.dumps(inspiration, ensure_ascii=False)))
                history.append({**item, "inspiration": inspiration})
                made.append(key)
        return [self.get(key) for key in made]

    def edit(self, key: str, *, title: str, angle: str, audience: str, notes: str) -> dict:
        with sqlite_util.connect(self.path, write=True) as db:
            changed = db.execute("UPDATE themes SET title=?,angle=?,audience=?,notes=?,updated_at=?,edited=1 "
                                 "WHERE id=? AND script_id='' AND used_at=0 AND status IN ('ready','recommended','dismissed')",
                                 (title.strip(), angle, audience, notes, time.time(), key)).rowcount
            if not changed:
                raise ValueError("主题已进入制作，请在关联文稿中修改")
        return self.get(key)

    def dismiss(self, key: str, dismissed: bool) -> dict:
        return self.feedback(key, "dismissed" if dismissed else "clear", "not_now" if dismissed else "")

    def attach(self, key: str, script_id: str, auto_video: bool) -> dict:
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE themes SET script_id=?,auto_video=?,status='started',updated_at=?,used_at=? WHERE id=?",
                       (script_id, int(auto_video), time.time(), time.time(), key))
            self._record_feedback(db, key, "selected", "", "")
        return self.get(key)

    def publish(self, matrix: str, day: str, candidates: list[dict], *, refresh: bool = False) -> list[dict]:
        """One slot per category; reruns and recent similar titles never displace a chosen theme."""
        made = []
        with sqlite_util.connect(self.path, write=True) as db:
            previous = db.execute("SELECT * FROM themes WHERE matrix=? AND (created_at>=? OR used_at>0)",
                                  (matrix, time.time() - 14 * 86400)).fetchall()
            replaceable = {r["category"]: r for r in previous if refresh and r["day"] == day
                           and r["origin"] == "discovered" and r["status"] == "recommended" and not r["script_id"] and not r["edited"]}
            replace_ids = {r["id"] for r in replaceable.values()}
            titles, replaced = [], set()
            occupied = {row["category"] for row in previous if row["origin"] == "discovered" and row["day"] == day and row["id"] not in replace_ids}
            for item in candidates:
                old = replaceable.get(item["category"])
                history = [r["title"] for r in previous if r["id"] not in replaced and (not old or r["id"] != old["id"])]
                if item["category"] in occupied or any(similar(item["title"], t) for t in history + titles):
                    continue
                key, now = uuid.uuid4().hex, time.time()
                if old:
                    key = old["id"]
                    replaced.add(key)
                    db.execute("UPDATE themes SET title=?,angle=?,audience=?,reason=?,signals=?,ranking=?,updated_at=? WHERE id=?",
                               (item["title"], item.get("angle", ""), item.get("audience", ""), item["reason"],
                                json.dumps(item["signals"], ensure_ascii=False), json.dumps(item.get("ranking", {})), now, key))
                else:
                    db.execute("INSERT INTO themes(id,matrix,category,title,angle,audience,notes,origin,day,reason,signals,status,script_id,auto_video,created_at,updated_at,ranking) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'',0,?,?,?)",
                           (key, matrix, item["category"], item["title"], item.get("angle", ""),
                            item.get("audience", ""), "", "discovered", day, item["reason"],
                            json.dumps(item["signals"], ensure_ascii=False), "recommended", now, now, json.dumps(item.get("ranking", {}))))
                titles.append(item["title"])
                occupied.add(item["category"])
                made.append(key)
        return [self.get(key) for key in made]

    def begin_run(self, matrix: str, day: str) -> str:
        key = uuid.uuid4().hex
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE discovery_runs SET status='interrupted',finished_at=?,error=? WHERE matrix=? AND status='running'",
                       (time.time(), "前一次发现已中断，已开始新一轮；历史推荐保留", matrix))
            db.execute("INSERT INTO discovery_runs(id,matrix,day,status,started_at) VALUES (?,?,?,'running',?)",
                       (key, matrix, day, time.time()))
        return key

    def finish_run(self, key: str, status: str, result: dict, error: str = "") -> None:
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE discovery_runs SET status=?,finished_at=?,result=?,error=? WHERE id=?",
                       (status, time.time(), json.dumps(result, ensure_ascii=False), error, key))

    def runs(self, matrix: str) -> list[dict]:
        with sqlite_util.connect(self.path) as db:
            return [{**dict(row), "result": json.loads(row["result"])} for row in db.execute(
                "SELECT * FROM discovery_runs WHERE matrix=? ORDER BY started_at DESC LIMIT 15", (matrix,))]

    def active(self) -> list[dict]:
        with sqlite_util.connect(self.path) as db:
            return [self.decode(row) for row in db.execute("SELECT * FROM themes WHERE script_id!='' AND auto_video=1 AND status='started'")]

    def mark(self, key: str, status: str) -> None:
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE themes SET status=?,updated_at=? WHERE id=?", (status, time.time(), key))
