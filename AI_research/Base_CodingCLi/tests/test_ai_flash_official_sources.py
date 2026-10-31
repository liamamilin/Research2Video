"""Official product updates feed the existing AI flash report and writer."""
from pathlib import Path

import yaml

from core.sources import resolve


def test_ai_flash_includes_official_updates_and_pro_benefits_with_existing_feeds():
    root = Path(__file__).resolve().parents[1]
    job = yaml.safe_load((root / "jobs/monitoring/ai_flash_discovery.yaml").read_text())
    specs = resolve(job["sources"], str(root / "config"))
    assert "page:https://help.openai.com/en/articles/6825453-chatgpt-release-notes" in specs
    assert "page:https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers" in specs
    assert "rss:https://openai.com/news/rss.xml" in specs
    assert "tldr:ai" in specs and "hf_models:trending" in specs
    assert "rss:https://aicatchup.com/rss.xml" in specs
    assert job["search"] is False and job["sources_limit"] == 10
    assert job["topics"]["alias"] == "flash"
