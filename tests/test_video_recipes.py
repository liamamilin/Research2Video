"""Video script recipes: one YAML file per recipe in config/video_recipes/ (spec/video-recipes.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.video import columns as video_columns
from core.video import recipes, store, writer
from tests import golden_video_cells as golden
from tests.test_video_scripts import _csrf, _login, autovideo, video_env  # noqa: F401
from web.services import video_workflow

ROOT = Path(__file__).resolve().parents[1]
CONFIG = str(ROOT / "config")

MINIMAL = """name: 试验
status: experimental
target_minutes: 1.5
research:
  mode: job
  prompt: 核实 {topic_title}：{topic.变化}，{topic.不存在的字段}
writing:
  pipeline: single
  style: 一句话讲完
"""


def _write(folder: Path, key: str, text: str) -> None:
    (folder / "video_recipes").mkdir(parents=True, exist_ok=True)
    (folder / "video_recipes" / f"{key}.yaml").write_text(text, encoding="utf-8")


def test_every_shipped_recipe_builds_and_the_keys_scripts_use_still_resolve():
    every = recipes.load(CONFIG)
    assert not [r.key for r in every.values() if r.error]
    assert {"deep_dive", "flash", "point_short", "ai-developer-roundup-long"} <= set(every)
    assert [r.status for r in every.values()][:3] == ["enabled"] * 3  # enabled ones are listed first
    # a script may name a recipe by the cell id its tags spell
    assert video_columns.get(CONFIG, "ai-developer-quick_take-short").key == "flash"
    assert every["deep_dive"].column.writer == "adapt" and every["flash"].column.writer == "legacy"
    assert every["ai-developer-roundup-long"].column.research_mode == "none"


def test_a_broken_file_is_reported_on_its_own_and_does_not_stop_the_others(tmp_path):
    _write(tmp_path, "good", MINIMAL)
    _write(tmp_path, "typo", MINIMAL.replace("  style:", "  styel:"))
    _write(tmp_path, "notyaml", "name: [unclosed\n")
    every = recipes.load(str(tmp_path))
    assert list(video_columns.load(str(tmp_path))) == ["good"]
    assert "styel" in every["typo"].error and every["notyaml"].error
    rows = {r["recipe"]: r for r in recipes.catalog(str(tmp_path))}
    assert rows["typo"]["usable"] is False and rows["typo"]["key"] is None and "styel" in rows["typo"]["error"]
    with pytest.raises(video_columns.ColumnError, match="typo"):
        video_columns.get(str(tmp_path), "typo")


@pytest.mark.parametrize("change, message", [
    (("pipeline: single", "pipeline: adapt"), "五步改编"),          # 1.5 minutes is too short to adapt
    (("  mode: job\n", "  mode: none\n"), None),                    # no research of its own: fine
    (("  prompt: 核实 {topic_title}：{topic.变化}，{topic.不存在的字段}\n", ""), "research_prompt"),
    (("pipeline: single", "pipeline: five_steps"), "pipeline"),
    (("writing:\n", "output:\n  target: douyin\nwriting:\n"), "output.target"),
    (("status: experimental", "status: blocked"), "reason"),
    (("status: experimental", "status: maybe"), "status"),
])
def test_what_a_recipe_may_say(change, message):
    text = MINIMAL.replace(*change)
    if message is None:
        assert recipes.validate("x", text).column.research_mode == "none"
    else:
        with pytest.raises(video_columns.ColumnError, match=message):
            recipes.validate("x", text)


def test_a_blocked_recipe_makes_no_new_script_but_its_old_ones_still_resolve(tmp_path):
    _write(tmp_path, "paused", MINIMAL.replace("status: experimental", "status: blocked\nreason: 等新声音"))
    assert "paused" not in video_columns.load(str(tmp_path))  # not offered for new scripts
    assert recipes.catalog(str(tmp_path))[0]["usable"] is False
    assert video_columns.get(str(tmp_path), "paused").name == "试验"  # the scripts made with it still read it
    with pytest.raises(video_columns.ColumnError, match="等新声音"):
        video_columns.for_new_script(str(tmp_path), "paused")


def test_api_blocking_a_recipe_keeps_its_scripts_readable_approvable_and_renderable(client, video_env, autovideo):
    from tests.test_video_scripts import _approved_script
    from web.services import video_render

    script = _approved_script("停用前写的")
    path = video_env / "config" / "video_recipes" / "deep_dive.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("status: enabled", "status: blocked\nreason: 换声音", 1),
                    encoding="utf-8")
    _login(client)
    detail = client.get(f"/api/video/scripts/{script['id']}").json()
    assert detail["column"]["key"] == "deep_dive" and detail["version_shown"]["checks"]
    store.update_script(script["id"], status="DRAFT", approved_version=None)
    approved = client.post(f"/api/video/scripts/{script['id']}/approve", headers=_csrf(client), json={})
    assert approved.status_code == 200, approved.text
    render = video_render.create(store.get_script(script["id"]))
    autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
    assert video_render.process(render["id"]) == "COMPLETED"
    made = client.post("/api/video/scripts", headers=_csrf(client),
                       json={"column": "deep_dive", "topic": {"title": "停用后的新选题", "angle": "x"}})
    assert made.status_code == 422  # but no new script


def test_a_value_of_the_wrong_type_is_an_error_on_that_file_only(tmp_path):
    _write(tmp_path, "good", MINIMAL)
    _write(tmp_path, "words", MINIMAL.replace("  mode: job\n", "  mode: job\n  timeout_seconds: 二十五分钟\n"))
    every = recipes.load(str(tmp_path))
    assert "words" in every and "类型不对" in every["words"].error and list(video_columns.load(str(tmp_path))) == ["good"]


@pytest.mark.parametrize("line, message", [
    ("topic:\n  sources: flash\n", "topic.sources"),            # would split into f, l, a, s, h
    ("topic:\n  sources: ['two words']\n", "two words"),   # a source is a job alias or path; whether one exists is a warning

    ("writing_extra: 1\n", "writing_extra"),
])
def test_list_fields_must_be_lists_of_known_names(line, message):
    with pytest.raises(video_columns.ColumnError, match=message):
        recipes.validate("x", MINIMAL + line)
    tones = MINIMAL.replace("  pipeline: single\n", "  pipeline: single\n  allowed_tones: news\n")
    with pytest.raises(video_columns.ColumnError, match="allowed_tones"):
        recipes.validate("x", tones)


def test_the_research_prompt_takes_any_topic_field_and_the_older_names():
    column = recipes.validate("x", MINIMAL).column
    topic = {"title": "某模型涨价", "fields": {"变化": "2 → 4 美元"}}
    assert video_workflow.job_spec(column, topic, "abcdef123456")["prompt"] == "核实 某模型涨价：2 → 4 美元，（未提供）"
    assert video_workflow.fill_topic("{topic_change}/{topic.标题}/{topic_minutes}", column, topic) == "2 → 4 美元/某模型涨价/1.5"
    assert video_workflow.job_spec(recipes.validate("x", MINIMAL + "  ").column, topic, "abcdef123456")["language"] == "zh"
    english = MINIMAL.replace("  mode: job\n", "  mode: job\n  language: en\n")
    assert video_workflow.job_spec(recipes.validate("x", english).column, topic, "abcdef123456")["language"] == "en"


def test_the_topic_fields_the_writer_is_shown_come_from_the_recipe():
    topic = {"title": "某模型涨价", "fields": {"核心角度": "涨了一倍", "变化": "2 → 4 美元", "条目": "不该出现"}}
    column = recipes.validate("x", MINIMAL.replace("research:", "topic:\n  fields: [变化]\nresearch:")).column
    request = writer._request("报告", topic, column.script_style, column.target_minutes, 300,
                              tuple(column.paragraph_units), column.writer_topic_fields())
    assert "- 变化：2 → 4 美元" in request and "核心角度" not in request and "条目" not in request
    default = recipes.validate("x", MINIMAL).column.writer_topic_fields()
    assert default == video_columns.DEFAULT_TOPIC_FIELDS["legacy"]


def test_a_one_call_recipe_has_no_restyle_step(video_env):
    script = store.create_script("flash", {"id": None, "title": "快讯", "fields": {}})
    store.update_script(script["id"], status="DRAFT", report_path="output/x.md")
    (video_env / "output" / "x.md").write_text("报告", encoding="utf-8")
    with pytest.raises(video_workflow.RestyleError, match="single"):
        video_workflow.restyle(script["id"], tone="news")


def test_api_read_check_save_copy_and_delete(client, video_env):
    _login(client, "viewer", "viewer-pass-123")
    rows = client.get("/api/video/recipes").json()
    assert {"deep_dive", "flash"} <= {r["key"] for r in rows}
    flash = client.get("/api/video/recipes/flash").json()
    assert flash["pipeline"] == "single" and "research:" in flash["content"] and flash["column"]["key"] == "flash"
    assert client.put("/api/video/recipes/flash", json={"content": flash["content"]}, headers=_csrf(client)).status_code == 403

    _login(client)
    bad = client.post("/api/video/recipes/flash/validate", headers=_csrf(client),
                      json={"content": "name: x\nwriting: {styel: 1}\n"}).json()
    assert bad["ok"] is False and "styel" in bad["error"]
    refused = client.put("/api/video/recipes/flash", headers=_csrf(client),
                         json={"content": flash["content"].replace("  pipeline: single", "  pipeline: nope")})
    assert refused.status_code == 422  # a bad edit never reaches the file
    assert "pipeline: single" in (video_env / "config" / "video_recipes" / "flash.yaml").read_text(encoding="utf-8")

    edited = flash["content"].replace("target_minutes: 1.25", "target_minutes: 1.5")
    stale = client.put("/api/video/recipes/flash", headers=_csrf(client),
                       json={"content": edited, "expected_mtime": flash["mtime"] - 100})
    assert stale.status_code == 409
    saved = client.put("/api/video/recipes/flash", headers=_csrf(client),
                       json={"content": edited, "expected_mtime": flash["mtime"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["target_minutes"] == 1.5 and "# 文稿配方" in saved.json()["content"]  # comments kept
    assert list((video_env / "state" / "backups").glob("video_recipe_flash_*.yaml"))

    copied = client.post("/api/video/recipes", headers=_csrf(client), json={"key": "flash_non_dev", "copy_from": "flash"})
    assert copied.status_code == 201, copied.text
    assert copied.json()["status"] == "experimental"  # a copy has not been rendered yet
    assert client.post("/api/video/recipes", headers=_csrf(client),
                       json={"key": "flash_non_dev", "copy_from": "flash"}).status_code == 409
    assert client.post("/api/video/recipes", headers=_csrf(client),
                       json={"key": "Bad Name", "copy_from": "flash"}).status_code == 422

    store.create_script("flash", {"id": None, "title": "用着呢", "fields": {}})
    assert client.delete("/api/video/recipes/flash", headers=_csrf(client)).status_code == 409
    assert client.delete("/api/video/recipes/flash_non_dev", headers=_csrf(client)).status_code == 200
    assert client.get("/api/video/recipes/flash_non_dev").status_code == 404


def test_api_a_recipe_without_research_needs_a_report_to_start_from(client, video_env):
    _login(client)
    folder = video_env / "config" / "video_recipes"
    (folder / "no_research.yaml").write_text(MINIMAL.replace("  mode: job\n", "  mode: none\n"), encoding="utf-8")
    made = client.post("/api/video/scripts", headers=_csrf(client),
                       json={"column": "no_research", "topic": {"title": "一个手写选题", "angle": "x"}})
    assert made.status_code == 422 and "report_required" in made.text


def test_the_shipped_recipes_match_the_golden_snapshot_install():
    # the fixture copy the other tests use is the shipped files, byte for byte
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = golden.install_recipes(tmp)
        for path in (ROOT / "config" / "video_recipes").glob("*.yaml"):
            assert (folder / path.name).read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
