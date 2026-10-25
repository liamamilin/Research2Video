from __future__ import annotations

from core.video import quality


def version(number, texts, edit_type="GENERATED", **extra):
    return {"version": number, "title": f"v{number}", "edit_type": edit_type, "length_check": {"status": "OK"},
            "meta": extra.get("meta") or {}, "sections": [{"title": "节", "paragraphs": [{"text": t} for t in texts]}]}


def test_identical_text_is_all_kept():
    result = quality.compare(version(1, ["第一段内容。", "第二段内容。"]), version(2, ["第一段内容。", "第二段内容。"], "EDITED"))
    assert (result["unchanged"], result["edited"], result["dropped"], result["added"]) == (2, 0, 0, 0)
    assert result["kept_share"] == 1.0 and result["new_share"] == 0.0 and result["examples"] == []


def test_paragraphs_are_told_apart_as_unchanged_edited_dropped_and_added():
    draft = version(1, ["开场一句话不动。", "这一段说缓存折扣可能拿不到，需要核实。", "这一段整个删掉，没有任何保留价值呀。"])
    final = version(2, ["开场一句话不动。", "这一段说缓存折扣可能拿不到，要先核实再说。", "全新加的一段话与其他完全不同"], "EDITED")
    result = quality.compare(draft, final)
    assert (result["unchanged"], result["edited"], result["dropped"], result["added"]) == (1, 1, 1, 1)
    assert result["examples"][0]["before"].startswith("这一段说缓存") and result["examples"][0]["after"].endswith("再说。")
    assert 0 < result["kept_share"] < 1


def test_the_baseline_is_the_newest_generated_version_at_or_before_the_approved_one():
    versions = [version(1, ["甲"]), version(2, ["乙"], "EDITED"), version(3, ["丙"]), version(4, ["丁"], "EDITED")]
    assert quality.baseline(versions, 4)["version"] == 3
    assert quality.baseline(versions, 2)["version"] == 1
    assert quality.baseline([version(1, ["甲"], "EDITED")], 1) is None


def test_a_script_report_needs_an_approved_version_and_a_draft_and_carries_the_drafts_own_signals():
    meta = {"checks": {"model_ran": True, "summary": {"serious": 1, "must": 2}}, "problems": ["x"]}
    versions = [version(1, ["第一段内容。"], meta=meta), version(2, ["第一段内容改了。"], "EDITED")]
    script = {"id": "s", "column_key": "flash", "approved_version": 2}
    row = quality.script_report(script, versions)
    assert row["draft_version"] == 1 and row["edited_by_hand"] is True
    assert row["signals"] == {"length_status": "OK", "serious": 1, "must": 2, "problems": 1, "audited": True}
    assert quality.script_report({**script, "approved_version": None}, versions) is None
    assert quality.script_report(script, [versions[1]]) is None


def test_the_summary_is_per_column_with_counts():
    rows = [{"column_key": "flash", "kept_share": 0.5, "draft_paragraphs": 4, "unchanged": 1, "edited_by_hand": True},
            {"column_key": "flash", "kept_share": 0.9, "draft_paragraphs": 6, "unchanged": 5, "edited_by_hand": False},
            {"column_key": "point_short", "kept_share": None, "draft_paragraphs": 0, "unchanged": 0, "edited_by_hand": True}]
    summary = quality.summarize(rows)
    assert summary["flash"] == {"scripts": 2, "edited_by_hand": 1, "median_kept_share": 0.7, "paragraphs_untouched": 0.6}
    assert summary["point_short"]["median_kept_share"] is None and summary["point_short"]["paragraphs_untouched"] is None
