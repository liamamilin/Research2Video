"""Recoverable configuration edits, coordinated with the schedule transaction."""

from __future__ import annotations

import base64
import json
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from core.fileio import atomic_write, file_lock

_ACTIVE = ContextVar("configuration_transaction", default=None)


def _bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _encode(raw: bytes | None) -> str | None:
    return base64.b64encode(raw).decode() if raw is not None else None


def before_write(path: str, content: str | None, encoding: str = "utf-8") -> None:
    """Record intent before a managed YAML replacement or removal."""
    active = _ACTIVE.get()
    target = Path(path).resolve()
    if active is None or target.suffix not in (".yaml", ".yml"):
        return
    if not any(target.is_relative_to(root) for root in active["roots"]):
        return
    key = str(target)
    row = active["data"]["files"].setdefault(key, {"before": _encode(_bytes(target))})
    if "after" in row:
        row.setdefault("intermediate", []).append(row["after"])
    row["after"] = _encode(content.encode(encoding) if content is not None else None)
    _save(active)


def remove(path: str | Path) -> None:
    before_write(str(path), None)
    os.remove(path)


def _save(active: dict) -> None:
    atomic_write(str(active["journal"]), json.dumps(active["data"], ensure_ascii=False))


def _undo(data: dict) -> list[str]:
    conflicts = []
    for name, item in reversed(list(data["files"].items())):
        path = Path(name)
        current = _encode(_bytes(path))
        if current == item["before"]:
            continue
        if current not in [item["after"], *item.get("intermediate", [])]:
            conflicts.append(name)
            continue
        if item["before"] is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(name, base64.b64decode(item["before"]).decode())
    return conflicts


def recover(state_dir: str, schedules) -> None:
    """Called under the configuration lock; retain committed changes after a crash."""
    for path in (Path(state_dir) / "config_changes").glob("*.json"):
        data = json.loads(path.read_text())
        if data["status"] in ("committed", "rolled_back"):
            continue
        with schedules.connect() as db:
            committed = db.execute(
                "SELECT 1 FROM scheduler_meta WHERE key=?",
                ("config_change:" + data["id"],),
            ).fetchone()
        conflicts = [] if committed else _undo(data)
        data["status"] = (
            "conflict" if conflicts else "committed" if committed else "rolled_back"
        )
        data["conflicts"] = conflicts
        atomic_write(str(path), json.dumps(data, ensure_ascii=False))
        if conflicts:
            raise RuntimeError("配置恢复存在外部修改，已保留：" + "、".join(conflicts))


@contextmanager
def transaction(state_dir: str, roots: list[Path], schedules):
    if _ACTIVE.get() is not None:
        yield
        return
    with file_lock(str(Path(state_dir) / "config-management"), timeout=30):
        recover(state_dir, schedules)
        identifier = uuid.uuid4().hex
        journal = Path(state_dir) / "config_changes" / (identifier + ".json")
        journal.parent.mkdir(parents=True, exist_ok=True)
        active = {
            "roots": [p.resolve() for p in roots],
            "journal": journal,
            "data": {"id": identifier, "status": "pending", "files": {}},
        }
        _save(active)
        token = _ACTIVE.set(active)
        try:
            with schedules.atomic() as db:
                active["data"]["schedules_before"] = [
                    dict(row) for row in db.execute("SELECT * FROM schedules")
                ]
                _save(active)
                yield
                active["data"]["schedules_after"] = [
                    dict(row) for row in db.execute("SELECT * FROM schedules")
                ]
                _save(active)
                db.execute(
                    "INSERT INTO scheduler_meta(key,value) VALUES(?,?)",
                    ("config_change:" + identifier, "committed"),
                )
        except BaseException:
            _ACTIVE.reset(token)
            conflicts = _undo(active["data"])
            active["data"].update(
                status="conflict" if conflicts else "rolled_back", conflicts=conflicts
            )
            _save(active)
            raise
        else:
            _ACTIVE.reset(token)
            active["data"]["status"] = "committed"
            _save(active)


