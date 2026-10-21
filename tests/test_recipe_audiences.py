"""A recipe written once for several audiences: each audience is a profile, and each is a column of its own."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from core.video import columns as video_columns
from core.video import recipes
from tests.test_video_scripts import video_env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
CONFIG = str(ROOT / "config")

PROFILE = """name: {name}
depth: {depth}
text:
  rules: {rules}
"""

RECIPE = """name: 试验
status: enabled
tags:
  domain: {key: ai, name: AI}
  type: {key: quick_take, name: 快讯}
  format: {key: short, name: 短}
audiences: [kids, adults]
target_minutes: 1.5
research:
  mode: job
  prompt: |
    给{{audience}}的调研
    {{audience.rules}}
    结尾 {topic_title} {date}
writing:
  pipeline: single
  default_style:
    depth: '{{audience.depth}}'
    tone: news
  style: |
    讲给{{audience}}听
    {{audience.rules}}
"""


def _config(tmp_path: Path, recipe: str = RECIPE, **profiles: dict) -> str:
    (tmp_path / "video_recipes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "video_recipes" / "quick.yaml").write_text(recipe, encoding="utf-8")
    folder = tmp_path / "video_parts" / "audience"
    folder.mkdir(parents=True, exist_ok=True)
    profiles = profiles or {"kids": {"name": "孩子", "depth": "intro", "rules": "'说得简单些'"},
                            "adults": {"name": "大人", "depth": "intermediate", "rules": "''"}}
    for key, fields in profiles.items():
        (folder / f"{key}.yaml").write_text(PROFILE.format(**fields), encoding="utf-8")
    return str(tmp_path)


def test_shipped_recipes_come_out_exactly_as_they_were_written_out_per_audience():
    """tests/fixtures/recipe_columns.json is every recipe's column as it was before audiences became profiles."""
    gold = json.loads((ROOT / "tests" / "fixtures" / "recipe_columns.json").read_text(encoding="utf-8"))
    now = recipes.expanded(CONFIG)
    assert not [k for k, r in now.items() if r.error]
    for key, want in gold.items():
        r = now[key]
        got = {"status": r.status, "label": r.label, "tags": r.tags, "cell_id": r.cell_id,
               "resolved_sha256": r.resolved_sha256, "column": dataclasses.asdict(r.column)}
        assert json.loads(json.dumps(got, ensure_ascii=False)) == want, key


def test_each_audience_is_a_column_named_by_the_cell_its_tags_spell(tmp_path):
    config = _config(tmp_path)
    every = recipes.expanded(config)
    assert set(every) == {"quick", "ai-adults-quick_take-short"}  # the first audience keeps the file's own key
    assert every["quick"].audience == "kids" and every["ai-adults-quick_take-short"].audience == "adults"
    assert every["ai-adults-quick_take-short"].base == "quick"
    assert set(video_columns.load(config)) == set(every)
    assert video_columns.get(config, "ai-adults-quick_take-short").audience == "大人"
    assert every["quick"].tags["audience"] == {"key": "kids", "name": "孩子"}
    assert every["quick"].label == "快讯 · 孩子 · 短"
    assert recipes.provenance(config, "ai-adults-quick_take-short")["uses"]["audience"]["name"] == "adults"


def test_an_audiences_words_go_in_and_an_empty_one_leaves_no_blank_line(tmp_path):
    every = recipes.expanded(_config(tmp_path))
    kids, adults = every["quick"].column, every["ai-adults-quick_take-short"].column
    assert kids.script_style == "讲给孩子听\n说得简单些"
    assert adults.script_style == "讲给大人听"
    assert kids.research_prompt == "给孩子的调研\n说得简单些\n结尾 {topic_title} {date}"  # run-time placeholders stay
    assert adults.research_prompt == "给大人的调研\n结尾 {topic_title} {date}"
    assert (kids.default_depth, adults.default_depth) == ("intro", "intermediate")


def test_an_experimental_audience_makes_its_recipe_experimental(tmp_path):
    config = _config(tmp_path, kids={"name": "孩子", "depth": "intro", "rules": "''"},
                     adults={"name": "大人", "depth": "intermediate", "rules": "''"})
    path = Path(config) / "video_parts" / "audience" / "kids.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "status: experimental\n", encoding="utf-8")
    every = recipes.expanded(config)
    assert (every["quick"].status, every["ai-adults-quick_take-short"].status) == ("experimental", "enabled")


