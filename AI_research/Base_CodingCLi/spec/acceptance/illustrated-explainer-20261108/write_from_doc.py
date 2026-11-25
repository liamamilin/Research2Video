from dotenv import load_dotenv; load_dotenv(".env")
import json, sys
from pathlib import Path
from core.config import load_system_config
from core.llm import LLMConfig
from core.video import recipes, writer
topic = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
doc, out = Path(sys.argv[2]).read_text(encoding="utf-8"), Path(sys.argv[3])
column = recipes.expanded("config")["illustrated_explainer"].column
w = writer.write_for_column(column=column, content_context={}, report=doc, citation_check=None, topic=topic,
                            llm_config=LLMConfig.from_system(load_system_config("config"), "ai"))
sections = w.sections if hasattr(w, "sections") else w[0]
out.write_text(json.dumps(sections, ensure_ascii=False, indent=1), encoding="utf-8")
md = [f"# {topic['title']}\n"] + [f"## {s['title']}\n\n" + "\n\n".join(p["text"] for p in s["paragraphs"]) + "\n" for s in sections]
out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
print(out.name, "sections", len(sections), "chars", sum(len(p["text"]) for s in sections for p in s["paragraphs"]))