def undo_change(state_dir: str, identifier: str, roots: list[Path], schedules) -> None:
    """Reverse one completed edit without rewinding execution history or newer edits."""
    import re
    import time
    from core.schedule_store import slot

    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("Invalid change identifier")
    journal = Path(state_dir) / "config_changes" / (identifier + ".json")
    data = json.loads(journal.read_text())
    if data["status"] != "committed":
        raise ValueError("Only a committed change can be reversed")
    before = {p["id"]: p for p in data.get("schedules_before", [])}
    after = {p["id"]: p for p in data.get("schedules_after", [])}
    fields = (
        "job_name",
        "schedule",
        "timezone",
        "enabled",
        "missed_policy",
        "max_retries",
        "version",
        "deleted",
    )
    changed = {
        key
        for key in before.keys() | after.keys()
        if {f: before.get(key, {}).get(f) for f in fields}
        != {f: after.get(key, {}).get(f) for f in fields}
    }
    with transaction(state_dir, roots, schedules):
        # New chain configuration is also the address book for reports/media
        # produced after rollout. Rolling back code must not remove that book.
        import sqlite3
        import yaml

        for name, item in data["files"].items():
            path = Path(name)
            if (
                item["before"] is not None
                or path.parent.name != "chains"
                or not path.exists()
            ):
                continue
            metadata = yaml.safe_load(path.read_text()) or {}
            outputs = metadata.get("outputs", []) + metadata.get("retired_outputs", [])
            has_history = False
            video = Path(state_dir) / "video.db"
            if video.exists() and outputs:
                with sqlite3.connect(
                    video.resolve().as_uri() + "?mode=ro", uri=True
                ) as db:
                    has_history = bool(
                        db.execute(
                            "SELECT 1 FROM scripts WHERE production IN ("
                            + ",".join("?" for _ in outputs)
                            + ") LIMIT 1",
                            outputs,
                        ).fetchone()
                    )
            ledger = Path(state_dir) / "chains.db"
            if ledger.exists():
                with sqlite3.connect(
                    ledger.resolve().as_uri() + "?mode=ro", uri=True
                ) as db:
                    has_history = has_history or bool(
                        db.execute(
                            "SELECT 1 FROM runs WHERE chain_id=? LIMIT 1",
                            (metadata.get("key"),),
                        ).fetchone()
                    )
            if has_history:
                raise ValueError(
                    "新链路已有运行或产物，保留配置和历史；请用 pause 暂停未来生产，再回退入口代码："
                    + str(metadata.get("key"))
                )
        for name, item in data["files"].items():
            if not any(Path(name).resolve().is_relative_to(p.resolve()) for p in roots):
                raise ValueError("Change path is outside the configured roots")
            if _encode(_bytes(Path(name))) != item["after"]:
                raise ValueError("配置已有后续修改，保留当前内容：" + name)
        with schedules.connect(True) as db:
            for key in changed:
                row = db.execute(
                    "SELECT * FROM schedules WHERE id=?", (key,)
                ).fetchone()
                current = dict(row) if row else {}
                if any(
                    current.get(field) != after.get(key, {}).get(field)
                    for field in fields
                ):
                    raise ValueError("周期已有后续修改，保留当前周期：" + key)
                if db.execute(
                    "SELECT 1 FROM schedule_runs WHERE schedule_id=? AND status IN ('starting','running')",
                    (key,),
                ).fetchone():
                    raise ValueError("周期仍有任务运行，完成后再恢复：" + key)
            for key in changed:
                old = before.get(key)
                if not old or old["deleted"]:
                    schedules.delete(key)
                    continue
                now = time.time()
                db.execute(
                    """UPDATE schedules SET schedule=?, timezone=?, enabled=?, missed_policy=?,
                              max_retries=?, deleted=0, version=version+1, next_run_at=?, updated_at=? WHERE id=?""",
                    (
                        old["schedule"],
                        old["timezone"],
                        old["enabled"],
                        old["missed_policy"],
                        old["max_retries"],
                        slot(old["schedule"], now, old["timezone"]),
                        now,
                        key,
                    ),
                )
                schedules._cancel_pending(
                    db,
                    key,
                    now,
                    "链路配置已恢复，保留冻结期次",
                    keep_issues=bool(old["enabled"]),
                )
        for name, item in reversed(list(data["files"].items())):
            if item["before"] is None:
                if Path(name).exists():
                    remove(name)
            else:
                Path(name).parent.mkdir(parents=True, exist_ok=True)
                atomic_write(name, base64.b64decode(item["before"]).decode())
