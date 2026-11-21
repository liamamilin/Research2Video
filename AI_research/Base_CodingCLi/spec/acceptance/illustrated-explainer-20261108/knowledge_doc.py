"""Write a knowledge explainer document from the model's own knowledge (no web search)."""
from dotenv import load_dotenv; load_dotenv(".env")
import json, sys
from pathlib import Path
from core.config import load_system_config
from core.llm import LLMClient, LLMConfig

SYSTEM = """你是知识讲解的研究编辑。根据你已有的知识，为一个知识问题写一份讲解底稿，供视频作者写稿。
不联网，不编造具体机构、报告、日期和精确统计；需要数字时只用公认的常识量级，并说明是大致数。
用 Markdown，按下面的结构写：
# 标题
## 一句话讲清
## 和观众有什么关系
## 核心机制（3 到 5 步，每步讲清前因后果）
## 一个好比喻（最好能画成：几个人在一个具体地方做一件事，手边一个物件）
## 生活里能看到的例子（3 到 5 个具体情境）
## 常见误解（2 到 3 条：大家通常怎么想，实际是怎样）
## 观众能带走什么（一两句话）"""

topic = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
fields = topic.get("fields", {})
user = f"问题：{topic['title']}\n讲述角度：{fields.get('核心角度','')}\n观众：{fields.get('目标受众','')}"
client = LLMClient(LLMConfig.from_system(load_system_config("config"), "ai"))
resp = client.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], temperature=0.4)
out.write_text(resp.content, encoding="utf-8")
print("doc chars", len(resp.content))