@pytest.mark.parametrize("edit, message", [
    (lambda r: r.replace("{{audience.rules}}", "{{audience.nothing}}", 1), "没写 nothing"),
    (lambda r: r.replace("{{audience.rules}}", "{{audiense}}", 1), "没填的"),
    (lambda r: r.replace("audiences: [kids, adults]", "audiences: [kids, kids]"), "不能重复"),
    (lambda r: r.replace("audiences: [kids, adults]", "audiences: [kids, ghosts]"), "ghosts"),
    (lambda r: r.replace("audiences: [kids, adults]", "audiences: [kids, adults]\naudience: 孩子"), "不要再写 audience"),
    (lambda r: r.replace("  type: {key: quick_take, name: 快讯}\n", ""), "要写全 tags"),
])
def test_a_recipe_that_mixes_up_its_audience_words_does_not_build(tmp_path, edit, message):
    config = _config(tmp_path, edit(RECIPE))
    with pytest.raises(video_columns.ColumnError, match=message):
        recipes.validate("quick", (Path(config) / "video_recipes" / "quick.yaml").read_text(encoding="utf-8"), config)


def test_saving_refuses_a_recipe_when_only_a_later_audience_would_break(tmp_path):
    config = _config(tmp_path)
    (Path(config) / "video_parts" / "audience" / "adults.yaml").write_text("depth: x\n", encoding="utf-8")  # no name
    text = (Path(config) / "video_recipes" / "quick.yaml").read_text(encoding="utf-8")
    assert recipes.parse("quick", text, config_dir=config).error == ""  # the first audience is fine
    with pytest.raises(video_columns.ColumnError, match="adults"):
        recipes.validate("quick", text, config)


def test_a_recipe_without_audiences_is_left_alone_and_cannot_use_the_tokens(tmp_path):
    plain = "name: 老写法\nstatus: enabled\naudience: 开发者\nresearch:\n  mode: none\nwriting:\n  style: 给{{audience}}\n"
    config = _config(tmp_path, plain)
    assert "没填的" in recipes.expanded(config)["quick"].error
    ok = _config(tmp_path, plain.replace("给{{audience}}", "写给开发者"))
    column = recipes.expanded(ok)["quick"].column
    assert (column.audience, column.script_style) == ("开发者", "写给开发者")


def test_a_variant_never_takes_a_key_a_file_already_has(tmp_path):
    config = _config(tmp_path)
    (Path(config) / "video_recipes" / "ai-adults-quick_take-short.yaml").write_text(
        "name: 自己写的\nstatus: enabled\nresearch:\n  mode: none\nwriting:\n  style: 自己写的\n", encoding="utf-8")
    assert recipes.expanded(config)["ai-adults-quick_take-short"].column.script_style == "自己写的"


def test_audience_parts_are_listed_with_the_recipe_files_that_use_them(tmp_path):
    rows = {(p["kind"], p["name"]): p for p in recipes.parts(_config(tmp_path))}
    assert rows[("audience", "kids")]["used_by"] == ["quick"] and rows[("audience", "adults")]["used_by"] == ["quick"]
    shipped = {(p["kind"], p["name"]): p for p in recipes.parts(CONFIG)}
    assert set(shipped[("audience", "non_tech")]["used_by"]) == {"flash", "point_short", "deep_dive",
                                                                 "ai-developer-roundup-long", "report_digest",
                                                                 "top_finding", "action_list"}


def test_api_shows_audiences_and_rebuilds_every_version_when_a_profile_is_edited(client, video_env):
    from tests.test_video_scripts import _csrf, _login

    _login(client)
    flash = client.get("/api/video/recipes/flash").json()
    assert flash["audiences"] == [{"key": "developer", "recipe": "flash"},
                                  {"key": "non_tech", "recipe": "ai-non_tech-quick_take-short"}]
    assert flash["uses"]["audience"] == {"kind": "audience", "name": "developer"}
    row = next(r for r in client.get("/api/video/recipes").json() if r["key"] == "flash")
    assert row["audiences"] == flash["audiences"]
    assert "ai-non_tech-quick_take-short" not in {r["key"] for r in client.get("/api/video/recipes").json()}  # one row per file

    part = client.get("/api/video/recipes/parts/audience/non_tech").json()
    assert {"flash", "point_short", "deep_dive", "ai-developer-roundup-long", "report_digest", "top_finding",
            "action_list"} == set(part["used_by"])
    refused = client.put("/api/video/recipes/parts/audience/non_tech", headers=_csrf(client),
                         json={"content": part["content"].replace("name: 普通用户\n", "")})
    assert refused.status_code == 422 and "ai-non_tech-deep_dive-long" in refused.text  # the columns that would break
    saved = client.put("/api/video/recipes/parts/audience/non_tech", headers=_csrf(client),
                       json={"content": part["content"].replace("每月多花", "每年多花"), "expected_mtime": part["mtime"]})
    assert saved.status_code == 200, saved.text
    config = str(video_env / "config")
    assert "每年多花" in video_columns.get(config, "ai-non_tech-quick_take-short").script_style
    assert "每年多花" not in video_columns.get(config, "flash").script_style


