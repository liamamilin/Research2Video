"""Real daily/weekly failure modes, isolated from the production ledger and all publishing providers."""
import pytest

from core.video import content_guard as guard, reports, store
from scripts import archive_video_duplicates as cleanup, eval_video_chain as evaluation
from tests.test_douyin import douyin_env  # noqa: F401
from tests.test_platforms import platform_env, render  # noqa: F401
from tests.test_social_publishing import social_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import douyin, platform_manager, social_publishing, video_autopilot, video_executor, video_workflow
from web.settings import get_settings

SNAKE = "https://www.sciencedaily.com/releases/2026/10/261023023718.htm"
NEW = "https://www.science.org/content/article/new-dinosaur-discovery"


def sections(url=SNAKE):
    return [{"title": "开场", "paragraphs": [{"text": "这条小蛇滚动逃跑。", "citations": [url]}]}]


@pytest.fixture
def guard_env(video_env):
    root = video_env
    folder = root / "config/matrices"
    folder.mkdir(exist_ok=True)
    (folder / "science.yaml").write_text("name: 科学\njobs: [science/daily, science/weekly]\n")
    (folder / "history.yaml").write_text("name: 历史\njobs: [history/daily]\n")
    return root


def script(root, job, *, url=SNAKE, recipe="flash", creator="autopilot", ready=True, report_path=""):
    path = report_path or f"output/{job}.md"
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("### 小蛇\n\n小蛇会滚。\n\n来源：" + url)
    record = reports.record_file(path, job=job)
    topic = {"title": job, "from_report": path, "report_id": record["id"],
             "matrix_dedup": {"job": job, "policy": "matrix-source-v1"}}
    row = store.create_script(recipe, topic, creator)
    store.update_script(row["id"], report_path=path, report_id=record["id"])
    if ready:
        store.add_version(row["id"], title="蛇不爬，改滚", sections=sections(url), length_check={}, edit_type="GENERATED")
        store.update_script(row["id"], status="DRAFT")
    return store.get_script(row["id"])


def check(row):
    paths = get_settings().paths
    return guard.check(row, sections(), paths.config_dir, paths.jobs_dir)


def test_daily_and_weekly_share_story_identity_even_after_title_changes(guard_env):
    first = script(guard_env, "science/daily")
    second = script(guard_env, "science/weekly")
    with pytest.raises(guard.DuplicateContent) as exc:
        check(second)
    assert exc.value.peers[0]["script_id"] == first["id"]
    check(first)  # keep the earlier draft deterministically, including same-second creations


def test_different_matrices_and_different_stories_on_same_report_are_allowed(guard_env):
    first = script(guard_env, "science/daily")
    check(script(guard_env, "history/daily"))
    other = script(guard_env, "science/daily", url=NEW, recipe="point_short", report_path=first["report_path"])
    paths = get_settings().paths
    guard.check(other, sections(NEW), paths.config_dir, paths.jobs_dir)


def test_same_report_different_recipes_no_longer_exempts_same_story(guard_env):
    first = script(guard_env, "science/daily")
    second = script(guard_env, "science/daily", recipe="point_short", report_path=first["report_path"])
    with pytest.raises(guard.DuplicateContent):
        check(second)
    paths = get_settings().paths
    plan = cleanup.build_plan(paths.config_dir, paths.jobs_dir, paths.state_dir)
    assert [r["script_id"] for r in plan["archive"]] == [second["id"]]


def test_legacy_dated_flash_gets_policy_without_rewriting_history(guard_env):
    first = script(guard_env, "science/daily")
    current = script(guard_env, "science/weekly")
    store.update_script(current["id"], topic={"id": "2026-10-27/F2", "from_report": current["report_path"]})
    original = store.get_script(current["id"])
    guarded = guard.with_policy(original)
    assert guarded["topic"]["matrix_dedup"]["policy"] == "matrix-source-v2"
    with pytest.raises(guard.DuplicateContent) as exc:
        check(guarded)
    assert exc.value.peers[0]["script_id"] == first["id"]
    assert store.get_script(current["id"]) == original


