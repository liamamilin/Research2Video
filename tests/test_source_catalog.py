"""The information sources page reads the registry, the Jobs and the runs' history; it stores nothing."""

from __future__ import annotations

import json

from web.services import source_catalog


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_catalog_shows_users_last_fetch_and_jobs_that_name_a_missing_source(tmp_path):
    config, jobs, state = tmp_path / "config", tmp_path / "jobs", tmp_path / "state"
    _write(config / "sources.yaml", "a:\n  name: A\n  spec: rss:https://a.example/f\n  tags: [tech]\n"
                                    "b:\n  name: B\n  spec: rss:https://b.example/f\n  tags: [tech]\n"
                                    "c:\n  name: C\n  spec: rss:https://c.example/f\n  tags: [other]\n")
    _write(jobs / "m" / "tech.yaml", "name: 科技周报\nprompt: p\nsources: ['tag:tech', 'rss:https://free.example/f']\n")
    _write(jobs / "m" / "broken.yaml", "name: 坏的\nprompt: p\nsources: ['source:nope']\n")
    _write(jobs / "m" / "plain.yaml", "name: 没有源\nprompt: p\n")
    history = state / "history" / "m_tech.jsonl"
    _write(history, "\n".join(json.dumps(e) for e in [
        {"ts": "2026-10-20T03:00:00+0800", "status": "success", "usage": {"fetched": {"rss:https://a.example/f": 9}}},
        {"ts": "2026-10-21T03:00:00+0800", "status": "success", "usage": {"fetched": {
            "rss:https://a.example/f": 7, "rss:https://b.example/f": "失败：HTTP Error 403: Forbidden"}}},
    ]) + "\n")

    out = source_catalog.catalog(str(config), str(jobs), str(state))
    rows = {r["spec"]: r for r in out["sources"]}
    assert rows["rss:https://a.example/f"]["last"] == {"at": "2026-10-21T03:00:00+0800", "job": "m/tech", "items": 7, "error": ""}
    assert rows["rss:https://b.example/f"]["last"]["error"].startswith("失败")
    assert rows["rss:https://a.example/f"]["used_by"] == [{"job": "m/tech", "name": "科技周报"}]
    assert rows["rss:https://c.example/f"]["used_by"] == [] and rows["rss:https://c.example/f"]["last"] is None
    free = rows["rss:https://free.example/f"]  # written out in full in a Job, not registered
    assert free["id"] == "" and free["used_by"][0]["job"] == "m/tech" and free["last"] is None
    assert out["problems"] == [{"job": "m/broken", "problem": "信息源注册表里没有 nope"}]
    assert [r["id"] for r in out["sources"]][-1] == "c"  # the ones nobody uses come last
