"""Freeze selected automatic inputs and prepare two isolated author workspaces."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BASE = ROOT / "state/acceptance-content-value-20261115/natural-writing-20261116"
WORK = Path("/tmp/natural-writing-20261116-r1")
sys.path.insert(0, str(ROOT))


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def replace_node(source: str, name: str, replacement: str) -> str:
    node = next(n for n in ast.parse(source).body
                if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
                or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)))
    lines = source.splitlines(keepends=True)
    return "".join(lines[:node.lineno - 1]) + replacement.rstrip() + "\n" + "".join(lines[node.end_lineno:])


def candidate(work: Path) -> None:
    path = work / "core/video/persona.py"
    text = path.read_text()
    text = replace_node(text, "prompt", '''def prompt(persona: dict, *, opening_only: bool = False) -> str:
    if not persona:
        return ""
    identity = str(persona.get("introduce") or f"我是{persona['name']}。").rstrip("。.!！ ")
    greeting = persona.get("opening_greeting", "大家好")
    ending = "" if opening_only else f"最后道别说「{persona['farewell']}」。"
    return (f"\\n你是账号的讲述者「{persona['name']}」，用自己的口吻向目标观众讲述。"
            f"开场保留简短的问候和身份「{greeting}，{identity}」，衔接方式由正在讲的内容决定。"
            "节目已有固定问候时只介绍身份。"
            f"{ending}"
            "公开材料中的测试和经历按原有归属讲述，不说成自己的亲身经历。")''')
    text = replace_node(text, "opening_needs_introduction", '''def opening_needs_introduction(paragraphs: list, persona: dict, *, fixed_greeting: str = "") -> bool:
    """Check account identity without imposing a hook or a topic sentence."""
    if not persona or not paragraphs:
        return False
    identity = str(persona.get("introduce") or f"我是{persona['name']}。").rstrip("。.!！ ")
    opening = "\\n".join(p["text"] if isinstance(p, dict) else p.text for p in paragraphs)
    if opening.count(identity) != 1:
        return True
    greeting = persona.get("opening_greeting") if not fixed_greeting else ""
    return bool(greeting and opening.count(f"{greeting}，{identity}") != 1)''')
    path.write_text(text)
    path = work / "core/video/writer.py"
    text = path.read_text()
    for name, value in {
        "OPENING_RULE": "从本期最值得听的内容进入，切入点和讲法由材料决定。",
        "STYLE_RULE": "用自然清楚的口语讲述。具体细节和必要解释充分展开，术语在需要时讲明白，数字的取舍与说法服务于理解。",
        "CONCRETE_RULE": "用材料中的具体细节、例子、过程和原话支撑解释与判断，让观众听明白事情怎样发生。已有的事实、数据和经历按材料讲述，制作备注留在稿外。",
    }.items():
        text = replace_node(text, name, f"{name} = {value!r}")
    text = replace_node(text, "SCOPE_CONCRETE_RULE", 'SCOPE_CONCRETE_RULE = CONCRETE_RULE + "面向选题指定的受众讲述，具体例子帮助他们理解，不把例子的职业或任务变成整篇适用范围。"')
    text = text.replace("讲述有起承转合：开头引出本期的问题或看点，中段一步步展开，在关键处转折或点出要害，结尾回到开头的问题落下结论。", "")
    text = text.replace("有两面时，讲清哪一面在眼下更重要、凭什么，或哪个条件会改变选择。把真正影响判断的条件放在相关处，结尾把自己的观点讲完整。", "让解释和判断有清楚的依据，必要的条件在相关处讲明白。")
    begin = text.index("        throughline = (")
    end = text.index("        material = (", begin)
    text = text[:begin] + '        throughline = ""\n' + text[end:]
    text = text.replace("修顺这篇口播的开场，保留完整钩子、事实、人物引语和引用链接。将原来孤立的自我介绍改写为问候、名字和具体话题的连贯表达，不是另外新增一段介绍。", "为这篇口播的开场补齐讲述者身份，保留已有内容、事实、人物引语和引用链接，按上下文自然衔接。")
    text = text.replace("给中文视频写两三句开场，自然接到已写好的正文。先从观众的具体处境发问，或者把正文首段的", "为这期中文视频写简短开场，自然接到已写好的正文。按内容需要，可以把正文首段的")
    path.write_text(text)
    path = work / "core/video/manuscript_workflow.py"
    text = path.read_text()
    insert = '''
# Isolated experiment: remove compulsory narrative moves, keep job purposes.
JOB_PROMPTS["monitoring/ai_flash_discovery"] = replace(
    JOB_PROMPTS["monitoring/ai_flash_discovery"], version="flash-natural-r1",
    draft="围绕已选消息，讲清最值得理解的变化及其意义。用具体材料展开解释，给出有依据的认识或判断；讲述顺序和篇幅由本题内容决定。",
    read="第一次听这条消息，复述最值得记住的变化或认识，引用值得保留的原话。若有一处让你走神、听不懂或觉得在反复说同一件事，指出原句并说清影响；已经讲顺可以留空。只标解决同一问题需要的段落。输出 JSON：{\\"takeaway\\":\\"听完的认识\\",\\"keep_quote\\":\\"值得保留的原话\\",\\"issue_quote\\":\\"问题原句，可空\\",\\"revision\\":\\"还需要听清什么，可空\\",\\"targets\\":[\\"段落编号\\"]}",
    revise="只修改指出的段落，结合原材料讲清读者没听懂的内容，或去掉没有推进讲述的重复。保留有价值的细节与作者判断，顺接上下文。原段已经合适可以保留。输出 JSON：{\\"paragraphs\\":[{\\"id\\":\\"原段落编号\\",\\"text\\":\\"修订口播\\"}]}"
)
JOB_PROMPTS["ai_media/ai_tool_tutorials"] = replace(
    JOB_PROMPTS["ai_media/ai_tool_tutorials"], version="ai-tool-natural-r1",
    draft="让本期目标观众能够完成选定的操作，讲清实际入口、必要输入、关键动作和可见结果。陌生功能与状态结合当前操作解释，适用条件放在需要它的地方。示例明确作为示意，涉及修改时说明原内容与改后结果。按实际操作的依赖组织讲述，开场、段落和收尾由内容决定。"
)

'''
    text = text.replace("\ndef enabled_jobs(", "\n" + insert + "def enabled_jobs(", 1)
    path.write_text(text)
    for p in (work / "core/video").glob("*.py"):
        if p.name in {"persona.py", "writer.py", "manuscript_workflow.py"}:
            compile(p.read_text(), str(p), "exec")


def main() -> None:
    if WORK.exists():
        raise FileExistsError(f"Preserve previous attempts: {WORK}")
    for variant in ("control", "candidate"):
        work = WORK / variant
        for folder in ("core", "utils", "web", "config", "jobs"):
            shutil.copytree(ROOT / folder, work / folder,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "*.lock"))
        for folder in ("state", "logs"):
            (work / folder).mkdir(exist_ok=True)
    candidate(WORK / "candidate")
    from core.video import story_pool
    cases = {"flash-svg": "d74b8ce57a7d447788ddcf54e6fa9ed6",
             "tutorial-share": "72a8e8b64c964cf6b6b27d4b4521d9d2"}
    with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
        for name, sid in cases.items():
            script = json.loads((BASE / "baseline/scripts" / f"{sid}.json").read_text())
            topic = script["topic"]
            pool_id = topic["story"]["pool_id"]
            row = db.execute("SELECT payload FROM video_story_pools WHERE id=?", (pool_id,)).fetchone()
            if row is None:
                raise RuntimeError("Selected automatic source pool is missing")
            pool = json.loads(row[0])
            save(BASE / "round1/inputs" / f"{name}-pool.json", pool)
            original = None
            direction = ""
            if joint := topic.get("joint_plan"):
                plan = json.loads((BASE / "baseline/material_files/state/joint-plans" / f"{joint['id']}.json").read_text())
                selected = plan["assignments"][joint["entry"]]
                report, original, direction = selected["report"], selected["material"], selected["direction"]
            else:
                report = story_pool.material_for(pool, topic["story"])
            save(BASE / "round1/inputs" / f"{name}.json", {
                "name": name, "script": script, "report": report, "original_material": original,
                "allocation_direction": direction, "pool_id": pool_id,
                "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
                "material_sha256": hashlib.sha256((original or "").encode()).hexdigest(),
                "evidence_level": "saved automatic selection replay; not full business-entry validation",
            })
    manifest = []
    for variant in ("control", "candidate"):
        for name in ("persona.py", "writer.py", "manuscript_workflow.py"):
            path = WORK / variant / "core/video" / name
            raw = path.read_bytes()
            saved = BASE / "round1/source" / variant / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(raw)
            manifest.append({"variant": variant, "file": name, "sha256": hashlib.sha256(raw).hexdigest()})
    save(HERE / "round1-source.json", manifest)
    print(json.dumps({"work": str(WORK), "cases": list(cases), "prepared": True}))


if __name__ == "__main__":
    main()