def test_same_report_sibling_waits_then_uses_only_fresh_material(guard_env):
    first = script(guard_env, "science/daily", ready=False)
    paths = get_settings().paths
    assert guard.writing_peer(first["topic"], paths.config_dir, paths.jobs_dir)
    store.add_version(first["id"], title="小蛇", sections=sections(), length_check={}, edit_type="GENERATED")
    store.update_script(first["id"], status="DRAFT")
    second = script(guard_env, "science/daily", recipe="point_short", report_path=first["report_path"], ready=False)
    material = f"### 小蛇\n\n{SNAKE}\n\n### 恐龙\n\n{NEW}"
    report, _, meta = guard.prepare(second, material, "", paths.config_dir, paths.jobs_dir)
    assert NEW in report and SNAKE not in report and meta["peers"][0]["script_id"] == first["id"]


def test_earlier_writer_reserves_render_but_later_writer_cannot_deadlock_first(guard_env):
    first = script(guard_env, "science/daily", ready=False)
    second = script(guard_env, "science/daily", recipe="point_short")
    result = video_executor.advance_auto(second, {}, can_start_script=True, can_start_render=True)
    assert not result["hold"] and "更早" in result["item"]["note"]
    assert not store.list_renders(second["id"])
    store.add_version(first["id"], title="小蛇", sections=sections(), length_check={}, edit_type="GENERATED")
    store.update_script(first["id"], status="DRAFT")
    later = script(guard_env, "science/weekly", ready=False)
    paths = get_settings().paths
    assert not guard.writing_peer(first["topic"], paths.config_dir, paths.jobs_dir, script_id=first["id"])
    assert guard.writing_peer(later["topic"], paths.config_dir, paths.jobs_dir, script_id=second["id"]) is False


def test_pending_upload_receipt_prevents_automatic_archival_and_duplicate_mutation(guard_env):
    import sqlite3

    script(guard_env, "science/daily")
    second = script(guard_env, "science/weekly")
    with sqlite3.connect(guard_env / "state/platforms.db") as conn:
        conn.execute("CREATE TABLE uploads(script_id TEXT,status TEXT,bvid TEXT)")
        conn.execute("INSERT INTO uploads VALUES(?,?,?)", (second["id"], "VERIFYING", ""))
    before = store.get_script(second["id"])
    check(second)
    assert not guard.archive_duplicate(second["id"], "重复", [])
    assert store.get_script(second["id"]) == before


def test_flash_picking_defers_event_identity_and_keeps_finite_alternatives(guard_env, monkeypatch):
    from core.video import topic_store
    from core.video.topics import Topic

    script(guard_env, "science/daily")
    def item(code, url):
        return Topic(f"2026-10-27/{code}", "2026-10-27", code, code, {"来源": url})

    topics = [item("F2", SNAKE), item("F3", NEW), item("F4", NEW + "?utm_source=second"),
              item("F5", "https://science.org/article/another-discovery")]
    monkeypatch.setattr(video_autopilot, "sync_topics", lambda: [])
    monkeypatch.setattr(topic_store, "load", lambda source: ("2026-10-27", topics))
    monkeypatch.setattr(topic_store, "resolve", lambda jobs, source: topic_store.Source("flash", "science/daily", "科学"))
    picked = video_autopilot.pick_topics({"picks": [{"column": "flash", "count": 3, "source": "flash"}]})
    # Shared URLs can carry an actual new result. The event organizer makes
    # that decision before writing, and can replace old events with F5.
    assert [i["topic"]["id"] for i in picked] == ["2026-10-27/F2", "2026-10-27/F3", "2026-10-27/F4"]
    assert all(i["topic"]["matrix_dedup"]["policy"] == "matrix-story-v3" for i in picked)
    assert [t["id"] for t in picked[0]["topic"]["_story_fallbacks"]] == ["2026-10-27/F5"]


def test_already_queued_cross_day_topic_cancels_before_spawning_writer(guard_env, monkeypatch):
    from core.video import autopilot_store as ap

    first = script(guard_env, "science/daily")
    topic = {**first["topic"], "id": "2026-10-27/F2", "fields": {"来源": SNAKE}}
    monkeypatch.setattr(video_workflow, "spawn", lambda *args: pytest.fail("duplicate must not start writing"))
    run = ap.create_run({}, [{"column_key": "flash", "topic": topic}])
    video_autopilot.advance(run["items"][0], can_start_script=True, can_start_render=True)
    item = ap.get_run(run["id"])["items"][0]
    assert item["state"] == "CANCELLED" and not item["script_id"]


