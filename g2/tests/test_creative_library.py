import copy
import hashlib

import pytest

from g2 import creative_library as library
from g3.app import CreateJobRequest


def request(**extra):
    return CreateJobRequest(source_text="用于验证创作资源和制作配方的完整素材。" * 5, **extra).model_dump(exclude_none=True)


def test_original_taxonomy_ids_and_capabilities_are_distinct_from_examples(monkeypatch, tmp_path):
    monkeypatch.setenv('AUTOVIDEO_MUSIC_MODEL_ROOT',str(tmp_path/'disconnected'))
    data = library.public_catalog()
    assert len(data["forms"]) == 31
    assert {r["id"] for r in data["forms"]} == {f"F{i:02}" for i in range(1, 18)} | {f"MF{i:02}" for i in range(1, 15)}
    assert len([r for r in data["forms"] if r.get("example")]) == 25
    assert sum(r["kind"] == "video" for r in data["forms"]) == 17
    assert all(not r["executors"] for r in data["forms"] if r["kind"] == "music")
    assert not data["capabilities"]["music_generation"]
    assert all("relative_path" not in r.get("example", {}) for r in data["forms"])
    assert len(data["packaging"]["components"]) == 5


@pytest.mark.parametrize("key,form", [("ai_news", "kinetic_typography"), ("how_to", "process_diagram"), ("science_explainer", "auto")])
def test_recipes_resolve_into_supported_production(key, form):
    prepared = library.prepare_request(request(production_recipe=key))
    assert prepared["video_form"] == form
    assert prepared["duration_mode"] == "content"
    assert prepared["creative_intent"]["audience"]
    assert prepared["packaging"]["label"]
    snapshot = prepared["production_recipe_resolved"]
    assert snapshot["recipe"]["id"] == key
    assert snapshot["packaging_sha256"] == prepared["packaging_resolved"]["sha256"]
    assert snapshot["music"] == {"mode": "none", "background_music_generated": False}


def test_explicit_choices_override_defaults_including_false_and_empty_strings():
    prepared = library.prepare_request(request(production_recipe="how_to", video_form="information_motion",
        creative_intent={"tone": "", "audience": "开发者"}, packaging={"sound": {"enabled": True}, "outro": {"enabled": False}}))
    assert prepared["video_form"] == "information_motion"
    assert prepared["creative_intent"]["tone"] == ""
    assert prepared["creative_intent"]["purpose"]
    assert prepared["creative_intent"]["audience"] == "开发者"
    assert "outro" not in prepared["package"]
    assert prepared["packaging_resolved"]["components"]["sound"]["enabled"]


def test_saved_recipe_and_packaging_survive_catalog_changes_and_revisions(monkeypatch):
    parent = library.prepare_request(request(production_recipe="ai_news"))
    original = copy.deepcopy(parent)
    monkeypatch.setattr(library, "catalog", lambda: {"recipes": []})
    assert library.prepare_request(parent) == original
    child = library.prepare_request({**parent, "voice_id": "zm_yunxi", "source_text": "修订后的素材"})
    assert child["production_recipe_resolved"]["recipe"] == original["production_recipe_resolved"]["recipe"]
    assert child["production_recipe_resolved"]["selection"]["voice_id"] == "zm_yunxi"
    assert child["packaging_resolved"] == parent["packaging_resolved"]
    assert parent == original


def test_corrupt_recipe_snapshot_is_rejected():
    prepared = library.prepare_request(request(production_recipe="ai_news"))
    prepared["production_recipe_resolved"]["recipe"]["name"] = "被修改"
    with pytest.raises(library.CreativeResourceError, match="完整性"):
        library.prepare_request(prepared)


def test_preview_is_pinned_to_registered_files_and_hashes(tmp_path, monkeypatch):
    file = tmp_path / "case.wav"
    file.write_bytes(b"registered audio example")
    data = {"forms": [{"id": "MF07", "kind": "music", "example": {
        "relative_path": "case.wav", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}}]}
    monkeypatch.setattr(library, "catalog", lambda: data)
    monkeypatch.setenv("AUTOVIDEO_CREATIVE_ASSET_ROOT", str(tmp_path))
    assert library.preview("MF07") == (file, "audio/wav")
    with pytest.raises(FileNotFoundError):
        library.preview("../../case.wav")
    file.write_bytes(b"changed")
    with pytest.raises(library.CreativeResourceError, match="不一致"):
        library.preview("MF07")
    data["forms"][0]["example"]["relative_path"] = "../outside.wav"
    with pytest.raises(FileNotFoundError):
        library.preview("MF07")


def test_legacy_requests_keep_original_defaults():
    prepared = library.prepare_request(request())
    assert prepared["video_form"] == "knowledge_cards"
    assert prepared["duration_mode"] == "fixed"
    assert "production_recipe_resolved" not in prepared
    assert prepared["packaging_resolved"]["selection"] == {"preset": "common"}
