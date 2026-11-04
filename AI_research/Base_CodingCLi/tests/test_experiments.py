"""A/B module, phase 1 (spec/ab-testing.md): registry, definitions, store, analysis engine, boundary."""
from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.experiments import analysis as an
from core.experiments import background, registry
from core.experiments.definition import Arm, Experiment, Metric, Scope, Stop, aa, conflicts
from core.experiments.store import ExperimentStore
from tests.test_content_columns import column_env  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def compare(id="cover_title", overrides=None, **extra) -> Experiment:
    return Experiment(id=id, name="封面样式", arms={"A": Arm(name="现状"), "B": Arm(name="标题封面", overrides={"cover_style": "title"} if overrides is None else overrides)},
                      metric=Metric(primary="cover_pass", better="higher", min_effect=15), stop=Stop(per_arm=73, max_days=28), **extra)


# ---- registry ------------------------------------------------------------------------------------------------------

def test_write_paths_overlap_when_equal_or_nested():
    assert registry.overlaps("render", "render.packaging")
    assert registry.overlaps("presentation.opening_card", "presentation.opening_card")
    assert not registry.overlaps("presentation.strategy", "presentation.opening_card")
    assert not registry.overlaps("render", "renderer")


def test_a_sub_path_of_a_prefix_switch_is_found_but_not_of_a_plain_one():
    assert registry.find("render.voice_id").path == "render"
    assert registry.find("render.packaging").path == "render.packaging"
    assert registry.find("greeting.extra") is None
    assert registry.find("unknown") is None


# ---- definitions ---------------------------------------------------------------------------------------------------

def test_a_compare_experiment_needs_a_registered_switch_with_an_allowed_value():
    assert compare().block_size() == 2
    with pytest.raises(ValidationError, match="不在开关登记表"):
        compare(overrides={"title_style": "x"})
    with pytest.raises(ValidationError, match="只能取"):
        compare(overrides={"cover_style": "frame"})
    with pytest.raises(ValidationError, match="至少要改一个开关"):
        compare(overrides={})
    with pytest.raises(ValidationError, match="写入位置重叠"):
        compare(overrides={"render.packaging": {}, "render.packaging.intro": False})
    with pytest.raises(ValidationError, match="具体的子项"):
        compare(overrides={"render": {}})


def test_arm_a_is_always_today_and_the_share_comes_in_steps_of_five():
    with pytest.raises(ValidationError, match="A 组就是现状"):
        Experiment(id="x1", name="x", arms={"A": Arm(name="a", overrides={"cover_style": "title"}), "B": Arm(name="b", overrides={"cover_style": "title"})},
                   metric=Metric(primary="cover_pass", better="higher", min_effect=15), stop=Stop(per_arm=10, max_days=10))
    assert compare(share_b=30).block_size() == 10
    for bad in (0, 33, 60):
        with pytest.raises(ValidationError, match="B 组比例"):
            compare(share_b=bad)


def test_metrics_must_be_known_and_point_the_natural_way():
    with pytest.raises(ValidationError, match="主要指标只能是"):
        Metric(primary="views_24h", better="higher", min_effect=1)
    with pytest.raises(ValidationError, match="方向应为 lower"):
        Metric(primary="exit3s", better="higher", min_effect=1)


def test_an_aa_check_changes_nothing_and_defaults_to_twenty_per_arm():
    check = aa("aa_first")
    assert check.kind == "aa" and check.write_paths() == [] and check.stop.per_arm == 20
    with pytest.raises(ValidationError, match="A/A 检查两组设置相同"):
        Experiment(id="aa2", name="x", kind="aa", arms={"A": Arm(name="a"), "B": Arm(name="b", overrides={"cover_style": "title"})},
                   metric=Metric(primary="completion", better="higher", min_effect=8), stop=Stop(per_arm=20, max_days=28))


def test_switch_timing_and_reach_follow_the_registry():
    notes = compare(id="n1", overrides={"notes": "先讲价格"})
    assert notes.counts_failures_before_writing() and notes.needs_column()
    assert not compare().counts_failures_before_writing() and not compare().needs_column()


def test_running_together_is_refused_only_for_overlapping_write_paths_in_overlapping_scope():
    render_all = compare(id="r1", overrides={"render.packaging.intro": False})
    packaging = compare(id="r2", overrides={"render.packaging": {"intro": False}})
    cover = compare(id="c1")
    assert conflicts(render_all, packaging)
    assert not conflicts(render_all, cover)
    apart = compare(id="r3", overrides={"render.packaging": {"intro": False}}, scope=Scope(columns=["ai_flash"]))
    other = compare(id="r4", overrides={"render.packaging.intro": False}, scope=Scope(columns=["topic_digest"]))
    assert not conflicts(apart, other)


# ---- store ---------------------------------------------------------------------------------------------------------

