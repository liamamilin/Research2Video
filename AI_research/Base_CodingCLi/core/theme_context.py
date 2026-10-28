"""Matrix categories and topic-specific research briefs shared by discovery and manual creation."""
from __future__ import annotations

from . import matrices
from .config import load_job
from .video import content_columns


def categories(config_dir: str, matrix_key: str) -> list[dict]:
    matrix = matrices.load_all(config_dir).get(matrix_key)
    if matrix is None or matrix.archived:
        raise ValueError("矩阵不存在或已归档")
    rows = [{"key": c.key, "name": c.name, "promise": c.promise, "audience": c.audience,
             "notes": c.notes, "production_ids": list(c.production_ids), "type": c.type}
            for c in content_columns.load_all(config_dir).values() if c.matrix == matrix_key and c.usable]
    return rows or [{"key": "general", "name": matrix.name + " · 主题研究", "promise": matrix.domain or matrix.name,
                     "audience": matrix.content.get("audience", ""), "notes": "", "production_ids": [], "type": "explainer"}]


def research_spec(theme: dict, category: dict, matrix, *, sources: list[str]) -> dict:
    signals = "\n".join(f"- {s.get('title', '')}: {s['url']}\n  {s.get('excerpt', '')}"
                        for s in theme.get("signals", []) if s.get("url"))
    prompt = f"""根据素材，产出目标观众愿意看、听得懂、想继续看的视频。
当前步骤：围绕已经确定的主题研究，准备后续作者可以展开讲述的材料。
主题：{theme['title']}
领域：{matrix.domain or matrix.name}
栏目：{category['name']}
栏目交付目标：{category['promise']}
目标观众：{theme['audience'] or category['audience'] or matrix.content.get('audience', '')}
创作角度：{theme['angle']}
用户补充材料：{theme['notes']}
发现时的线索（用于确定问题，继续阅读相关正文）：
{signals or '用户自主命题。'}
研究日期：{{date}}

先明确这个题目需要回答的几个子问题。阅读提供的相关来源，定向搜索并阅读能回答这些问题的页面。
有用的旧教程、现行规则和案例同样可以使用；页面是否今天新增不决定它是否与本主题有关。
围绕信息缺口继续补充；材料充分后形成判断，不预设必须购买、升级或支持某种结论。
“使用一个月”等标题表述默认是目标观众所处的阶段；用户提供个人经历时把具体经历融入材料。
保留具体场景中的任务、输入、操作、结果和选择依据；将实际材料、可操作演示与待实测方案写清。
优先提供足以讲生动、讲明白的例子；用户给出的数据、计算与结论直接作为创作输入。

输出一份 Markdown 主题研究底稿：
# 主题标题
## 要回答的问题与主要判断
## 观众场景与具体材料
## 问题逐项展开（每个问题的具体资料、例子、机制和适用条件）
## 可用于讲述的操作与结果
## 画面材料（页面链接、展示区域、对应讲述内容、输入与输出的演示建议）
## 原始材料中值得保留的细节
## 来源清单
这是一份题目已经确定的研究底稿，不再生成多个候选选题。后续作者可以自主组织讲述顺序。
"""
    return {"prompt": prompt, "keywords": [theme["title"], matrix.domain or matrix.name], "language": "zh",
            "sources": list(dict.fromkeys(sources)), "sources_limit": 6, "max_searches": 8,
            "runtime": {"timeout_seconds": 2400, "skip_if_running": True}}


def source_specs(category: dict, config_dir: str, jobs_dir: str, matrix) -> list[str]:
    from .video import production
    configs = production.load_all(config_dir, jobs_dir)
    jobs = [configs[key].job for key in category["production_ids"] if key in configs and not configs[key].errors]
    specs = []
    for name in jobs or matrix.jobs[:2]:
        specs.extend((load_job(jobs_dir, name) or {}).get("sources") or [])
    return list(dict.fromkeys(specs))[:8]
