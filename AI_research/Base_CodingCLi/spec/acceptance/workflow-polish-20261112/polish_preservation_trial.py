"""Replay current polishing on an author draft with a previously saved result."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from opening_run import save, setup


def main(source: Path, out: Path) -> None:
    if out.exists():
        raise FileExistsError(f"Use a fresh isolated directory: {out}")
    draft = json.loads((source / "direct.json").read_text())
    original = json.loads((source / "generated.json").read_text())
    setup(out)
    from core.video import polish, writer
    from web.services import video_workflow

    manuscript = writer.WrittenScript(**draft)
    config, _ = video_workflow._llm_configs(None)
    polish.apply(manuscript, config, voice=draft["length_check"].get("voice"))
    save(out / "comparison.json", {"source": str(source), "version": polish.PROMPT_VERSION,
         "original_changes": original["meta"]["polish"]["changes"], "changes": manuscript.meta["polish"]["changes"],
         "error": manuscript.meta["polish"].get("error")})
    save(out / "generated.json", {"title": manuscript.title, "sections": manuscript.sections,
         "length_check": manuscript.length_check, "meta": manuscript.meta})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.source, args.output.resolve())
