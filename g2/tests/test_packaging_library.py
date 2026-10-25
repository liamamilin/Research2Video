import copy

import pytest

from g2 import packaging_library as library
from g2.package_kit import PackageConfigError

SOURCES = [{"title": "官方说明", "url": "https://www.example.com/release", "date": "2026-10-23"}]


def test_common_defaults_and_recipe_overrides_cover_all_five_slots():
    record = library.resolve({"recipe": "deep_dive", "label": "深度解读", "intro": {"seconds": 2}}, sources=SOURCES)
    assert set(record["component_versions"]) == {"intro", "outro", "badge", "source_card", "sound"}
    assert record["package"]["intro"]["title"] == record["package"]["outro"]["title"] == "深度解读"
    assert record["package"]["intro"]["seconds"] == 2
    assert record["package"]["outro"]["seconds"] == 2.5
    assert record["badge"]["text"] == "深度解读"
    assert record["source_card"]["sources"] == [{"title": "官方说明", "domain": "example.com", "date": "2026-10-23"}]
    assert record["package"]["intro"]["sound"] == "synth_sting"


def test_explicit_component_switches_and_no_sources_are_recorded():
    record = library.resolve({"intro": {"enabled": False}, "sound": {"enabled": False}, "badge": {"enabled": False}})
    assert "intro" not in record["package"] and "sound" not in record["package"]["outro"]
    assert record["badge"]["text"] == "" and record["source_card_state"] == "no_sources"
    off = library.resolve({"enabled": False}, sources=SOURCES)
    assert off["package"] is None and off["source_card"] is None


def test_retry_and_revision_freeze_defaults_and_detect_corrupted_snapshots(monkeypatch):
    saved = library.prepare_request({"packaging": {"label": "专题速览"}, "sources": SOURCES})
    old = copy.deepcopy(saved["packaging_resolved"])
    changed = library.catalog()
    changed["presets"]["common"]["intro"]["seconds"] = 5
    monkeypatch.setattr(library, "catalog", lambda: changed)
    assert library.prepare_request(saved)["packaging_resolved"] == old
    saved["packaging_resolved"]["package"]["intro"]["seconds"] = 4
    with pytest.raises(PackageConfigError, match="完整性"):
        library.prepare_request(saved)


@pytest.mark.parametrize("selection", [
    {"intro": {"component": "../../arbitrary.py"}}, {"source_card": {"sources": SOURCES}},
    {"preset": "unknown"}, {"outro": {"seconds": float("nan")}}, {"enabled": "false"},
    {"sound": {"component": "stinger"}}, {"intro": {"subtitle": "第一行\n第二行"}},
])
def test_invalid_references_and_model_supplied_source_text_are_rejected(selection):
    with pytest.raises(PackageConfigError):
        library.resolve(selection)


def test_legacy_package_is_registered_without_adding_unrequested_parts():
    package = {"accent": "#112233", "outro": {"template": "end_card", "title": "旧栏目", "seconds": 2}}
    saved = library.prepare_request({"package": package})
    assert saved["package"] == package
    assert saved["packaging_resolved"]["legacy_request"] is True
    assert "intro" not in saved["package"]


def test_old_source_cards_are_not_duplicated_and_conflicting_sources_fail():
    record = library.resolve(sources=SOURCES)
    props = {k: v for k, v in record["source_card"].items() if k in {"title", "sources"}}
    ir = {"scenes": [{"component": "source_card", "props": copy.deepcopy(props)}]}
    assert library.source_already_rendered(record, ir)
    assert not library.source_already_rendered(record, {"scenes": []})
    ir["scenes"][0]["props"]["sources"][0]["title"] = "另一份来源"
    with pytest.raises(PackageConfigError, match="来源不同"):
        library.source_already_rendered(record, ir)