def test_a_recipe_offers_other_lengths_only_when_it_says_so(tmp_path):
    config = _config(tmp_path, RECIPE.replace("target_minutes: 1.5", "target_minutes: 4\nminutes_range: [3, 6]"))
    every = recipes.expanded(config)
    assert every["quick"].minutes_range == (3.0, 6.0) and "quick-m5" not in every  # no list of every length
    five = recipes.find(config, "quick-m5")
    assert (five.key, five.column.key, five.column.target_minutes, five.audience, five.base) == ("quick-m5", "quick-m5", 5, "kids", "quick")
    seven = recipes.find(config, "ai-adults-quick_take-short-m5p5")  # another audience, at half a minute
    assert (seven.column.target_minutes, seven.audience) == (5.5, "adults") and not seven.error
    assert video_columns.get(config, "quick-m3").target_minutes == 3
    assert recipes.find(config, "quick-m7").error and "3–6 分钟" in recipes.find(config, "quick-m7").error
    assert recipes.find(config, "quick-m4p3") is None  # not a key at all
    assert recipes.find(config, "quick-m5-m5") is None  # a length of a length is not a thing
    fixed = _config(tmp_path / "fixed")
    assert "不能改" in recipes.find(fixed, "quick-m5").error
    # the other length changes the minutes and nothing else
    own, other = dataclasses.asdict(every["quick"].column), dataclasses.asdict(five.column)
    assert {k for k in own if own[k] != other[k]} == {"key", "target_minutes"}


@pytest.mark.parametrize("edit, message", [
    ("minutes_range: [3, 6]", "半分钟一档"),
    ("minutes_range: [6, 3]", "半分钟一档"),
    ("minutes_range: [5, 6]", "自己的 target_minutes 要在"),
    ("minutes_range: [3, 2]", "半分钟一档"),
    ("minutes_range: [0.5, 4]", "时长 0.5 分钟这一版有问题"),  # shorter than any column can be
])
def test_a_bad_minutes_range_does_not_save(tmp_path, edit, message):
    text = RECIPE.replace("target_minutes: 1.5", "target_minutes: 4\n" + edit)
    if "[3, 6]" in edit:
        text = text.replace("minutes_range: [3, 6]", "minutes_range: [3.3, 6]")
    config = _config(tmp_path, text)
    with pytest.raises(video_columns.ColumnError, match=message):
        recipes.validate("quick", text, config)


def test_a_recipe_can_say_its_research_is_optional_and_only_then_is_there_a_version_without_it(tmp_path):
    optional = RECIPE.replace("  mode: job\n", "  mode: job\n  optional: true\n")
    config = _config(tmp_path, optional)
    bare = recipes.find(config, "quick-nr")
    assert (bare.column.research_mode, bare.column.research_prompt, bare.audience, bare.no_research) == ("none", "", "kids", True)
    assert recipes.find(config, "ai-adults-quick_take-short-nr").column.research_mode == "none"
    assert recipes.find(config, "quick").research_optional and not bare.research_optional
    own, other = dataclasses.asdict(recipes.find(config, "quick").column), dataclasses.asdict(bare.column)
    assert {k for k in own if own[k] != other[k]} == {"key", "research_mode", "research_prompt"}
    assert recipes.split_variant_key("flash-m2-nr") == ("flash", 2.0, True)
    assert recipes.find(config, "quick-nr-m4") is None  # the length comes before -nr; there is no length of a column without research
    assert recipes.variant_column_key("deep_dive", 7.5, True) == "deep_dive-m7p5-nr"
    fixed = _config(tmp_path / "fixed")
    assert "调研不能关" in recipes.find(fixed, "quick-nr").error
    with pytest.raises(video_columns.ColumnError, match="research.optional"):
        recipes.validate("quick", optional.replace("optional: true", "optional: yes please"), config)
