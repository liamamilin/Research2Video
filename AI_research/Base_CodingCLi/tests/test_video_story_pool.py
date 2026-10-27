"""Event selection is tested before authors/renderers and never touches real accounts."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace

import pytest

from core.video import content_guard as guard, store, story_pool as pool
from core.video import autopilot_store as ap, columns
from tests.test_video_content_guard import guard_env, script, sections, SNAKE, NEW  # noqa: F401
from tests.test_video_scripts import video_env, llm, FakeLLM, script_json  # noqa: F401
from web.services import video_autopilot as auto, video_workflow as workflow
from web.settings import get_settings


def event(**overrides):
    return {"title": "小蛇滚动逃跑", "subject": "小蛇滚动逃跑研究", "action": "discovery",
            "edition": "", "event_date": "2026-10-23", "summary": "研究展示小蛇滚动逃跑的行为。",
            "primary": ["S1"], "background": [], "same_as": [], "changes": [], **overrides}


def organized(monkeypatch, rows):
    calls = []

    def organize(report, packets, previous, config):
        calls.append((report, packets, previous))
        return pool.validate(rows, packets, previous), {"requests": 1}

    monkeypatch.setattr(pool, "organize", organize)
    return calls


def create_pool(monkeypatch, rows=None, peers=None, material=None):
    organized(monkeypatch, rows if rows is not None else [event()])
    text = material or f"### 小蛇\n滚动逃跑。{SNAKE}\n\n---\n\n### 恐龙\n新发现。{NEW}"
    return pool.load_or_create("matrix:science", text, "", peers or [], None)


def fresh(root):
    row = script(root, "science/daily", ready=False)
    topic = {**row["topic"], "matrix_dedup": {"job": "science/daily", "policy": pool.POLICY}}
    store.update_script(row["id"], topic=topic)
    return store.get_script(row["id"])


def history(root):
    return guard.history(str(root / "config"), str(root / "jobs"))


def claim_state(script_id):
    with store.connect() as conn:
        row = conn.execute("SELECT state FROM video_story_claims WHERE script_id=?", (script_id,)).fetchone()
    return row["state"] if row else None


def test_report_pool_is_cached_across_configs_and_restart(guard_env, monkeypatch):
    calls = organized(monkeypatch, [event()])
    one = pool.load_or_create("matrix:science", f"### 小蛇\n{SNAKE}", "", [], None)
    store.init_db()  # schema initialization/restart must not erase the pool
    two = pool.load_or_create("matrix:science", f"### 小蛇\n{SNAKE}", "", [{"script_id": "x"}], object())
    assert one == two and len(calls) == 1


def test_report_footnotes_keep_article_context_instead_of_just_bibliography():
    packets = pool.packets(f"### 小蛇\n研究观察到滚动逃跑。[1]\n\n### 来源清单\n1. 论文 {SNAKE}")
    assert len(packets) == 1 and packets[0]["links"] == [SNAKE]
    assert "观察到滚动逃跑" in packets[0]["text"] and SNAKE in packets[0]["text"]


def test_missing_urls_keep_full_material_and_can_match_an_explicit_event_alias():
    text = "### 小蛇研究\n旧研究只是换了一个说法，没有新结果。"
    packets = pool.packets(text)
    peers = [{"script_id": "old", "title": "旧研究", "summary": "小蛇滚动逃跑", "sources": set()}]
    selected = pool.validate([event(same_as=["H1"])], packets, pool.context(peers))[0]
    assert packets[0]["text"] == text and not selected["primary_sources"]
    assert pool.duplicate(selected, peers) == peers


@pytest.mark.parametrize("override", [
    {"primary": ["invented"]}, {"same_as": ["invented"]}, {"event_date": "tomorrow"},
    {"action": "invented"}, {"primary": []}, {"changes": "new"},
])
def test_organizer_cannot_invent_source_or_history_ids(override):
    packets = pool.packets(f"### 小蛇\n{SNAKE}")
    with pytest.raises(pool.SelectionError):
        pool.validate([event(**override)], packets, [])


def test_report_date_and_headline_do_not_change_event_identity():
    packets = pool.packets(f"### 小蛇\n{SNAKE}")
    first = pool.validate([event()], packets, [])[0]
    renamed = pool.validate([event(title="今天重磅！", summary="另一种说法")], packets, [])[0]
    assert first["event_key"] == renamed["event_key"]
    assert pool.changes([{"dimension": "标题", "before": "旧标题", "after": "新标题"}]) == []
    assert pool.changes([{"dimension": "stars", "before": "20", "after": "50"}]) == []


def test_optional_null_metadata_is_unknown_and_missing_before_value_is_not_a_change():
    packets = pool.packets(f"### 发布\n{SNAKE}")
    selected = pool.validate([event(edition=None, event_date=None, background=None, same_as=None, changes=None)], packets, [])[0]
    assert selected["edition"] == selected["event_date"] == ""
    assert selected["changes"] == selected["background"] == selected["previous"] == []
    assert pool.changes([{"dimension": "实验结果", "before": None, "after": "新结果"}]) == []


def test_organizer_model_transport_uses_only_supplied_ids(monkeypatch):
    calls = []

    class Client:
        usage = {"requests": 1}

        def __init__(self, config):
            pass

        def chat(self, messages, **kwargs):
            calls.append(messages)
            return SimpleNamespace(content=json.dumps({"events": [event()]}))

    monkeypatch.setattr("core.llm.LLMClient", Client)
    packets = pool.packets(f"### 小蛇\n{SNAKE}")
    events, usage = pool.organize("给定研究材料", packets, [], object())
    assert usage["requests"] == 1 and events[0]["primary_sources"] == [SNAKE]
    assert json.loads(calls[0][1]["content"])["materials"][0]["id"] == "S1"


@pytest.mark.parametrize("dimension,before,after", [
    ("模型版本", "v1", "v2"), ("政策规则", "原规则", "新生效规则"), ("实验结果", "旧结果", "新结果"),
])
def test_explicit_new_developments_survive_reused_primary_source(dimension, before, after):
    packets = pool.packets(f"### 小蛇\n{SNAKE}")
    old = pool.validate([event()], packets, [])[0]
    peers = [{"script_id": "old", "title": old["title"], "story": old, "sources": {guard.source_key(SNAKE)}}]
    new = pool.validate([event(same_as=["H1"], changes=[{"dimension": dimension, "before": before, "after": after}])],
                        packets, pool.context(peers))[0]
    assert not pool.duplicate(new, peers)


def test_decimal_values_and_version_punctuation_remain_meaningful():
    assert pool.changes([{"dimension": "价格", "before": "1.00", "after": "100"}])
    packets = pool.packets(f"### 发布\n{SNAKE}")
    old = pool.validate([event(edition="1.0")], packets, [])[0]
    new = pool.validate([event(edition="10")], packets, [])[0]
    assert not pool._same_event(old, new)


@pytest.mark.parametrize("dimension", ["Hugging Face downloads", "GitHub stars", "HF 点赞数", "GitHub star count"])
def test_popularity_counters_with_platform_prefix_do_not_create_a_new_event(dimension):
    packets = pool.packets(f"### 发布\n{SNAKE}")
    old = pool.validate([event()], packets, [])[0]
    peers = [{"script_id": "old", "title": "已有发布", "story": old, "sources": {guard.source_key(SNAKE)}}]
    new = pool.validate([event(same_as=["H1"], changes=[{"dimension": dimension, "before": "8000", "after": "9000"}])],
                        packets, pool.context(peers))[0]
    assert not new["changes"] and pool.duplicate(new, peers)


def test_explicit_new_release_edition_is_new_even_without_redundant_change_text():
    packets = pool.packets(f"### 发布\n{SNAKE}")
    old = pool.validate([event(action="release", edition="1.0")], packets, [])[0]
    peers = [{"script_id": "old", "title": "已有发布", "story": old, "sources": {guard.source_key(SNAKE)}}]
    new = pool.validate([event(action="release", edition="2.0", same_as=["H1"])], packets, pool.context(peers))[0]
    assert new["changes"] == [{"dimension": "edition", "before": "1.0", "after": "2.0"}]
    assert not pool.duplicate(new, peers)
    equivalent = pool.validate([event(action="release", edition="v1.0", same_as=["H1"])], packets, pool.context(peers))[0]
    assert not equivalent["changes"] and pool.duplicate(equivalent, peers)


def test_dated_release_remains_one_event_despite_different_change_descriptions():
    packets = pool.packets(f"### 发布\n{SNAKE}")
    first = pool.validate([event(action="release", edition="2.0", changes=[
        {"dimension": "模型版本", "before": "1.0", "after": "2.0"}])], packets, [])[0]
    repeated = pool.validate([event(action="release", edition="v2.0", changes=[
        {"dimension": "推理功能", "before": "无", "after": "支持推理"}])], packets, [])[0]
    assert first["event_key"] == repeated["event_key"]
    peers = [{"script_id": "first", "title": "已有发布", "story": first, "sources": {guard.source_key(SNAKE)}}]
    assert pool.duplicate(repeated, peers)
    later = pool.validate([event(action="release", edition="2.0", event_date="2026-10-25", changes=[
        {"dimension": "权重开放", "before": "仅API", "after": "开放权重"}])], packets, [])[0]
    assert not pool.duplicate(later, peers)


def test_already_covered_edition_is_not_new_when_linking_multiple_historical_editions():
    packets = pool.packets(f"### 发布\n{SNAKE}")
    peers = []
    for edition in ("1.0", "2.0"):
        story = pool.validate([event(action="release", edition=edition, event_date="")], packets, [])[0]
        peers.append({"script_id": edition, "title": edition, "story": story, "sources": {guard.source_key(SNAKE)}})
    repeated = pool.validate([event(action="release", edition="2.0", same_as=["H1", "H2"])], packets, pool.context(peers))[0]
    assert not repeated["changes"] and pool.duplicate(repeated, peers)


def test_same_pool_groups_multiple_headlines_for_same_main_event():
    packets = pool.packets(f"### 研究报道\n{SNAKE}\n\n---\n\n### 另一报道\n{NEW}")
    events = pool.validate([event(), event(title="另一种标题", primary=["S2"])], packets, [])
    assert len(events) == 1 and events[0]["primary"] == ["S1", "S2"]


def test_published_older_version_stays_in_semantic_memory_after_topic_changes():
    peers = [{"script_id": "same-script", "version": 1, "title": "旧事件", "sources": {"old.example/opaque-id"}},
             {"script_id": "same-script", "version": 2, "title": "另一个事件", "sources": {"new.example/opaque-id"}}]
    previous = pool.context(peers)
    assert len(previous) == 2
    packets = pool.packets(f"### 换链接重述旧事件\n{SNAKE}")
    old = pool.validate([event(same_as=["H1"])], packets, previous)[0]
    other = pool.validate([event(same_as=["H2"])], packets, previous)[0]
    assert old["event_key"] != other["event_key"]
    assert pool.duplicate(old, peers) == [peers[0]]


def test_same_entity_without_edition_or_date_is_not_blanket_banned():
    a = pool.validate([event(event_date="")], pool.packets(f"### 小蛇\n{SNAKE}"), [])[0]
    b = pool.validate([event(event_date="")], pool.packets(f"### 新实验\n{NEW}"), [])[0]
    assert not pool._same_event(a, b)


def test_main_event_alias_blocks_changed_links_and_extra_background(guard_env, monkeypatch):
    previous = script(guard_env, "science/daily")
    peers = history(guard_env)
    candidates = create_pool(monkeypatch, [event(title="另一个标题", same_as=["H1"], primary=["S2"], background=["S1"])], peers)
    row = fresh(guard_env)
    with pytest.raises(pool.NoCandidate):
        pool.take(row["id"], candidates, peers)
    result = pool.selection(row["id"])
    assert result["skipped"] == 1 and not result["selected"]
    assert result["decisions"][0]["peer_id"] == previous["id"]
    assert store.get_script(row["id"])["current_version"] == 0


def test_configs_claim_different_events_and_third_makes_fewer(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch, [event(), event(title="恐龙", subject="恐龙新发现", primary=["S2"])])
    rows = [fresh(guard_env) for _ in range(3)]
    first, _ = pool.take(rows[0]["id"], candidates, [])
    second, decisions = pool.take(rows[1]["id"], candidates, [])
    assert first["event_key"] != second["event_key"]
    assert decisions[0]["peer_id"] == rows[0]["id"]
    with pytest.raises(pool.NoCandidate):
        pool.take(rows[2]["id"], candidates, [])
    assert len(pool.reservations("matrix:science")) == 2


def test_concurrent_transactions_reserve_only_once(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    rows = [fresh(guard_env), fresh(guard_env)]
    barrier = Barrier(2)

    def choose(row):
        barrier.wait()
        try:
            return pool.take(row["id"], candidates, [])[0]
        except pool.NoCandidate:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(choose, rows))
    assert sum(r is not None for r in result) == 1


def test_restart_resumes_claim_failure_releases_and_draft_covers(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    first = fresh(guard_env)
    selected, _ = pool.take(first["id"], candidates, [])
    store.init_db()
    assert pool.take(first["id"], candidates, [])[0] == selected
    store.update_script(first["id"], status="FAILED")
    pool.reconcile()
    assert claim_state(first["id"]) == "RELEASED"
    second = fresh(guard_env)
    selected, _ = pool.take(second["id"], candidates, [])
    store.add_version(second["id"], title="小蛇", sections=sections(), length_check={}, edit_type="GENERATED", meta={"story": selected})
    store.update_script(second["id"], status="DRAFT")
    pool.reconcile()
    assert claim_state(second["id"]) == "COVERED"
    assert history(guard_env)[0]["story"] == selected


def test_cancelled_unwritten_claim_releases_but_receipt_remains(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    first = fresh(guard_env)
    pool.take(first["id"], candidates, [])
    run = ap.create_run({}, [{"column_key": "flash", "topic": first["topic"]}])
    ap.update_item(run["items"][0]["id"], script_id=first["id"], state="CANCELLED")
    pool.reconcile()
    assert claim_state(first["id"]) == "RELEASED"
    second = fresh(guard_env)
    pool.take(second["id"], candidates, [])
    with sqlite3.connect(guard_env / "state/platforms.db") as conn:
        conn.execute("CREATE TABLE uploads(script_id TEXT,status TEXT,bvid TEXT)")
        conn.execute("INSERT INTO uploads VALUES(?,?,?)", (second["id"], "VERIFYING", ""))
    store.update_script(second["id"], status="FAILED")
    pool.reconcile()
    assert claim_state(second["id"]) == "ACTIVE"


def test_receipt_scan_tolerates_a_new_queue_before_schema_initialization(guard_env, monkeypatch):
    folder = guard_env / "state/platforms/new-platform"
    folder.mkdir(parents=True)
    sqlite3.connect(folder / "queue.db").close()
    candidates = create_pool(monkeypatch)
    row = fresh(guard_env)
    pool.take(row["id"], candidates, [])
    pool.reconcile()
    assert claim_state(row["id"]) == "ACTIVE"


def test_cancelled_author_cannot_commit_after_another_config_takes_event(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    first = fresh(guard_env)
    chosen, _ = pool.take(first["id"], candidates, [])
    run = ap.create_run({}, [{"column_key": "flash", "topic": first["topic"]}])
    ap.update_item(run["items"][0]["id"], script_id=first["id"], state="CANCELLED")
    pool.reconcile()
    pool.take(fresh(guard_env)["id"], candidates, [])
    with pytest.raises(pool.SelectionError):
        store.add_version(first["id"], title="小蛇", sections=sections(), length_check={}, edit_type="GENERATED",
                          story_claim=chosen["event_key"])
    assert store.get_script(first["id"])["current_version"] == 0


def test_cancelled_writer_cannot_reclaim_or_leave_matrix_waiting_forever(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    first = fresh(guard_env)
    run = ap.create_run({}, [{"column_key": "flash", "topic": first["topic"]}])
    ap.update_item(run["items"][0]["id"], script_id=first["id"], state="CANCELLED")
    with pytest.raises(pool.SelectionError):
        pool.take(first["id"], candidates, [])
    paths = get_settings().paths
    assert not guard.writing_peer(first["topic"], paths.config_dir, paths.jobs_dir)


def test_restarted_released_claim_waits_until_reassigned(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    first = fresh(guard_env)
    pool.take(first["id"], candidates, [])
    store.update_script(first["id"], status="FAILED")
    pool.reconcile()
    store.update_script(first["id"], status="WRITING")  # automatic retry after restart
    paths = get_settings().paths
    assert guard.writing_peer(first["topic"], paths.config_dir, paths.jobs_dir)
    pool.take(first["id"], candidates, [])
    assert not guard.writing_peer(first["topic"], paths.config_dir, paths.jobs_dir)


def test_genuine_new_result_can_reuse_url_and_pass_same_pre_render_check(guard_env, monkeypatch):
    old = script(guard_env, "science/daily")
    peers = history(guard_env)
    delta = [{"dimension": "实验结果", "before": "仅观察到滚动", "after": "新实验证实滚动速度提高"}]
    candidates = create_pool(monkeypatch, [event(same_as=["H1"], changes=delta)], peers)
    row = fresh(guard_env)
    selected, _ = pool.take(row["id"], candidates, peers)
    assert selected["previous"] == [old["id"]]
    paths = get_settings().paths
    guard.check(store.get_script(row["id"]), sections(), paths.config_dir, paths.jobs_dir)
    with pytest.raises(pool.NoCandidate):
        pool.take(fresh(guard_env)["id"], candidates, peers)


def test_published_rows_unchanged_and_never_reassigned(guard_env, monkeypatch):
    row = script(guard_env, "science/daily")
    store.add_publication(row["id"], version=1, url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25")
    before = store.get_script(row["id"]), store.get_version(row["id"], 1), store.list_publications(row["id"])
    candidates = create_pool(monkeypatch)
    with pytest.raises(pool.SelectionError):
        pool.take(row["id"], candidates, [])
    pool.reconcile()
    assert before == (store.get_script(row["id"]), store.get_version(row["id"], 1), store.list_publications(row["id"]))


def test_writer_receives_selected_material_and_frozen_event_metadata(guard_env, monkeypatch, llm):
    script(guard_env, "science/daily")
    row = fresh(guard_env)
    text = f"### 小蛇\n{SNAKE}\n\n---\n\n### 恐龙\n{NEW}"
    (guard_env / row["report_path"]).write_text(text)
    organized(monkeypatch, [event(), event(title="恐龙", subject="恐龙新发现", primary=["S2"])])
    FakeLLM.replies = [script_json(citations={(0, 0): [NEW]})]
    assert workflow.run(row["id"]) == "DRAFT"
    version = store.get_version(row["id"], 1)
    assert version["meta"]["story"]["subject"] == "恐龙新发现"
    request = FakeLLM.calls[0][-1]["content"]
    assert NEW in request and SNAKE not in request
    assert "已选主事件" in request
    assert claim_state(row["id"]) == "COVERED"


def test_adaptation_uses_same_selected_event_and_saves_metadata(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    row = fresh(guard_env)
    chosen, _ = pool.take(row["id"], candidates, [])
    row = store.get_script(row["id"])
    captured = {}

    def adapt(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(title="小蛇", sections=sections(), length_check={}, meta={}), {}

    monkeypatch.setattr(workflow.adapt_pipeline, "adapt", adapt)
    column = replace(columns.get(str(guard_env / "config"), "flash"), writer="adapt")
    workflow._adapt_version(row["id"], report=pool.material_for(candidates, chosen), report_path=row["report_path"],
                            citation_check=None, column=column, topic=row["topic"], style=object(), started_by="autopilot", llm_config=None)
    assert captured["topic"]["story"] == chosen
    assert store.get_version(row["id"], 1)["meta"]["story"] == chosen
    assert claim_state(row["id"]) == "COVERED"


def test_exhaustion_archives_only_new_draft_and_automatically_reselects(guard_env, monkeypatch, llm):
    script(guard_env, "science/daily")
    row = fresh(guard_env)
    organized(monkeypatch, [event()])
    fallback = {**row["topic"], "id": "tomorrow/F3", "title": "恐龙"}
    topic = {**row["topic"], "id": "today/F2", "_story_fallbacks": [fallback]}
    store.update_script(row["id"], topic=topic)
    run = ap.create_run({}, [{"column_key": "flash", "topic": topic}])
    item_id = run["items"][0]["id"]
    ap.update_item(item_id, script_id=row["id"], state="WORKING")
    assert workflow.run(row["id"]) == "FAILED"
    assert store.get_script(row["id"])["archived"]
    assert not FakeLLM.calls  # no duplicated body was written
    auto.advance(ap.get_run(run["id"])["items"][0], can_start_script=True, can_start_render=False)
    item = ap.get_run(run["id"])["items"][0]
    assert item["state"] == "PENDING" and item["script_id"] is None
    assert item["topic"]["id"] == "tomorrow/F3" and "自动改选" in item["note"]
    assert item["gate"]["story_attempts"][0]["selection"]["skipped"] == 1


def test_no_candidates_finishes_as_shortage_and_batch_explains_counts(guard_env, monkeypatch):
    row = fresh(guard_env)
    organized(monkeypatch, [])
    run = ap.create_run({}, [{"column_key": "flash", "topic": row["topic"]}])
    item_id = run["items"][0]["id"]
    ap.update_item(item_id, script_id=row["id"], state="WORKING")
    workflow.run(row["id"])
    auto.advance(ap.get_run(run["id"])["items"][0], can_start_script=True, can_start_render=False)
    run = ap.get_run(run["id"])
    assert run["items"][0]["state"] == "CANCELLED"
    assert "候选不足" in run["items"][0]["note"]
    assert "候选 0，采用 0" in auto._summary(run) and "素材不足 1" in auto._summary(run)


def test_assigned_event_lets_other_config_select_without_waiting_for_body(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch)
    row = fresh(guard_env)
    paths = get_settings().paths
    assert guard.writing_peer(row["topic"], paths.config_dir, paths.jobs_dir)
    pool.take(row["id"], candidates, [])
    assert not guard.writing_peer(row["topic"], paths.config_dir, paths.jobs_dir)


def test_historical_replay_uses_readonly_source_and_temporary_claims(guard_env):
    from pathlib import Path
    from scripts.replay_video_story_selection import inputs, replay

    previous = script(guard_env, "science/daily")
    row = fresh(guard_env)
    original_path = store.db_path()
    before = store.get_script(previous["id"]), store.get_script(row["id"])
    groups = inputs(Path(original_path), [row["id"]], str(guard_env / "config"), str(guard_env / "jobs"))
    result = replay(groups, saved={groups[0]["key"]: [event(same_as=["H1"])]})
    assert result["production_readonly"] and result["drafts_written"] == 0
    assert result["groups"][0]["allocations"][0]["skipped"] == 1
    assert not result["groups"][0]["allocations"][0]["selected"]
    assert store.db_path() == original_path
    assert before == (store.get_script(previous["id"]), store.get_script(row["id"]))
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_story_claims").fetchone()[0] == 0
