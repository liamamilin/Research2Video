"""Find stiff narration in a fresh draft and rewrite only the paragraphs that show it.

The program decides which paragraphs have a problem; the model only rewords them. A paragraph comes back with the
same numbers, citations and position, or the original is kept. Nothing here blocks approval or rendering.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel, Field

PROMPT_VERSION = 3
#: "不是 A，而是 B" is a fine turn of phrase once or twice an episode; past this it becomes a tic.
CONTRAST_ALLOWED = 2

#: A production note to the video maker said as narration ("视频里应该说清楚", "素材只有一句描述").
_PRODUCTION_NOTE = re.compile(
    # "视频里应该" is a note; "生成的视频应该更稳" is content about a video tool, so the 里/中 is required.
    r"(?:视频|画面|片子|口播)(?:里|中)(?:应该|应当|要|需要)(?:说清楚|讲清楚|讲清|说清|把|当|讲成|强调|交代)"
    r"|(?:这条|本条|这一条)(?:应该|应当)(?:说清楚|讲清楚|讲清|说清|把|当|讲成|强调|交代)"
    # Narration about footage ("素材没有版权") is content; these are remarks on the research brief.
    r"|素材(?:给的信息|给出的信息|只有一句|里只有|中只有)"
    r"|(?:素材|材料)(?:里|中)(?:也)?(?:明确)?(?:说|提到|写|实测)"
    r"|(?:研究)?底稿(?:里|中)?")
#: An item rounded off with the same stock line ("这条的要点是", "这一条记住的是").
# "最值得记住的是" closed six items of one programme (2026-11-02 快讯).
_STOCK_ENDING = re.compile(r"这(?:一)?条(?:的)?(?:要点|重点|核心|价值)(?:是|在于)|这(?:一)?条(?:要)?记住"
                           r"|最值得(?:记住|注意|看|弄懂)的(?:是|一点|新做法|，)|这(?:一)?条真正(?:改变|值得看)的是")
#: A made-up bridge into an item ("如果说上一条讲的是……那么这一条……", "接下来这条处理的是").
_STOCK_BRIDGE = re.compile(r"如果说[^。！？]{2,40}?[，,]\s*那么|接下来(?:这|的这)(?:一)?条|下面这(?:一)?条")
#: A hedge said like a disclaimer ("以你实际界面为准", "不是效果承诺", "我们不做猜测").
_HEDGE = re.compile(r"以(?:你的?)?实际(?:界面|页面|情况|账单)?(?:显示)?为准|不是效果承诺|(?:我们)?不(?:做|作)(?:任何)?猜测")
_CONTRAST = re.compile(r"不是[^。！？；\n]{1,30}?[，,]\s*(?:而)?是")
#: The closing's goodbye; a rewrite of that paragraph must still say it.
_SIGN_OFF = re.compile(r"[^。！？]*(?:下期见|感谢收看|感谢观看|再见)[^。！？]*[。！？]?")

RULES = {
    "production_note": "制作说明混进了口播，观众听到的是写给剪辑的话",
    "stock_ending": "用固定句式收尾，各条听起来一个模子",
    "contrast_overuse": "「不是……而是……」在本期用得太多，变成口头禅",
    "stock_bridge": "硬造的过渡句，两条之间没有关系也要接上",
    "hedge": "像免责声明一样的套话，观众听着生硬",
}

SYSTEM_PROMPT = """根据素材，产出目标观众愿意看、听得懂、想继续看的视频。
当前步骤：润色口播稿中被指出问题的段落。
只改列出的段落，每段改成一段，意思、事实、数字、名称和条件保持不变，只换说法。
制作说明（写给剪辑或画面的话）改成直接对观众讲的内容，或删去那句话；固定收尾句改成一句贴合本段内容的短判断；
硬造的过渡句改成直接进入本条（比如“再说 Gemini。”），或用一句话说出两条真实的关系；免责套话删去，必要的前提用半句话自然带过；
「不是……而是……」改为直接说出肯定的那一半，被否定的一半不必要就删去，不换成「……反而没那么重要」「……倒在其次」之类的新套话。
保持段落与前后文的衔接，不加新内容，不打招呼；段落里原有的道别句原样保留。
输出 JSON：{"paragraphs":[{"id":"S1P2","text":"改好的口播"}]}"""


class _Rewrite(BaseModel):
    id: str
    text: str = Field(min_length=1)


class _Reply(BaseModel):
    paragraphs: list[_Rewrite] = Field(default_factory=list)


def pid(si: int, pi: int) -> str:
    return f"S{si + 1}P{pi + 1}"


def findings(sections: list[dict], *, greeting: str = "") -> list[dict]:
    """Paragraphs with a problem a rewrite can fix, in reading order.

    The fixed greeting is the program's own line and is never flagged.
    """
    found = []
    contrasts = 0
    for si, section in enumerate(sections):
        item = section.get("episode_role", "item") == "item"
        for pi, paragraph in enumerate(section.get("paragraphs", [])):
            text = paragraph.get("text") or ""
            if greeting and text.strip() == greeting:
                continue
            hits = [("production_note", m.group()) for m in _PRODUCTION_NOTE.finditer(text)]
            if item:
                hits += [("stock_ending", m.group()) for m in _STOCK_ENDING.finditer(text)]
                hits += [("stock_bridge", m.group()) for m in _STOCK_BRIDGE.finditer(text)]
            hits += [("hedge", m.group()) for m in _HEDGE.finditer(text)]
            for match in _CONTRAST.finditer(text):
                contrasts += 1
                if contrasts > CONTRAST_ALLOWED:
                    hits.append(("contrast_overuse", match.group()))
            found += [{"id": pid(si, pi), "section": si, "paragraph": pi, "rule": rule, "quote": quote,
                       "problem": RULES[rule]} for rule, quote in hits]
    return found


def _numbers(text: str) -> set[str]:
    from .writer import _numbers as numbers
    return numbers(text)


def _script_text(sections: list[dict]) -> str:
    lines = []
    for si, section in enumerate(sections):
        lines.append(f"## {section['title']}")
        lines += [f"[{pid(si, pi)}] {p['text']}" for pi, p in enumerate(section["paragraphs"])]
    return "\n".join(lines)


def polish(client, sections: list[dict], *, greeting: str = "") -> tuple[list[dict], dict]:
    """(sections, record). With nothing found no model call is made; any failure keeps the draft as it was."""
    found = findings(sections, greeting=greeting)
    record: dict = {"version": PROMPT_VERSION, "findings": found, "changes": [], "rejected": []}
    if not found:
        return sections, record
    targets: dict[str, list[dict]] = {}
    for finding in found:
        targets.setdefault(finding["id"], []).append(finding)
    issues = "\n".join(f"[{key}] " + "；".join(f"{f['problem']}：「{f['quote']}」" for f in items)
                       for key, items in targets.items())
    request = f"## 现在的口播稿\n{_script_text(sections)}\n\n## 要改的段落和问题\n{issues}"
    from .adapt.common import call_json
    try:
        reply = call_json(client, SYSTEM_PROMPT, request, _Reply, temperature=0.4)
    except Exception as exc:  # noqa: BLE001 - polishing is optional; a failed call never costs the draft
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        return sections, record
    out = json.loads(json.dumps(sections))
    where = {f["id"]: (f["section"], f["paragraph"]) for f in found}
    for rewrite in reply.paragraphs:
        if rewrite.id not in targets:
            continue
        si, pi = where[rewrite.id]
        before = out[si]["paragraphs"][pi]["text"]
        after = rewrite.text.strip()
        goodbye = "".join(m.group() for m in _SIGN_OFF.finditer(before)).strip()
        if goodbye and not _SIGN_OFF.search(after):
            after = after.rstrip() + goodbye
        if after == before:
            continue
        if _numbers(after) != _numbers(before):
            record["rejected"].append({"id": rewrite.id, "reason": "改写改动了数字", "text": after})
            continue
        out[si]["paragraphs"][pi]["text"] = after
        record["changes"].append({"id": rewrite.id, "where": f"{out[si]['title']} · 第 {pi + 1} 段",
                                  "rules": sorted({f["rule"] for f in targets[rewrite.id]}),
                                  "before": before, "after": after})
    record["remaining"] = findings(out, greeting=greeting)
    return out, record


def apply(written, llm_config, *, greeting: Optional[str] = None, voice: Optional[str] = None,
          cancel_token=None) -> None:
    """Polish a fresh ``writer.WrittenScript`` in place and record what happened in its meta."""
    from core import telemetry
    from core.llm import LLMClient
    from . import length
    client = LLMClient(llm_config, cancel_token=cancel_token)
    with telemetry.track("polish") as span:
        try:
            sections, record = polish(client, written.sections, greeting=greeting or "")
        except Exception as exc:  # noqa: BLE001 - a bug here must not cost a finished draft
            sections, record = written.sections, {"version": PROMPT_VERSION, "findings": [], "changes": [],
                                                  "rejected": [], "error": f"{type(exc).__name__}: {exc}"[:500]}
        if record.get("error"):
            span["status"] = "failed"
    record["usage"] = dict(client.usage)
    written.meta["polish"] = record
    if not record["changes"]:
        return
    written.sections = sections
    check = written.length_check
    keep = {k: check[k] for k in ("dropped_citations", "duration_policy") if k in check}
    target = None if check.get("duration_policy") == "content" else check.get("target_minutes")
    written.length_check = {**length.measure(sections, target, voice=voice), **keep}
