"""Matrix topic workbench: discovery schedules, manual briefs and the existing video workflow."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from core import matrices, theme_context, theme_discovery
from core.fileio import atomic_write, file_lock
from core.schedule_store import ScheduleStore, iso
from core.theme_store import ThemeStore
from core.theme_library import ThemeLibrary
from core.video import production, store as video_store
from web.settings import get_settings


def paths():
    return get_settings().paths


def storage() -> ThemeStore:
    return ThemeStore(paths().state_dir)


def reference_library(key: str) -> ThemeLibrary:
    value = ThemeLibrary(paths().state_dir)
    value.seed(paths().config_dir, key)
    return value


def matrix(key: str):
    value = matrices.load_all(paths().config_dir).get(key)
    if value is None or value.archived:
        raise ValueError("矩阵不存在或已归档")
    return value


def require_theme(key: str, theme_id: str) -> dict:
    matrix(key)
    value = storage().get(theme_id)
    if value is None or value["matrix"] != key:
        raise LookupError("主题不存在")
    return value


def category(key: str, category_key: str) -> dict:
    return next((row for row in theme_context.categories(paths().config_dir, key, include_legacy=True)
                 if row["key"] == category_key), None)


def _schedule(key: str) -> dict | None:
    plan = next((row for row in ScheduleStore(paths().state_dir).list()
                 if row["job_name"] == f"topic_discovery/{key}"), None)
    return {**plan, "next_run": iso(plan["next_run_at"], plan["timezone"])} if plan else None


def _with_production(items: list[dict]) -> list[dict]:
    for item in items:
        script = video_store.get_script(item["script_id"]) if item["script_id"] else None
        if script:
            renders = video_store.list_renders(script["id"])
            item.update(script_status=script["status"], stage=script.get("stage"), error=script.get("error"),
                        report_path=script.get("report_path"), version=script["current_version"],
                        renders=[{"id": r["id"], "status": r["status"]} for r in renders])
    return items


def history(key: str, *, offset: int = 0) -> dict:
    matrix(key)
    result = storage().history(key, offset=offset)
    result["themes"] = _with_production(result["themes"])
    return result


def snapshot(key: str) -> dict:
    matrix(key)
    store = storage()
    items = _with_production(store.list(key))
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from web.runner.registry import TaskRegistry
    from core.lock import LockManager
    categories = theme_context.categories(paths().config_dir, key, include_legacy=True)
    return {"categories": [c for c in categories if not c.get("legacy")],
            "legacy_categories": [c for c in categories if c.get("legacy")], "themes": items,
            "today": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            "discovery_running": TaskRegistry().is_running(f"topic_platform/{key}") or
                (theme_discovery.configuration(paths().config_dir, key)["mode"] == "discovery" and TaskRegistry().is_running(f"topic_discovery/{key}")),
            "runs": store.runs(key), "library": reference_library(key).snapshot(key), "used_count": store.history(key, limit=1)["total"],
            "ideas_running": TaskRegistry().is_running(f"topic_ideas/{key}") or LockManager.is_locked(f"theme-ideas/{key}") or
                (theme_discovery.configuration(paths().config_dir, key)["mode"] == "ideas" and TaskRegistry().is_running(f"topic_discovery/{key}")),
            "feedback": store.feedback_profile(key),
            "configuration": theme_discovery.configuration(paths().config_dir, key),
            "schedule": _schedule(key), "scheduler": ScheduleStore(paths().state_dir).health()}


def create(key: str, fields: dict) -> dict:
    matrix(key)
    found = category(key, fields["category"])
    if found is None:
        raise ValueError("请选择本矩阵的栏目")
    return storage().create(key, fields["category"], fields["title"], angle=fields.get("angle", ""),
                            audience=fields.get("audience") or found["audience"], notes=fields.get("notes", ""))


def ensure_job(key: str) -> str:
    value = matrix(key)
    name = f"topic_discovery/{key}"
    path = Path(paths().jobs_dir) / f"{name}.yaml"
    if not path.exists():
        atomic_write(str(path), yaml.safe_dump({"name": value.name + " · 每日主题发现", "enabled": True,
            "description": "按栏目寻找公开平台的观众问题与主题线索，每类每天最多一条。",
            "topic_discovery": key, "prompt": "动态读取矩阵栏目，执行分类主题发现。",
            "runtime": {"timeout_seconds": 2400, "skip_if_running": True},
            "output": f"output/topic_discovery/{key}/{{date}}.md"}, allow_unicode=True, sort_keys=False))
    return name


def set_schedule(key: str, *, enabled: bool, at: str, platforms: list[str], mode: str | None = None,
                 direction: str | None = None) -> dict:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", at):
        raise ValueError("时间格式为 HH:MM")
    cfg = theme_discovery.configuration(paths().config_dir, key)
    mode = mode or cfg["mode"]
    if mode not in {"ideas", "discovery"}:
        raise ValueError("请选择每日推荐方式")
    if (mode == "discovery" and not platforms) or any(p not in theme_discovery.PLATFORMS for p in platforms):
        raise ValueError("请选择支持的公开平台")
    name = ensure_job(key)
    current = _schedule(key)
    cfg.update(time=at, timezone="Asia/Shanghai", platforms=list(dict.fromkeys(platforms)), mode=mode,
               direction=cfg["direction"] if direction is None else direction.strip()[:4000])
    if enabled:
        from web.services import managed_scheduler
        expression = f"{int(at[3:])} {int(at[:2])} * * *"
        plan = managed_scheduler.save(name, expression, timezone="Asia/Shanghai", max_retries=1,
                                      schedule_id=current["id"] if current else "themes_" + key,
                                      editing=bool(current))
        if not plan["enabled"]:
            ScheduleStore(paths().state_dir).toggle(plan["id"], True)
    elif current:
        ScheduleStore(paths().state_dir).toggle(current["id"], False)
    atomic_write(str(Path(paths().config_dir) / "topic_discovery" / f"{key}.yaml"),
                 yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return {"configuration": cfg, "schedule": _schedule(key)}


def discover(key: str, by: str) -> dict:
    from web.runner.executor import start_job
    from web.runner.registry import TaskRegistry
    from web.services.assistant_maintenance import run_admission
    job = ensure_job(key)
    if theme_discovery.configuration(paths().config_dir, key)["mode"] == "ideas":
        from core.config import load_job
        original = load_job(paths().jobs_dir, job)
        job = f"topic_platform/{key}"
        atomic_write(str(Path(paths().jobs_dir) / f"{job}.yaml"), yaml.safe_dump(
            {k: v for k, v in {**original, "platform_discovery_only": True}.items() if not k.startswith("_")},
            allow_unicode=True, sort_keys=False))
    with run_admission(), file_lock(str(Path(paths().state_dir) / "theme-discovery-start" / key)):
        if TaskRegistry().is_running(job):
            return {"status": "running", "job": job}
        task = start_job(job, by, config_dir=paths().config_dir, jobs_dir=paths().jobs_dir)
        return {"status": "running", "job": job, "run_id": task.task_id}


def generate(key: str, by: str, *, brief: str = "", category_key: str = "", pattern_id: str = "") -> dict:
    from web.runner.executor import start_job
    from web.runner.registry import TaskRegistry
    from web.services.assistant_maintenance import run_admission
    from core.lock import LockManager
    value = matrix(key)
    if category_key and category(key, category_key) is None:
        raise ValueError("请选择本矩阵的栏目")
    library = reference_library(key)
    if pattern_id:
        pattern = next((p for p in library.snapshot(key)["patterns"] if p["id"] == pattern_id), None)
        if not pattern or (category_key and category_key != pattern["category"]):
            raise ValueError("请选择本矩阵和栏目的题型")
        category_key = pattern["category"]
    name = f"topic_ideas/{key}"
    with run_admission(), file_lock(str(Path(paths().state_dir) / "theme-ideas-start" / key)):
        if TaskRegistry().is_running(name) or LockManager.is_locked(name) or LockManager.is_locked(f"theme-ideas/{key}"):
            return {"status": "running", "job": name}
        brief = brief.strip() or theme_discovery.configuration(paths().config_dir, key)["direction"]
        request_id = library.queue(key, brief, category_key, pattern_id)
        path = Path(paths().jobs_dir) / f"{name}.yaml"
        atomic_write(str(path), yaml.safe_dump({"name": value.name + " · 参考题型出题", "enabled": True,
            "description": "参考保留的题目和题型，按栏目生成新问题，不额外联网搜索。", "topic_ideas": key,
            "idea_request": request_id, "prompt": "根据矩阵和参考库生成候选题目。",
            "runtime": {"timeout_seconds": 1200, "skip_if_running": True},
            "output": f"output/topic_ideas/{key}/{{date}}.md"}, allow_unicode=True, sort_keys=False))
        try:
            task = start_job(name, by, config_dir=paths().config_dir, jobs_dir=paths().jobs_dir)
        except Exception as exc:
            library.finish(request_id, "failed", {}, str(exc)[:500])
            raise
        return {"status": "running", "job": name, "run_id": task.task_id, "request_id": request_id}


def production_input(key: str, value: dict, minutes: float = 4, *, automatic: bool = False) -> tuple[str, dict]:
    from core.video import recipes
    found = category(key, value["category"])
    if found is None:
        raise ValueError("主题所属栏目已移除，请调整栏目后再研究")
    configs = production.load_all(paths().config_dir, paths().jobs_dir)
    binding = next((configs[pid] for pid in found["production_ids"] if pid in configs and not configs[pid].errors and (not automatic or configs[pid].auto)), None)
    recipe_key = "theme_research"
    recipe = recipes.find(paths().config_dir, recipe_key)
    if recipe and recipe.minutes_range and recipe.minutes_range[0] <= minutes <= recipe.minutes_range[1]:
        recipe_key = recipes.variant_column_key(recipe_key, minutes=minutes)
    sources = theme_context.source_specs(found, paths().config_dir, paths().jobs_dir, matrix(key))
    topic = {"title": value["title"], "fields": {"核心角度": value["angle"], "目标受众": value["audience"],
              "一句话": found["promise"], "补充材料": value["notes"]}, "theme_task_id": value["id"],
              "theme_research": theme_context.research_spec(value, found, matrix(key), sources=sources)}
    if binding:
        topic["_production"] = binding.key
    if not binding or found.get("catalog"):
        from core.video import content_columns
        context = content_columns.snapshot(paths().config_dir, binding.key, binding.job) if binding else {
            "source_matrix": key, "render_inheritance": "content_matrix", "render": {}, "presentation": {}}
        context.update({"matrix": key, "column": found["key"], "column_name": found["name"],
                   "audience": value["audience"], "promise": found["promise"], "notes": found["notes"],
                   "type": found["type"], "type_definition": content_columns.types(paths().config_dir).get(found["type"], {})})
        topic["_content_context"] = context
    return recipe_key, topic


def start(key: str, theme_id: str, by: str, *, auto_video: bool, minutes: float = 4) -> dict:
    from web.services import video_executor
    from web.services.assistant_maintenance import run_admission
    with run_admission(), file_lock(str(Path(paths().state_dir) / "theme-start" / theme_id)):
        value = require_theme(key, theme_id)
        if value["script_id"]:
            return value
        if value["used_at"]:
            raise ValueError("主题已记录为使用过，可以参考同一题型生成新问题")
        # Recover the small cross-database handover if the app stopped after creating the script.
        with video_store.connect() as db:
            old = db.execute("SELECT id FROM scripts WHERE json_extract(topic,'$.theme_task_id')=?",
                             (theme_id,)).fetchone()
        if old:
            return storage().attach(theme_id, old["id"], auto_video)
        recipe_key, topic = production_input(key, value, minutes)
        script = video_executor.create(recipe_key, topic, started_by=by)
        return storage().attach(theme_id, script["id"], auto_video)


def enqueue_recommendations() -> int:
    """All eligible daily recommendations enter the same durable batch queue."""
    from core.video import auto, autopilot_store as ap, content_guard, story_pool
    values = auto.recommendations_pending(paths().config_dir, paths().jobs_dir, paths().state_dir)
    made = 0
    for row in values:
        with file_lock(str(Path(paths().state_dir) / "theme-start" / row["id"])):
            value = storage().get(row["id"])
            if not value or value["script_id"] or value["used_at"]:
                continue
            with video_store.connect() as db:
                queued = db.execute("SELECT 1 FROM autopilot_items WHERE json_extract(topic,'$.theme_task_id')=?",
                                    (value["id"],)).fetchone()
            if queued:
                continue
            recipe_key, topic = production_input(value["matrix"], value, automatic=True)
            owner = matrix(value["matrix"])
            configs = production.load_all(paths().config_dir, paths().jobs_dir)
            binding = configs.get(topic.get("_production", ""))
            job = binding.job if binding else next(iter(owner.jobs), "")
            topic = content_guard.policy_topic({**topic, "id": "theme/" + value["id"]}, job)
            topic["matrix_dedup"]["policy"] = story_pool.POLICY
            ap.create_run({"picks": [], "refresh": False, "theme_id": value["id"], "matrix": value["matrix"]},
                          [{"column_key": recipe_key, "topic": topic, "production": binding.key if binding else ""}],
                          started_by="autopilot")
            storage().mark(value["id"], "queued")
            made += 1
    return made


def tick() -> None:
    """Advance opt-in themes after writing; existing render records provide idempotency and recovery."""
    from web.services import video_executor
    with video_store.connect() as db:
        managed = {r[0] for r in db.execute("SELECT script_id FROM autopilot_items WHERE script_id IS NOT NULL")}
    for theme in storage().active():
        if theme["script_id"] in managed:
            script = video_store.get_script(theme["script_id"])
            renders = video_store.list_renders(theme["script_id"])
            if any(r["status"] == "COMPLETED" for r in renders):
                storage().mark(theme["id"], "completed")
            elif script and script.get("archived"):
                storage().mark(theme["id"], "skipped")
            continue
        try:
            with file_lock(str(Path(paths().state_dir) / "theme-render" / theme["id"]), timeout=0.05):
                script = video_store.get_script(theme["script_id"])
                if script and script["status"] == "FAILED":
                    storage().mark(theme["id"], "attention")
                    continue
                if not script or script["status"] not in {"DRAFT", "APPROVED"} or script.get("stage"):
                    continue
                renders = video_store.list_renders(script["id"])
                if renders:
                    if any(row["status"] == "COMPLETED" for row in renders):
                        storage().mark(theme["id"], "completed")
                    elif all(row["status"] in {"FAILED", "CANCELLED"} for row in renders):
                        storage().mark(theme["id"], "attention")
                    continue
                if script["status"] == "DRAFT":
                    video_executor.approve(script["id"], script["current_version"], "theme-workbench")
                video_executor.render(script["id"], "theme-workbench")
        except TimeoutError:
            continue
        except Exception as exc:
            # Saved on the script so the user can retry from its existing production view.
            script = video_store.get_script(theme["script_id"])
            if script:
                video_store.update_script(script["id"], error=f"自动制作未完成：{str(exc)[:300]}")
                storage().mark(theme["id"], "attention")