def test_the_master_switch_is_off_until_turned_on_and_off_when_unreadable(tmp_path):
    store = ExperimentStore(tmp_path)
    assert store.enabled() is False
    store.set_enabled(True, "admin")
    assert store.enabled() is True
    broken = ExperimentStore(tmp_path / "missing" / "deeper")
    broken.path = str(tmp_path)  # a directory, not a database
    assert broken.enabled() is False


def test_drafts_save_and_list_and_a_started_experiment_is_locked(tmp_path):
    store = ExperimentStore(tmp_path)
    store.save(compare(), "admin")
    store.save(compare(), "admin")
    [row] = store.list()
    assert row["status"] == "draft" and row["definition"]["arms"]["B"]["overrides"] == {"cover_style": "title"}
    assert [e["kind"] for e in store.events("cover_title")] == ["created", "saved"]
    with store.connect(write=True) as db:
        db.execute("UPDATE experiments SET status='running' WHERE id='cover_title'")
    with pytest.raises(ValueError, match="不能修改"):
        store.save(compare(), "admin")


def _video_db(path: Path, rows: list[tuple[str, str, dict]]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE scripts (id TEXT, created_at TEXT, content_context TEXT)")
        db.executemany("INSERT INTO scripts VALUES (?,?,?)", [(i, c, json.dumps(ctx)) for i, c, ctx in rows])


def test_reconciliation_binds_tokens_found_on_scripts_and_never_writes_video_db(tmp_path):
    store = ExperimentStore(tmp_path)
    store.save(compare(), "admin")
    now = time.time()
    with store.connect(write=True) as db:
        db.execute("UPDATE experiments SET status='running', created_at=? WHERE id='cover_title'", (now - 3600,))
        for token in ("t1", "t2", "t-orphan"):
            db.execute("INSERT INTO assignments VALUES (?,?,?,?,?,?,?,NULL)", (token, "cover_title", "B", "ai_flash", 0, now, "abc"))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))
    old = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now - 86400))
    _video_db(tmp_path / "video.db", [
        ("s1", stamp, {"experiments": [{"id": "cover_title", "arm": "B", "token": "t1"}]}),
        ("s2", stamp, {"column": "x", "experiments": [{"id": "cover_title", "arm": "A", "token": "t2"}]}),
        ("s0", old, {"experiments": [{"id": "cover_title", "arm": "B", "token": "t-orphan"}]}),  # before the start
        ("s3", stamp, {}),
    ])
    before = (tmp_path / "video.db").read_bytes()
    assert background.reconcile(store, tmp_path / "video.db") == 2
    bound = {a["token"]: a["script_id"] for a in store.assignments("cover_title")}
    assert bound == {"t1": "s1", "t2": "s2", "t-orphan": None}
    assert (tmp_path / "video.db").read_bytes() == before