def test_replay_cannot_mutate_ledger_and_preserves_published_versions(guard_env):
    import sqlite3
    from pathlib import Path
    from scripts.eval_video_dedup import readonly_ledger, replay

    first = script(guard_env, "science/daily")
    second = script(guard_env, "science/weekly")
    store.add_publication(first["id"], version=1, url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25")
    before = cleanup.published_digest()
    paths = get_settings().paths
    results = replay(Path(store.db_path()), [first["id"], second["id"]], paths.config_dir, paths.jobs_dir)
    assert [r["result"] for r in results] == ["PROTECTED", "DUPLICATE"]
    with readonly_ledger(Path(store.db_path())):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store.update_script(second["id"], archived=1)
    assert not store.get_script(second["id"])["archived"] and cleanup.published_digest() == before


def test_published_sample_blocks_repeated_story_without_changing_sample(guard_env):
    sample = script(guard_env, "science/daily", creator="chain-validation")
    store.add_publication(sample["id"], version=1, url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25")
    before = store.get_script(sample["id"])
    with pytest.raises(guard.DuplicateContent):
        check(script(guard_env, "science/weekly"))
    assert store.get_script(sample["id"]) == before
    assert not guard.archive_duplicate(sample["id"], "重复", [])
    assert len(store.list_publications(sample["id"])) == 1


def test_unpublished_sample_and_archived_draft_do_not_consume_real_topics(guard_env):
    sample = script(guard_env, "science/daily", creator="chain-validation")
    assert sample["archived"] and sample not in store.list_scripts()
    first_weekly = script(guard_env, "science/weekly")
    check(first_weekly)
    store.update_script(first_weekly["id"], archived=1)
    regular = script(guard_env, "science/daily")
    store.update_script(regular["id"], archived=1)
    weekly = script(guard_env, "science/weekly")
    store.update_script(weekly["id"], status="FAILED")
    check(script(guard_env, "science/weekly", report_path="output/future.md"))


def test_published_older_version_and_local_removed_record_still_reserve_story(guard_env):
    first = script(guard_env, "science/daily")
    pub = store.add_publication(first["id"], version=1, url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25")
    store.add_version(first["id"], title="新选题", sections=sections(NEW), length_check={}, edit_type="EDITED")
    store.delete_publication(pub["id"])
    store.update_script(first["id"], archived=1)
    with pytest.raises(guard.DuplicateContent):
        check(script(guard_env, "science/weekly"))


def test_prepare_strips_old_original_material_and_keeps_new_story(guard_env):
    script(guard_env, "science/daily")
    current = script(guard_env, "science/weekly", ready=False)
    material = f"### 小蛇\n\n小蛇会滚\n{SNAKE}\n\n---\n\n### 恐龙\n\n新发现\n{NEW}"
    report, fresh, meta = guard.prepare(current, material, material, str(guard_env / "config"), str(guard_env / "jobs"))
    assert "小蛇" not in fresh and SNAKE not in report
    assert NEW in fresh and "恐龙" in fresh
    assert meta["peers"] and meta["excluded_sources"] == [guard.source_key(SNAKE)]
    with pytest.raises(guard.DuplicateContent):
        guard.prepare(current, SNAKE, "", str(guard_env / "config"), str(guard_env / "jobs"))


def test_sources_are_canonical_and_shared_reference_pages_remain_reusable():
    assert guard.source_key(SNAKE + "?utm_source=share#part") == guard.source_key(SNAKE.replace("https:", "http:"))
    assert guard.source_key("https://arxiv.org/html/2610.12345v2") == guard.source_key("https://arxiv.org/abs/2610.12345")
    for url in ("https://docs.example.com/models", "https://artificialanalysis.ai/leaderboards/models#one", "https://example.com/rss.xml",
                "https://www.ft.com/rss/home", "https://example.com/feeds/news", "https://example.com/feed"):
        assert not guard.source_key(url)
    assert guard.source_key("https://developers.example.com/blog/new-model")
    assert guard.overlap({"a.example/critical-maxkb-ai-agent-flaw"}, {"b.example/critical-maxkb-ai-agent-flaw"})
    assert not guard.overlap({"a.example/openai-launches-gpt6"}, {"b.example/anthropic-ships-opus-55"})


def test_extra_background_link_does_not_make_covered_core_event_fresh(guard_env):
    script(guard_env, "science/daily")
    topic = guard.policy_topic({"id": "2026-10-27/F2", "title": "同一个小蛇事件的新标题",
                                "fields": {"证据来源": SNAKE, "相关资料": NEW}}, "science/daily")
    paths = get_settings().paths
    assert guard.topic_sources(topic) == {guard.source_key(SNAKE)}
    with pytest.raises(guard.DuplicateContent):
        guard.check_topic(topic, paths.config_dir, paths.jobs_dir)


def test_new_event_can_reuse_covered_article_as_background(guard_env):
    script(guard_env, "science/daily")
    topic = guard.policy_topic({"title": "恐龙的新发现", "fields": {"证据来源": NEW, "背景资料": SNAKE}}, "science/daily")
    paths = get_settings().paths
    guard.check_topic(topic, paths.config_dir, paths.jobs_dir)
    assert guard.topic_sources(topic) == {guard.source_key(NEW)}
    topic["fields"] = {"证据来源": "[1]", "来源网址": NEW, "背景资料": SNAKE}
    assert guard.topic_sources(topic) == {guard.source_key(NEW)}


def test_new_comparison_with_one_old_reference_is_not_archived_as_a_duplicate(guard_env):
    script(guard_env, "science/daily")
    script(guard_env, "science/daily", url="https://science.org/article/another-discovery", report_path="output/previous.md")
    current = script(guard_env, "science/weekly", url=NEW)
    mixed = sections(NEW)
    mixed[0]["paragraphs"][0]["citations"] += [SNAKE, "https://science.org/article/another-discovery"]
    store.add_version(current["id"], title="新旧发现的比较", sections=mixed, length_check={}, edit_type="EDITED")
    paths = get_settings().paths
    guard.check(current, mixed, paths.config_dir, paths.jobs_dir)
    plan = cleanup.build_plan(paths.config_dir, paths.jobs_dir, paths.state_dir)
    assert not plan["archive"]


def test_queued_weekly_waits_for_daily_draft_then_checks_its_final_sources(guard_env):
    daily = script(guard_env, "science/daily", ready=False)
    weekly = script(guard_env, "science/weekly", ready=False)
    store.update_script(weekly["id"], status="FAILED")  # only the not-yet-started queue item below is tested
    paths = get_settings().paths
    assert guard.writing_peer(weekly["topic"], paths.config_dir, paths.jobs_dir)
    store.add_version(daily["id"], title="小蛇", sections=sections(), length_check={}, edit_type="GENERATED")
    store.update_script(daily["id"], status="DRAFT")
    assert not guard.writing_peer(weekly["topic"], paths.config_dir, paths.jobs_dir)


def test_auto_render_archives_duplicate_without_spawning_a_render(guard_env):
    first = script(guard_env, "science/daily")
    second = script(guard_env, "science/weekly")
    store.update_script(second["id"], status="APPROVED", approved_version=1)
    result = video_executor.advance_auto(store.get_script(second["id"]), {}, can_start_script=True, can_start_render=True)
    assert result["item"]["state"] == "CANCELLED"
    assert store.get_script(second["id"])["archived"]
    assert store.get_script(second["id"])["topic"]["content_archive"]["duplicate_of"] == [first["id"]]
    assert not store.list_renders(second["id"])


def test_fully_covered_report_is_archived_before_calling_any_writer(guard_env, monkeypatch):
    script(guard_env, "science/daily")
    current = script(guard_env, "science/weekly", ready=False)
    monkeypatch.setattr(video_workflow.writer, "write", lambda **kw: pytest.fail("duplicate must not spend an LLM call"))
    assert video_workflow.run(current["id"], started_by="autopilot") == "FAILED"
    assert store.get_script(current["id"])["archived"]
    assert "均已讲过" in store.get_script(current["id"])["error"]


def test_programmatic_validation_cannot_write_to_production_store(guard_env, monkeypatch):
    monkeypatch.setattr(evaluation, "ROOT", guard_env)
    with pytest.raises(ValueError, match="正式视频数据库"):
        evaluation.require_isolated_store(guard_env / "state/validation/check")


def test_queue_serializes_daily_and_weekly_but_keeps_other_matrices_parallel(guard_env, monkeypatch):
    from core.video import autopilot_store as ap

    topics = [script(guard_env, job, ready=False)["topic"] for job in ("science/daily", "science/weekly", "history/daily")]
    for s in store.list_scripts():
        store.update_script(s["id"], status="FAILED")
    spawned = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    run = ap.create_run({}, [{"column_key": "flash", "topic": topic} for topic in topics])
    for item in run["items"]:
        video_autopilot.advance(item, can_start_script=True, can_start_render=True)
    assert len(spawned) == 2
    items = ap.get_run(run["id"])["items"]
    assert items[0]["script_id"] and not items[1]["script_id"] and items[2]["script_id"]
    assert "同矩阵" in items[1]["note"]


def test_cleanup_prefers_published_work_and_never_edits_published_history(guard_env):
    daily = script(guard_env, "science/daily")
    weekly = script(guard_env, "science/weekly")
    store.add_publication(weekly["id"], version=1, platform="douyin", url="https://www.douyin.com/video/7700436961890209034", published_on="2026-10-25")
    before = cleanup.published_digest()
    plan = cleanup.build_plan(str(guard_env / "config"), str(guard_env / "jobs"), str(guard_env / "state"))
    assert [r["script_id"] for r in plan["archive"]] == [daily["id"]]
    receipt = cleanup.apply_plan(plan, str(guard_env / "state"), guard_env / "maintenance")
    assert receipt["published_unchanged"] and cleanup.published_digest() == before
    assert store.get_script(daily["id"])["archived"]
    assert not store.get_script(weekly["id"])["archived"]


def test_cleanup_keeps_completed_video_and_stops_redundant_failed_render_retry(guard_env):
    daily = script(guard_env, "science/daily")
    weekly = script(guard_env, "science/weekly")
    render_row = store.create_render(weekly["id"], 1, title="已完成", source_text="正文", source_sha256="a", request={})
    store.update_render(render_row["id"], status="COMPLETED")
    plan = cleanup.build_plan(str(guard_env / "config"), str(guard_env / "jobs"), str(guard_env / "state"))
    assert [r["script_id"] for r in plan["archive"]] == [daily["id"]]


@pytest.mark.parametrize("sample", [True, False])
def test_bilibili_candidates_and_direct_preview_enforce_isolation(platform_env, sample):
    row = render()
    if sample:
        store.update_script(row["script_id"], created_by="chain-validation")
    else:
        assert guard.archive_duplicate(row["script_id"], "重复主选题", ["kept"])
    assert not platform_manager.candidates()
    with pytest.raises(platform_manager.Error, match="样片|重复"):
        platform_manager.eligible(row["id"])


def test_published_sample_is_excluded_from_new_posting_but_still_counted_in_history(platform_env):
    row = render()
    store.update_script(row["script_id"], created_by="chain-validation")
    store.add_publication(row["script_id"], version=1, platform="bilibili", account_uid="123",
                          url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25")
    candidates, stats = platform_manager.candidate_catalog()
    assert not candidates
    assert stats["completed_renders"] == stats["published_versions"] == 1


def test_sample_isolation_does_not_change_publication_status_or_account_warnings(platform_env):
    from web.services import distributions

    row = render()
    store.update_script(row["script_id"], created_by="chain-validation")
    store.add_publication(row["script_id"], version=1, platform="bilibili", account_uid="123",
                          url="https://www.bilibili.com/video/BV1xx411c7mD", published_on="2026-10-25")
    states = distributions.overview([store.get_script(row["script_id"])])[row["script_id"]]
    bili = next(s for s in states if s["platform"] == "bilibili" and s["current_account"])
    assert bili["status"] == "PUBLISHED" and not bili["blocked_reason"]
    assert not any(s["can_prepare"] for s in states)
    assert not any("样片" in s["blocked_reason"] for s in states)


def test_douyin_excludes_samples_but_keeps_normal_archived_historical_versions(douyin_env):
    row = render()
    store.update_script(row["script_id"], archived=1)
    assert douyin.candidates()
    store.update_script(row["script_id"], created_by="chain-validation")
    assert not douyin.candidates()


@pytest.mark.parametrize("platform", ["kuaishou", "xiaohongshu"])
def test_other_platform_candidates_share_the_same_isolation_policy(social_env, platform):
    row = render()
    assert social_publishing.candidates(platform)
    store.update_script(row["script_id"], topic={"test_sample": True})
    assert not social_publishing.candidates(platform)
