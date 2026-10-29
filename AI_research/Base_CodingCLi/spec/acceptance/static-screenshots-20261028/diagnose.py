"""Reproduce the existing local vision failure without changing production state."""
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.config import load_system_config
from core.llm import LLMClient, LLMConfig
from core.video.image_quality import VisionDecision, vision_messages
from core.video.adapt.common import parse_json

out = ROOT / "state/validation/static-screenshots-20261028/diagnosis" / ("vision-" + uuid4().hex[:8])
out.mkdir(parents=True, exist_ok=False)
with sqlite3.connect((ROOT / "state/video.db").as_uri() + "?mode=ro", uri=True) as db:
    audit = json.loads(db.execute("SELECT visuals FROM renders WHERE id=?",
        ("88db595549444e5f8b6ee024d0c4d3dd",)).fetchone()[0])
need = audit["needs"][1]
raw = (ROOT / audit["pages"][1]["evidence"]).read_bytes()
cfg = replace(LLMConfig.from_system(load_system_config(str(ROOT / "config")), "visual_ai"),
              timeout=90, request_timeout=90, max_retries=0, stream=False, max_tokens=1000)
client = LLMClient(cfg)
try:
    response = client.chat(vision_messages(raw, need, audit["needs"])[0], temperature=0, stream=False)
    (out / "vision-response.txt").write_text(response.content)
    try:
        result = VisionDecision.model_validate(parse_json(response.content))
        diagnostic = {"state": "passed", "decision": result.model_dump()}
    except Exception as exc:
        diagnostic = {"state": "failed", "type": type(exc).__name__,
                      "errors": exc.errors(include_input=False) if hasattr(exc, "errors") else str(exc)}
    (out / "diagnostic.json").write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(diagnostic, ensure_ascii=False, default=str), flush=True)
finally:
    client.close()