def test_the_background_tick_does_nothing_while_switched_off(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(background, "reconcile", lambda *a: called.append(a))
    background.tick(tmp_path)
    assert called == []


# ---- analysis ------------------------------------------------------------------------------------------------------

def test_completion_is_adjusted_for_length_with_a_frozen_fit():
    rng = random.Random(3)
    points = [(d, 60 - 8 * __import__("math").log(d) + rng.gauss(0, 1)) for d in rng.choices(range(60, 800), k=80)]
    fit = an.fit_duration(points)
    assert fit["slope"] == pytest.approx(-8, abs=0.5)
    assert an.adjusted_completion(None, 100, fit) is None and an.adjusted_completion(30, None, fit) is None
    short, long = an.adjusted_completion(40, 60, fit), an.adjusted_completion(15, 700, fit)
    assert abs(short - (40 - (fit["slope"] * __import__("math").log(60) + fit["intercept"]))) < 1e-9
    assert an.fit_duration(points[:5]) is None


def test_low_volume_columns_join_their_matrix_and_weekly_ones_use_four_week_windows():
    start = 0.0
    day = 86400
    videos = ([{"column": "daily", "matrix": "m1", "pubdate": start + i * day / 2} for i in range(28)]  # 14 days, 2 a day
              + [{"column": "weekly", "matrix": "m2", "pubdate": start + i * 7 * day} for i in range(2)]
              + [{"column": "rare", "matrix": "", "pubdate": start + 3 * day}]
              + [{"column": "", "matrix": "", "pubdate": start + 9 * day}])
    keys = an.stratum_keys(videos, start)
    assert keys[0] == "daily|w0" and keys[27] == "daily|w1"
    assert keys[28] == keys[29] == "m2|m0"
    assert keys[30] == keys[31] == f"{an.LOW_VOLUME}|m0"


def test_strata_missing_an_arm_are_skipped_and_reported():
    rows = [{"arm": "A", "stratum": "s1", "value": 10}, {"arm": "B", "stratum": "s1", "value": 20},
            {"arm": "A", "stratum": "only_a", "value": 99}, {"arm": "B", "stratum": "s1", "value": None}]
    result = an.estimate(rows, resamples=200)
    assert result["skipped_strata"] == ["only_a"] and result["n_a"] == 1 and result["n_b"] == 1
    assert result["estimate"] == pytest.approx(10)


def _simulate(rng, n, shift=0.0):
    rows = []
    for i in range(2 * n):
        stratum = f"c{(i // 2) % 6}"
        arm = "A" if i % 2 == 0 else "B"
        level = {"c0": 10, "c1": 25, "c2": 40, "c3": 15, "c4": 30, "c5": 20}[stratum]
        rows.append({"arm": arm, "stratum": stratum, "value": level + rng.gauss(0, 15) + (shift if arm == "B" else 0)})
    return rows


def test_aa_rarely_calls_b_better_and_a_real_fifteen_point_gain_is_found():
    rng = random.Random(7)
    runs = 150
    false_better = sum(an.estimate(_simulate(rng, 45), resamples=400, seed=k)["low"] > 0 for k in range(runs))
    assert false_better / runs <= 0.05
    found = sum(an.estimate(_simulate(rng, 45, 15), resamples=400, seed=k)["low"] > 0 for k in range(60))
    assert found / 60 >= 0.8


def test_ratio_of_medians_for_play_counts():
    rows = [{"arm": "A", "stratum": "s", "value": v} for v in (10, 20, 30)] + [{"arm": "B", "stratum": "s", "value": v} for v in (5, 10, 15)]
    assert an.estimate(rows, "ratio", resamples=200)["estimate"] == pytest.approx(-0.5)


def test_srm_uses_the_exact_binomial():
    assert an.srm_p(50, 50, 50) == pytest.approx(1.0)
    assert an.srm_p(70, 30, 50) < 0.01
    assert an.srm_p(70, 30, 30) > 0.5


def test_verdict_follows_the_rules_in_order_and_respects_lower_is_better():
    good = {"estimate": 6, "low": 2, "high": 10, "sd": 15, "n_a": 45, "n_b": 45}
    assert an.verdict(good, "higher", ended=False, srm=1)["verdict"] == "running"
    assert an.verdict(good, "higher", ended=True, srm=0.001)["verdict"] == "srm"
    assert an.verdict(good, "higher", ended=True, srm=1)["verdict"] == "b_better"
    assert an.verdict(good, "lower", ended=True, srm=1)["verdict"] == "b_worse"  # exits went up
    vetoed = an.verdict(good, "higher", ended=True, srm=1, guardrails=[{"metric": "exit3s", "status": "worse"}])
    assert vetoed["verdict"] == "no_difference" and vetoed["minimum_detectable"] == pytest.approx(an.Z_MDE * 15 * (2 / 45) ** 0.5)
    noted = an.verdict(good, "higher", ended=True, srm=1, applied_rate=0.5, guardrails=[{"metric": "views_24h", "status": "hint"}])
    assert noted["verdict"] == "b_better" and len(noted["notes"]) == 2


def test_guardrails_veto_only_when_the_whole_interval_is_past_the_threshold():
    exits_up = {"estimate": 12, "low": 7, "high": 17}  # exit3s, lower is better
    assert an.guardrail_status(exits_up, "lower", 5) == "worse"
    assert an.guardrail_status({"estimate": 8, "low": -2, "high": 18}, "lower", 5) == "hint"
    assert an.guardrail_status({"estimate": -0.3, "low": -0.5, "high": -0.25}, "higher", 0.2) == "worse"
    assert an.guardrail_status({"estimate": 0.0, "low": -0.3, "high": 0.3}, "higher", 0.2) == "ok"


def test_harm_hint_waits_for_fifteen_each_and_only_warns():
    small = [10.0] * 10
    assert an.harm_hint(small, small, 0, 0) is None
    assert "不到 A 组一半" in an.harm_hint([20.0] * 15, [5.0] * 15, 0, 0)
    assert "缺失率" in an.harm_hint([10.0] * 15, [10.0] * 15, 0.1, 0.35)


# ---- API and boundary ----------------------------------------------------------------------------------------------

def _login(client, username="admin"):
    client.post("/api/auth/login", json={"username": username, "password": f"{username}-pass-123"})


def test_the_tab_api_is_read_only_and_for_administrators(client, web_env):
    _login(client, "editor")
    assert client.get("/api/experiments").status_code == 403
    _login(client)
    body = client.get("/api/experiments").json()
    assert body["enabled"] is False and body["experiments"] == []
    assert any(r["path"] == "render.*" for r in body["registry"])
    assert client.get("/api/experiments/nope").status_code == 404
    tmp, _ = web_env
    ExperimentStore(tmp / "state").save(aa("aa_first"), "admin")
    detail = client.get("/api/experiments/aa_first").json()
    assert detail["status"] == "draft" and detail["arms"] == {"A": 0, "B": 0} and detail["events"][0]["kind"] == "created"
    assert client.post("/api/experiments", json={}).status_code == 403  # no CSRF token


#: Existing files allowed to reference the A/B module (spec §2 rule 1).
ALLOWED = {"web/server.py", "web/services/video_workflow.py", "web/services/automation_health.py",
           "web/services/weekly_review.py"}
_OWN = ("core/experiments/", "web/routes/experiments.py", "tests/")


def test_existing_code_references_the_module_only_from_the_allowlist():
    pattern = re.compile(r"core\.experiments|core import experiments|web\.routes\.experiments|routes import experiments")
    found = set()
    for folder in ("core", "web", "utils", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(_OWN):
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                found.add(rel)
    assert found <= ALLOWED, f"A/B 模块被白名单以外的文件引用：{sorted(found - ALLOWED)}"


def test_a_broken_module_leaves_the_app_up(monkeypatch, web_env):
    import builtins
    real = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("web.routes.experiments"):
            raise ImportError("broken on purpose")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    from web.server import create_app
    from fastapi.testclient import TestClient
    client = TestClient(create_app())
    assert client.get("/api/live").status_code == 200


# ---- phase 2: the creation hook -----------------------------------------------------------------------------------

from core.experiments import hook  # noqa: E402


def _running(store: ExperimentStore, experiment: Experiment) -> None:
    store.save(experiment, "admin")
    store.move(experiment.id, "start", "admin")


def test_the_hook_changes_nothing_when_off_manual_or_idle(tmp_path):
    store = ExperimentStore(tmp_path)
    context = {"column": "ai_flash", "notes": "x"}
    assert hook.arm_for(tmp_path, context, column_key="flash", created_by="autopilot") is context  # switch off
    store.set_enabled(True, "admin")
    assert hook.arm_for(tmp_path, context, column_key="flash", created_by="autopilot") is context  # nothing running
    _running(store, aa("aa_first"))
    assert hook.arm_for(tmp_path, context, column_key="flash", created_by="admin") is context  # manual
    assert store.assignments("aa_first") == []


def test_blocks_of_two_hold_one_of_each_arm_per_column_and_repeat_the_same_way(tmp_path):
    store = ExperimentStore(tmp_path)
    store.set_enabled(True, "admin")
    _running(store, aa("aa_first"))
    arms = [hook.arm_for(tmp_path, {"column": "c1"}, column_key="flash", created_by="autopilot")["experiments"][0]["arm"]
            for _ in range(10)]
    other = [hook.arm_for(tmp_path, {}, column_key="topic_digest", created_by="autopilot")["experiments"][0]["arm"]
             for _ in range(4)]
    assert all(sorted(arms[i:i + 2]) == ["A", "B"] for i in range(0, 10, 2))
    assert sorted(other) == ["A", "A", "B", "B"]
    rows = store.assignments("aa_first")
    assert {r["column_key"] for r in rows} == {"c1", "topic_digest"} and all(r["code_version"] for r in rows)
    again = ExperimentStore(tmp_path / "again")
    again.set_enabled(True, "admin")
    _running(again, aa("aa_first"))
    assert [hook.arm_for(tmp_path / "again", {"column": "c1"}, column_key="flash", created_by="autopilot")["experiments"][0]["arm"]
            for _ in range(10)] == arms


def test_arm_b_writes_only_its_own_field_and_arm_a_gains_only_the_mark(tmp_path):
    store = ExperimentStore(tmp_path)
    store.set_enabled(True, "admin")
    experiment = compare(id="card", overrides={"presentation.opening_card": True})
    store.save(experiment, "admin")
    store.move("card", "start", "admin")
    before = {"column": "c1", "presentation": {"strategy": "先讲数字"}}
    results = [hook.arm_for(tmp_path, before, column_key="flash", created_by="autopilot") for _ in range(2)]
    by_arm = {r["experiments"][0]["arm"]: r for r in results}
    assert by_arm["B"]["presentation"] == {"strategy": "先讲数字", "opening_card": True}
    assert {k: v for k, v in by_arm["A"].items() if k != "experiments"} == before
    assert before == {"column": "c1", "presentation": {"strategy": "先讲数字"}}  # the caller's dict is untouched


def test_scope_and_reach_decide_who_takes_part(tmp_path):
    store = ExperimentStore(tmp_path)
    store.set_enabled(True, "admin")
    _running(store, compare(id="greet", overrides={"greeting": "你好"}))  # needs a content column
    _running(store, aa("aa_flash", scope=Scope(columns=["ai_flash"], content_mode="individual")))
    no_column = hook.arm_for(tmp_path, {"content_mode": "individual"}, column_key="topic_digest", created_by="autopilot")
    assert no_column == {"content_mode": "individual"}
    flash = hook.arm_for(tmp_path, {"column": "ai_flash", "content_mode": "individual"}, column_key="flash", created_by="autopilot")
    assert {m["id"] for m in flash["experiments"]} == {"greet", "aa_flash"}
    episode = hook.arm_for(tmp_path, {"column": "ai_flash", "content_mode": "episode"}, column_key="flash", created_by="autopilot")
    assert [m["id"] for m in episode["experiments"]] == ["greet"]


def test_status_moves_follow_the_allowed_paths(tmp_path):
    store = ExperimentStore(tmp_path)
    store.save(aa("aa_first"), "admin")
    with pytest.raises(ValueError):
        store.move("aa_first", "pause", "admin")
    assert store.move("aa_first", "start", "admin")["started_at"]
    assert store.move("aa_first", "pause", "admin")["status"] == "paused"
    assert store.move("aa_first", "resume", "admin")["status"] == "running"
    assert store.move("aa_first", "end", "admin")["status"] == "ended"
    assert [e["kind"] for e in store.events("aa_first")] == ["created", "start", "pause", "resume", "end"]


@pytest.fixture
def workflow_env(column_env, monkeypatch):
    from web.services import video_workflow
    monkeypatch.setattr(video_workflow, "spawn", lambda sid, by: None)
    return column_env


def _start(by="autopilot"):
    from core.video import store as video_store
    from web.services import video_workflow
    script = video_workflow.start("flash", {"title": "测试模型更新", "_production": "flash"}, by)
    no = video_store.add_version(script["id"], title="测试", sections=[], length_check={}, edit_type="manual")
    return video_store.get_script(script["id"]), video_store.get_version(script["id"], no)


def test_with_an_experiment_running_arm_a_matches_a_script_made_without_one(workflow_env):
    from web.services import video_list_payload
    plain, plain_version = _start()
    store = ExperimentStore(workflow_env / "state")
    store.set_enabled(True, "admin")
    _running(store, aa("aa_first"))
    scripts = [_start() for _ in range(2)]
    a_script, a_version = next(s for s in scripts if s[0]["content_context"]["experiments"][0]["arm"] == "A")
    strip = lambda ctx: {k: v for k, v in ctx.items() if k != "experiments"}  # noqa: E731
    assert strip(a_script["content_context"]) == plain["content_context"]
    assert strip(a_version["meta"]["content_context"]) == plain_version["meta"]["content_context"]
    listed = video_list_payload.compact(a_script)["content_context"]
    assert strip(listed) == video_list_payload.compact(plain)["content_context"]
    assert listed["experiments"][0]["id"] == "aa_first"


def test_a_failing_hook_leaves_script_creation_as_today(workflow_env, monkeypatch):
    plain, _ = _start()
    store = ExperimentStore(workflow_env / "state")
    store.set_enabled(True, "admin")
    _running(store, aa("aa_first"))

    def broken(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(hook, "arm_for", broken)
    script, _ = _start()
    assert script["content_context"] == plain["content_context"]


def test_an_empty_context_with_only_a_mark_is_not_copied_into_versions(tmp_path, monkeypatch):
    from core.video import store as video_store
    monkeypatch.setattr(video_store, "_DB_PATH_OVERRIDE", str(tmp_path / "video.db"))
    video_store.init_db() if hasattr(video_store, "init_db") else None
    script = video_store.create_script("flash", {"title": "x"}, content_context={"experiments": [{"id": "e", "arm": "A", "token": "t"}]})
    no = video_store.add_version(script["id"], title="x", sections=[], length_check={}, edit_type="manual")
    assert "content_context" not in (video_store.get_version(script["id"], no)["meta"] or {})


def test_start_is_refused_for_undeveloped_switches_previews_and_conflicts(client, web_env):
    _login(client)
    csrf = {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}
    made = client.post("/api/experiments", json=compare().model_dump(), headers=csrf)
    assert made.status_code == 201
    refused = client.post("/api/experiments/cover_title/start", headers=csrf)
    assert refused.status_code == 409 and "预览" in refused.json()["error"]["message"]
    planned = compare(id="later", overrides={"greeting_position": "after"}).model_dump()
    assert client.post("/api/experiments", json=planned, headers=csrf).status_code == 201
    waiting = client.post("/api/experiments/later/start", headers=csrf)
    assert waiting.status_code == 409 and "等待开发" in waiting.json()["error"]["message"]
    assert client.post("/api/experiments", json={"id": "bad"}, headers=csrf).status_code == 422
    assert client.post("/api/experiments", json=aa("aa_first").model_dump(), headers=csrf).status_code == 201
    assert client.post("/api/experiments/aa_first/start", headers=csrf).status_code == 200
    assert client.post("/api/experiments/aa_first/start", headers=csrf).status_code == 409
    assert client.put("/api/experiments/settings", json={"enabled": True}, headers=csrf).json() == {"enabled": True}
    assert client.get("/api/experiments").json()["enabled"] is True


# ---- phase 2.5: preview B and reminders ---------------------------------------------------------------------------

from core.experiments import notices, preview  # noqa: E402


def _approved(column_env):
    from core.video import store as video_store
    context = content_columns_snapshot(column_env)
    script = video_store.create_script("flash", {"title": "转换周报"}, production="flash", content_context=context,
                                       created_by="autopilot")
    version = video_store.add_version(script["id"], title="转换周报", sections=[{"title": "执行转换", "paragraphs": [
        {"text": "运行转换命令，查看生成的文件。", "citations": [], "evidence": "UNCITED"}]}], length_check={}, edit_type="GENERATED")
    video_store.update_script(script["id"], status="APPROVED", approved_version=version)
    return video_store.get_script(script["id"])


def content_columns_snapshot(column_env):
    from core.video import content_columns
    return content_columns.snapshot(str(column_env / "config"), "flash", "monitoring/ai_flash_discovery")


def test_preview_builds_b_from_the_real_request_builder_without_a_render_row(column_env):
    from core.video import store as video_store
    script = _approved(column_env)
    experiment = compare(id="card", overrides={"render.packaging.label": "要点版"})
    built = preview.build_request(preview.b_script(script, experiment))
    assert built["request"]["production_context"]["matrix"]["settings"]["packaging"]["label"] == "要点版"
    assert built["script_id"] == script["id"] and built["source_text"]
    assert video_store.list_renders(script["id"]) == []
    assert video_store.get_script(script["id"])["content_context"] == script["content_context"]  # the source is untouched


class FakeAutoVideo:
    def __init__(self):
        self.payloads, self.statuses = [], ["QUEUED", "RUNNING", "COMPLETED"]

    def create_job(self, payload):
        self.payloads.append(payload)
        return {"job_id": "job-1"}

    def get_job(self, job_id):
        return {"status": self.statuses.pop(0)}

    def list_artifacts(self, job_id):
        return [{"name": "video.mp4"}, {"name": "captions.srt"}]

    def download(self, item, dest):
        Path(dest).write_bytes(b"mp4")

    def session(self):
        from contextlib import nullcontext
        return nullcontext()


def test_a_render_preview_submits_once_and_keeps_only_the_video(tmp_path, monkeypatch):
    from web.services import video_visuals
    monkeypatch.setattr(video_visuals, "prepare", lambda render, client: ({**render["request"], "visual": 1}, {}))
    client = FakeAutoVideo()
    built = {"script_id": "s1", "request": {"input_mode": "narration", "chapters": []}, "visuals": {}, "source_text": "x"}
    files = preview.render(tmp_path, "card-1", built, client, sleep=lambda _s: None)
    assert files == ["video.mp4"] and (preview.folder(tmp_path, "card-1") / "video.mp4").read_bytes() == b"mp4"
    [payload] = client.payloads
    assert payload["idempotency_key"] == "experiment-preview:card-1" and payload["visual"] == 1


def _queue(tmp_path, monkeypatch, experiment):
    store = ExperimentStore(tmp_path)
    store.save(experiment, "admin")
    monkeypatch.setattr(preview, "sources", lambda *a, **k: [{"render_id": "r1", "script_id": "s1", "title": "t", "completed_at": ""}])
    return store, preview.request(store, tmp_path, experiment.id, "admin")


def test_previews_wait_for_idle_production_then_run_and_can_be_confirmed(tmp_path, monkeypatch):
    store, queued = _queue(tmp_path, monkeypatch, compare(id="card", overrides={"render.packaging.label": "x"}))
    assert queued["status"] == "requested"
    with pytest.raises(ValueError, match="已有一个预览"):
        preview.request(store, tmp_path, "card", "admin")
    with pytest.raises(ValueError, match="还没有做好的预览"):
        preview.confirm(store, "card", "admin")
    monkeypatch.setattr(preview, "production_idle", lambda _s: False)
    assert preview.run_next(tmp_path) is None
    monkeypatch.setattr(preview, "production_idle", lambda _s: True)
    from core.video import store as video_store
    monkeypatch.setattr(video_store, "get_script", lambda _id: {"id": "s1", "content_context": {}})
    monkeypatch.setattr(preview, "build_request", lambda script: {"script_id": "s1", "request": {}, "visuals": {}, "source_text": ""})
    monkeypatch.setattr(preview, "render", lambda *a, **k: ["video.mp4"])
    assert preview.run_next(tmp_path, client=FakeAutoVideo()) == queued["id"]
    assert preview.latest(store, "card")["status"] == "done"
    preview.confirm(store, "card", "admin")
    assert store.preview_confirmed("card")


def test_a_failed_preview_is_recorded_and_reminded(tmp_path, monkeypatch):
    store, queued = _queue(tmp_path, monkeypatch, compare(id="card", overrides={"render.packaging.label": "x"}))
    monkeypatch.setattr(preview, "production_idle", lambda _s: True)
    from core.video import store as video_store
    monkeypatch.setattr(video_store, "get_script", lambda _id: None)
    preview.run_next(tmp_path, client=FakeAutoVideo())
    shown = preview.latest(store, "card")
    assert shown["status"] == "failed" and "找不到" in shown["error"]
    [reminder] = notices.recent(tmp_path, time.time(), 7)
    assert "没出成" in reminder["title"] and reminder["link"] == "/analytics?tab=experiments"


def test_writing_only_experiments_need_no_preview(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="不需要预览"):
        _queue(tmp_path, monkeypatch, compare(id="greet", overrides={"greeting": "你好"}))


def test_reminders_cover_starts_and_survive_a_broken_module(tmp_path, monkeypatch, web_env):
    store = ExperimentStore(tmp_path)
    store.save(aa("aa_first"), "admin")
    store.move("aa_first", "start", "admin")
    assert [r["title"] for r in notices.recent(tmp_path, time.time(), 7)] == ["实验「A/A 检查」开始了"]
    from web.services import automation_health
    monkeypatch.setattr(notices, "recent", lambda *a: (_ for _ in ()).throw(RuntimeError("broken")))
    assert automation_health.experiment_notices(time.time()) == []


# ---- phase 3: results pipeline, cover_style, weekly review ---------------------------------------------------------

from core.experiments import progress, results  # noqa: E402


def _results_world(tmp_path, n=6, start=None):
    """A video.db and platform_analytics.db with ``n`` scripts per arm, all published and read after 48 hours."""
    start = start or time.time() - 20 * 86400
    store = ExperimentStore(tmp_path)
    experiment = aa("aa_first", per_arm=n)
    store.save(experiment, "admin")
    store.move("aa_first", "start", "admin")
    with store.connect(write=True) as db:
        db.execute("UPDATE experiments SET started_at=? WHERE id='aa_first'", (start,))
    vdb, adb = sqlite3.connect(tmp_path / "video.db"), sqlite3.connect(tmp_path / "platform_analytics.db")
    vdb.executescript("CREATE TABLE scripts (id TEXT, status TEXT, archived INT, content_context TEXT, created_at TEXT);"
                      "CREATE TABLE script_versions (script_id TEXT, version INT);"
                      "CREATE TABLE publications (script_id TEXT, platform TEXT, url TEXT, removed_at TEXT, created_at TEXT);"
                      "CREATE TABLE renders (id TEXT, script_id TEXT, status TEXT);")
    adb.executescript("CREATE TABLE analytics_videos (bvid TEXT, pubdate REAL, duration INT, state INT);"
                      "CREATE TABLE analytics_watch (bvid TEXT, day TEXT, data TEXT, collected_at REAL);"
                      "CREATE TABLE analytics_snapshots (bvid TEXT, observed_at REAL, metrics TEXT);")
    rng = random.Random(5)
    for i in range(2 * n):
        sid, arm, bvid = f"s{i}", "A" if i % 2 == 0 else "B", f"BV1{i:09d}"
        pub = start + 86400 + i * 3600
        with store.connect(write=True) as db:
            db.execute("INSERT INTO assignments VALUES (?,?,?,?,?,?,?,?)", (f"t{i}", "aa_first", arm, "ai_flash", i // 2, start, "v", sid))
        vdb.execute("INSERT INTO scripts VALUES (?,?,?,?,?)", (sid, "PUBLISHED", 0, json.dumps({"column": "ai_flash"}), ""))
        vdb.execute("INSERT INTO script_versions VALUES (?,1)", (sid,))
        vdb.execute("INSERT INTO publications VALUES (?,?,?,?,?)", (sid, "bilibili", f"https://b23/{bvid}", None, str(i)))
        adb.execute("INSERT INTO analytics_videos VALUES (?,?,?,1)", (bvid, pub, 120 + 10 * i))
        adb.execute("INSERT INTO analytics_watch VALUES (?,?,?,?)", (bvid, "d0", json.dumps({"completion": 99, "avg_seconds": 1}), pub + 3600))
        adb.execute("INSERT INTO analytics_watch VALUES (?,?,?,?)", (bvid, "d2", json.dumps(
            {"completion": 30 + rng.gauss(0, 5), "avg_seconds": 50, "exit3s": 30, "cover_pass": 60}), pub + 50 * 3600))
        for h in (0, 24):
            adb.execute("INSERT INTO analytics_snapshots VALUES (?,?,?)", (bvid, pub + h * 3600, json.dumps({"view": 10 + h})))
    for k in range(12):  # history before the start, for the length fit
        bvid = f"BVh{k:09d}"
        adb.execute("INSERT INTO analytics_videos VALUES (?,?,?,1)", (bvid, start - 86400 * (k + 1), 60 + 60 * k))
        adb.execute("INSERT INTO analytics_watch VALUES (?,?,?,?)", (bvid, "h", json.dumps({"completion": 50 - 3 * k, "avg_seconds": 40}), start))
    vdb.commit(); adb.commit(); vdb.close(); adb.close()
    return store


def test_results_read_after_48_hours_from_the_per_video_endpoint_only(tmp_path):
    store = _results_world(tmp_path)
    out = results.evaluate(store, "aa_first", tmp_path)
    assert out["n"] == {"A": 6, "B": 6} and out["with_data"] == {"A": 6, "B": 6}
    first = out["videos"][0]
    assert first["completion"] != 99 and first["views_24h"] == 34  # the 1-hour reading is too early
    assert out["fit"]["n"] == 12 and out["primary"]["metric"] == "completion_adjusted"
    assert out["endpoint_reached"] and out["verdict"]["verdict"] == "running"  # not ended yet


def test_the_timer_ends_at_the_end_point_then_freezes_the_result_after_the_wind_down(tmp_path):
    store = _results_world(tmp_path)
    results.evaluate_all(tmp_path)
    assert store.get("aa_first")["status"] == "ended"
    assert "endpoint" in [e["kind"] for e in store.events("aa_first")] and store.result("aa_first") is None
    results.evaluate_all(tmp_path, now=time.time() + 8 * 86400)
    frozen = store.result("aa_first")
    assert frozen["verdict"]["verdict"] in ("no_difference", "b_better", "b_worse")
    assert [e["kind"] for e in store.events("aa_first")][-1] == "verdict"
    results.evaluate_all(tmp_path, now=time.time() + 9 * 86400)
    assert store.result("aa_first")["computed_at"] == frozen["computed_at"]  # never rewritten


def test_scripts_that_failed_before_writing_leave_the_denominator_for_writing_switches(tmp_path):
    store = _results_world(tmp_path)
    with sqlite3.connect(tmp_path / "video.db") as db:
        db.execute("DELETE FROM script_versions WHERE script_id='s0'")
        db.execute("UPDATE publications SET removed_at='x' WHERE script_id='s3'")
    out = results.evaluate(store, "aa_first", tmp_path)
    assert out["n"] == {"A": 5, "B": 6} and out["missing"]["B"] == 1


def test_paused_and_switched_off_time_is_not_counted():
    events = [{"kind": "pause", "at": 100.0}, {"kind": "resume", "at": 300.0}]
    master = [{"kind": "master_off", "at": 500.0}]
    assert results.paused_seconds(events, master, 0.0, 1000.0) == 200 + 500


def test_cover_style_title_skips_the_cover_copy_model(monkeypatch, tmp_path):
    from core.video import store as video_store
    from web.services import publish_cover
    monkeypatch.setattr(video_store, "get_script", lambda sid: {"content_context": {"cover_style": "title"}} if sid == "s1" else {})
    assert publish_cover.cover_style({"script_id": "s1"}) == "title"
    assert publish_cover.cover_style({"script_id": "s2"}) == "number"
    monkeypatch.setattr(video_store, "get_script", lambda sid: (_ for _ in ()).throw(RuntimeError("db")))
    assert publish_cover.cover_style({"script_id": "s1"}) == "number"


def test_cover_style_applied_is_read_from_the_copy_cache(tmp_path):
    folder = tmp_path / "platforms" / "covers"
    folder.mkdir(parents=True)
    with sqlite3.connect(tmp_path / "video.db") as db:
        db.execute("CREATE TABLE renders (id TEXT, script_id TEXT)")
        db.executemany("INSERT INTO renders VALUES (?,?)", [("r1", "s1"), ("r2", "s2")])
    (folder / "r1-abc-copy.json").write_text(json.dumps({"copy": {"big": "差十倍"}}))
    (folder / "r2-abc-copy.json").write_text(json.dumps({"copy": None}))
    title = compare(overrides={"cover_style": "title"})
    assert results.applied(title, "s1", {}, tmp_path) is False and results.applied(title, "s2", {}, tmp_path) is True
    assert results.applied(compare(id="g1", overrides={"greeting": "x"}), "s1", {}, tmp_path) is None


def test_weekly_progress_lists_running_and_just_ended_experiments(tmp_path):
    store = ExperimentStore(tmp_path)
    store.save(aa("aa_first"), "admin")
    store.move("aa_first", "start", "admin")
    now = time.time()
    [row] = progress.week(tmp_path, now - 86400, now + 86400)
    assert row["status"] == "running" and row["verdict"] == "进行中"
    assert progress.week(tmp_path, now + 86400, now + 8 * 86400)[0]["id"] == "aa_first"  # still running next week
    assert progress.week(tmp_path, now - 30 * 86400, now - 20 * 86400) == []
    assert progress.week(tmp_path / "none", 0, now) == []


def test_review_marks_experiment_videos():
    from core.telemetry import weekly_review
    item = weekly_review._measure({"bvid": "BV1", "pubdate": 0, "experiments": [{"id": "e", "arm": "B", "token": "t"}]}, 1)
    assert item["experiments"] == [{"id": "e", "arm": "B"}]
    assert weekly_review._measure({"bvid": "BV2", "pubdate": 0}, 1)["experiments"] == []


def test_cover_previews_draw_both_styles(tmp_path, monkeypatch):
    from core.social import cover_copy
    from web.services import video_render
    import core.social.cover as cover_module
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(video_render, "safe_file", lambda render, name: video)
    monkeypatch.setattr(cover_copy, "for_render", lambda render, title, cache: {"big": "差十倍", "figure": "", "topic": ""})
    drawn = []

    def fake_make(video, title, dests, ffmpeg, copy):
        drawn.append(copy)
        for path in dests.values():
            Path(path).write_bytes(b"jpg")
        return dests

    monkeypatch.setattr(cover_module, "make_covers", fake_make)
    files = preview.covers(tmp_path, "p1", {"id": "r1", "title": "t"}, "t", "title")
    assert files == ["cover-a.jpg", "cover-b.jpg"] and drawn[0]["big"] == "差十倍" and drawn[1] is None
