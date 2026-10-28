"""Generate new topic questions from a retained library, without additional web searches."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import matrices, theme_context
from .errors import CancelledError
from .fileio import atomic_write
from .llm import LLMClient, LLMConfig
from .lock import LockAcquireError, LockManager
from .state import StateManager
from .theme_library import ThemeLibrary
from .theme_store import ThemeStore

logger = logging.getLogger(__name__)

SYSTEM = """你是矩阵视频选题编辑。根据矩阵受众、栏目目标、参考题目、可复用题型和用户选填方向，推荐新的具体问题。
先从真实参考题目学习提问方式和叙事结构，再换成适合本矩阵受众的任务、场景与决策。
各栏目最多一条，不能全变成新闻、套餐比较或同一个问题的不同标题。参考题型可复用，已经推荐或使用的问题不可重推。
history 中相同核心问题必须使用原来的 topic_key，并且不要再推荐。只换措辞、月份或序号不算新问题。
题目是待研究的问题，不提前编造答案、流量、实测成绩、个人经历或新功能。旧样本只借鉴题型，不代表当前功能或权益。
动态栏目没有当前更新材料时可以留空，不根据旧参考例题猜测今天发布了新功能。
额外方向是创作偏好，不要求用户已经想到题目。没有某栏目参考题时可以根据矩阵目标构思，example_ids 留空。
feedback 是用户在本矩阵的选择记录。优先借鉴被选用或感兴趣的题型；按忽略原因调整具体性和画面可展示性。
not_now 只代表暂时不用，不作为负面偏好。不能因少量反馈放弃其他栏目，仍保持栏目多样性。
reason 说明观众的具体问题和本题内容价值，不把少量选择记录描述成高热度、高需求或显著趋势。
有参考题时必须引用本栏目提供的 example_ids。已有题型引用 pattern_id；提炼新题型时提供 format。
format 是你从参考例子归纳的提问结构，包含 name、title_pattern（带槽位的题目格式）、outline（3至6项讲述步骤）。
输出 JSON {themes:[{category,title,question,topic_key,reason,angle,audience,example_ids:[],pattern_id,format:{name,title_pattern,outline:[]}}]}。
question 是不含修辞的核心问题，topic_key 是简短稳定的主题标识；不同具体问题可共用同一个题型。
"""


def select(response: str, categories: list[dict], library: ThemeLibrary, matrix: str,
           references: dict, *, brief: str = "", pattern_id: str = "") -> list[dict]:
    data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip()))
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        raise ValueError("未取得可用的候选题目")
    category_map = {c["key"]: c for c in categories}
    examples = {r["id"]: r for r in references["examples"]}
    patterns = {r["id"]: r for r in references["patterns"]}
    selected, occupied = [], set()
    for item in data["themes"][:100]:
        if not isinstance(item, dict) or item.get("category") not in category_map:
            continue
        category = item["category"]
        if category in occupied:
            continue
        required = ("title", "question", "topic_key", "reason")
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in required):
            continue
        if not 4 <= len(item["title"].strip()) <= 180:
            continue
        refs = item.get("example_ids") if isinstance(item.get("example_ids"), list) else []
        refs = [examples[key] for key in dict.fromkeys(r for r in refs if isinstance(r, str))
                if key in examples and examples[key]["category"] == category]
        if any(r["category"] == category for r in examples.values()) and not refs:
            continue
        requested_pattern = pattern_id or item.get("pattern_id", "")
        chosen = patterns.get(requested_pattern) if isinstance(requested_pattern, str) else None
        if chosen and chosen["category"] != category:
            continue
        if chosen is None:
            fmt = item.get("format") or {}
            if not isinstance(fmt, dict) or not all(isinstance(fmt.get(k), str) and fmt[k].strip() for k in ("name", "title_pattern")):
                continue
            outline = fmt.get("outline")
            if not isinstance(outline, list) or not 3 <= len(outline) <= 6 or any(not isinstance(s, str) or not s.strip() for s in outline):
                continue
            key = library.add_pattern(matrix, category, name=fmt["name"], title_pattern=fmt["title_pattern"],
                                      outline=[s[:300] for s in outline], example_ids=[r["id"] for r in refs])
            chosen = {"id": key, "name": fmt["name"][:100], "title_pattern": fmt["title_pattern"][:300], "outline": outline}
        now = time.time()
        signals = [{"id": r["id"], "category": category, "platform": "参考库", "title": r["title"], "url": r["url"],
                    "excerpt": r["summary"], "collected_at": r["seen_at"], "metrics": {}, "signal_kind": "reference_example"} for r in refs]
        selected.append({"category": category, "title": item["title"].strip(), "angle": str(item.get("angle") or "")[:1000],
            "audience": str(item.get("audience") or category_map[category]["audience"])[:1000], "notes": brief,
            "reason": item["reason"].strip()[:1500], "signals": signals,
            "inspiration": {"question": item["question"].strip()[:300], "topic_key": item["topic_key"].strip()[:150],
                "pattern_id": chosen["id"], "pattern_name": chosen["name"], "title_pattern": chosen["title_pattern"],
                "outline": chosen["outline"], "example_ids": [r["id"] for r in refs], "generated_at": now,
                "basis": "reference_library" if refs else "matrix"}})
        occupied.add(category)
    return selected


def run_daily(engine, job: dict, config: dict) -> str | None:
    key = str(job["topic_discovery"])
    day = datetime.now(ZoneInfo(config["timezone"])).date().isoformat()
    return run(engine, {**job, "topic_ideas": key, "idea_day": day, "daily_direction": config.get("direction", "")})


def reference_patterns(patterns: list[dict], categories: list[dict], feedback: dict, preferred: str = "") -> list[dict]:
    """Keep the prompt bounded while allowing recently preferred formats to enter it."""
    weights = {}
    for group in feedback["groups"]:
        key = group["pattern_id"]
        if key:
            negative = sum(n for reason, n in group["reasons"].items() if reason != "not_now")
            weights[key] = 2 * group["selected"] + group["interested"] - negative
    chosen = [p for category in categories for p in sorted(
        (p for p in patterns if p["category"] == category["key"]),
        key=lambda p: -weights.get(p["id"], 0))[:3]]
    return list({p["id"]: p for p in chosen + [p for p in patterns if p["id"] == preferred]}.values())


def run(engine, job: dict) -> str | None:
    key = str(job["topic_ideas"])
    canonical = job.get("_file") or f"topic_ideas/{key}"
    library = ThemeLibrary(str(Path(engine.workspace_dir) / "state"))
    request_id = str(job.get("idea_request") or "")
    started, llm, claimed = time.time(), None, False
    usage = {"searches": 0}
    try:
        with LockManager(f"theme-ideas/{key}"), LockManager(canonical):
            if job.get("idea_day"):
                request_id = library.daily_request(key, job["idea_day"], job.get("daily_direction", ""))
            request = library.run(request_id)
            if request is None or request["matrix"] != key:
                raise ValueError("请从主题工作台发起题目生成")
            if request["status"] == "running" and job.get("idea_day"):
                # Both locks are held, so this record belongs to an interrupted owner.
                library.finish(request_id, "interrupted", request["result"], "前次每日出题已中断，保留该尝试后重试")
                request_id = library.daily_request(key, job["idea_day"], request["brief"])
                request = library.run(request_id)
            if request["status"] == "success":
                StateManager.update(canonical, "success", output_path=request["result"].get("output"),
                                    usage={"requests": 0, "searches": 0, "total_tokens": 0, "reused": True},
                                    user=engine.user, duration_seconds=round(time.time() - started, 1))
                return request["result"].get("output")
            if request["status"] not in {"queued", "running"}:
                raise ValueError("这次生成已结束，请从主题工作台重新生成")
            claimed = True
            library.finish(request_id, "running", {})
            matrix = matrices.load_all(engine.config_dir).get(key)
            if matrix is None or matrix.archived:
                raise ValueError("矩阵不存在或已归档")
            categories = theme_context.categories(engine.config_dir, key)
            if request["category"]:
                categories = [c for c in categories if c["key"] == request["category"]]
            if not categories:
                raise ValueError("请选择本矩阵的栏目")
            library.seed(engine.config_dir, key)
            refs = library.snapshot(key)
            category_keys = {c["key"] for c in categories}
            # Bound prompt size per category while retaining every stored reference.
            refs["examples"] = [r for c in categories for r in [e for e in refs["examples"] if e["category"] == c["key"]][:3]]
            all_patterns = [p for p in refs["patterns"] if p["category"] in category_keys]
            storage = ThemeStore(str(Path(engine.workspace_dir) / "state"))
            feedback = storage.feedback_profile(key)
            refs["patterns"] = reference_patterns(all_patterns, categories, feedback, request["pattern_id"])
            history = storage.exclusions(key)
            payload = {"matrix": {"name": matrix.name, "domain": matrix.domain, "keywords": matrix.keywords, "content": matrix.content},
                "categories": categories, "extra_direction": request["brief"], "preferred_pattern": request["pattern_id"],
                "feedback": feedback,
                "patterns": refs["patterns"], "examples": [{**{k: r[k] for k in ("id", "category", "title")}, "summary": r["summary"][:350]} for r in refs["examples"]],
                "history": [{"title": r["title"], "question": r["inspiration"].get("question", ""),
                    "topic_key": r["inspiration"].get("topic_key", ""), "used": bool(r["used_at"])} for r in history[:100]]}
            if engine._cancel.is_set():
                raise CancelledError("题目生成已取消")
            engine._progress({"type": "phase", "phase": "generating_themes", "message": "参考选题库，按栏目生成候选问题"})
            config = LLMConfig.from_system(engine.sys, "ai")
            llm = LLMClient(replace(config, max_tokens=min(config.max_tokens, 8192)), cancel_token=engine._cancel, progress_cb=engine._progress)
            response = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], tools=None)
            usage.update(llm.usage)
            day = job.get("idea_day") or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
            folder = Path(engine.workspace_dir) / "output/topic_ideas" / key
            path = folder / f"{day}_{request_id[:8]}.md"
            atomic_write(str(path.with_suffix(".response.json")), json.dumps({"input": payload, "response": response.content, "usage": usage}, ensure_ascii=False, indent=2))
            candidates = select(response.content, categories, library, key, refs, brief=request["brief"], pattern_id=request["pattern_id"])
            for candidate in candidates:
                candidate["inspiration"]["generation_id"] = request_id
            if engine._cancel.is_set():
                raise CancelledError("题目生成已取消")
            made = storage.publish_generated(key, day, candidates)
            text = f"# {matrix.name} · 参考题型推荐\n\n补充方向：{request['brief'] or '按矩阵定位与栏目目标'}\n\n"
            for row in made:
                text += f"## {row['title']}\n\n{row['reason']}\n\n题型：{row['inspiration']['pattern_name']}\n\n"
            if not made:
                text += "本轮没有新的合适问题；已推荐和已使用主题保留，未重复加入。\n"
            atomic_write(str(path), text)
            output = str(path.relative_to(engine.workspace_dir))
            result = {"recommended": len(made), "theme_ids": [r["id"] for r in made], "usage": usage, "output": output,
                      "categories": len(categories), "examples": len(refs["examples"]),
                      "duration_seconds": round(time.time() - started, 1)}
            library.finish(request_id, "success", result)
            StateManager.update(canonical, "success", output_path=output, usage=usage, user=engine.user, duration_seconds=round(time.time() - started, 1))
            return output
    except LockAcquireError:
        logger.info("Theme ideas %s already running; keep its request intact", key)
        return None
    except Exception as exc:
        status = "cancelled" if isinstance(exc, CancelledError) else "failed"
        if claimed and request_id and (request := library.run(request_id)) and request["matrix"] == key and request["status"] in {"queued", "running"}:
            library.finish(request_id, status, {"usage": usage}, str(exc)[:500])
        StateManager.update(canonical, status, error=str(exc)[:500], usage=usage, user=engine.user)
        logger.warning("Theme ideas %s: %s", key, exc)
        return None
    finally:
        if llm:
            llm.close()
