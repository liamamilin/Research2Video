"""Usage and timing records: attribution, the LLM and search hooks, render ingestion, backfill, pricing, the API."""
import json
import re
import threading
from pathlib import Path
import time
from types import SimpleNamespace as NS

import pytest

from core import telemetry
from core.llm import LLMClient, LLMConfig
from core.telemetry import assistant, ingest, performance, pricing, query, retention


@pytest.fixture()
def db(tmp_path):
    telemetry.use_state_dir(str(tmp_path))
    return tmp_path


def rows(table):
    with telemetry.connect() as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def llm_client(**options):
    client = LLMClient.__new__(LLMClient)
    client.config = LLMConfig(model="m1", base_url="https://opencode.ai/zen/go/v1", **options)
    client._cancel = threading.Event()
    client.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return client


def test_nothing_is_written_without_a_state_directory(tmp_path):
    with telemetry.track("write", script_id="s1"):
        telemetry.record_call("llm", model="m")
    assert not list(tmp_path.iterdir())


def test_track_times_the_stage_and_every_call_inside_inherits_its_attribution(db):
    with telemetry.track("write", script_id="s1", column_key="c1", form="information_motion"):
        with telemetry.track("audit", matrix="m1"):
            telemetry.record_call("llm", model="x", usage={"prompt_tokens": 10, "completion_tokens": 5})
        telemetry.record_call("llm", model="y")
    nested, after = rows("calls")
    assert (nested["stage"], nested["script_id"], nested["matrix"], nested["column_key"]) == ("audit", "s1", "m1", "c1")
    assert (after["stage"], after["script_id"], after["matrix"]) == ("write", "s1", "")
    spans = {r["stage"]: r for r in rows("spans")}
    assert spans["write"]["status"] == "ok" and spans["write"]["duration_s"] >= 0
    assert spans["audit"]["matrix"] == "m1" and spans["write"]["form"] == "information_motion"
    assert nested["span_id"] == spans["audit"]["id"] and after["span_id"] == spans["write"]["id"]


def test_a_failed_or_cancelled_stage_is_recorded_and_the_error_still_propagates(db):
    class CancelledError(Exception):
        pass

    with pytest.raises(ValueError):
        with telemetry.track("write", script_id="s1"):
            raise ValueError("boom")
    with pytest.raises(CancelledError):
        with telemetry.track("audit", script_id="s1"):
            raise CancelledError()
    assert [(r["stage"], r["status"]) for r in rows("spans")] == [("write", "failed"), ("audit", "cancelled")]


def test_a_new_thread_does_not_inherit_attribution_so_track_goes_inside_the_thread(db):
    with telemetry.track("write", script_id="s1"):
        worker = threading.Thread(target=lambda: telemetry.record_call("llm", model="x"))
        worker.start()
        worker.join()
    assert rows("calls")[0]["script_id"] == ""


def test_recording_failures_never_reach_the_caller(db, monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr("core.telemetry.store.sqlite_util.connect", broken)
    telemetry.record_call("llm", model="x")
    with telemetry.track("write"):
        pass


def test_llm_requests_are_recorded_with_model_provider_tokens_and_timing(db):
    client = llm_client()
    client._call_started = time.monotonic()
    with telemetry.track("write", script_id="s1"):
        client._record_usage({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "reasoning_tokens": 5,
                              "cache_read_tokens": 40, "cost_usd": 0.002})
    [call] = rows("calls")
    assert (call["kind"], call["provider"], call["model"], call["script_id"]) == ("llm", "opencode.ai", "m1", "s1")
    assert (call["prompt_tokens"], call["completion_tokens"], call["reasoning_tokens"], call["cache_read_tokens"]) == \
        (100, 20, 5, 40)
    assert call["reported_cost_usd"] == 0.002 and call["duration_s"] is not None and not call["fell_back_from"]
    assert client.usage["requests"] == 1


def test_a_model_switch_leaves_a_failed_row_and_marks_the_next_call_as_a_fallback(db):
    client = llm_client(fallback_models=["m2"])
    client._model_idx = 0
    assert client._switch_model("额度用完", RuntimeError("402"))
    client._record_usage({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
    failed, answered = rows("calls")
    assert (failed["model"], failed["error_code"], failed["prompt_tokens"]) == ("m1", "额度用完", 0)
    assert (answered["model"], answered["fell_back_from"]) == ("m2", "m1")


def test_search_attempts_are_recorded_with_provider_and_failure(db):
    telemetry.record_search("parallel", "", time.monotonic())
    telemetry.record_search("exa", "no_results", time.monotonic())
    ok, empty = rows("calls")
    assert (ok["kind"], ok["provider"], ok["error_code"]) == ("search", "parallel", "")
    assert empty["error_code"] == "no_results"


def test_search_client_records_each_provider_it_tried(db, monkeypatch):
    from core.search import SearchClient, SearchConfig
    client = SearchClient(SearchConfig(provider="parallel", fallback_provider="duckduckgo"))
    monkeypatch.setattr(client, "_search_parallel", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no credits")))
    monkeypatch.setattr(client, "_search_duckduckgo", lambda *a, **k: [])
    client.search(["ai agents"])
    assert [(r["provider"], bool(r["error_code"])) for r in rows("calls")] == [("parallel", True), ("duckduckgo", True)]


def render_files(directory):
    directory.mkdir(parents=True)
    (directory / "model-cost.json").write_text(json.dumps({
        "endpoint": "https://api.deepseek.com/chat/completions", "model": "deepseek-flash",
        "calls": [
            {"call_id": "c3-explanation", "stage": "c3-explanation", "model": "deepseek-flash", "elapsed_seconds": 5.5,
             "prompt_tokens": 1000, "completion_tokens": 400, "reasoning_tokens": 100,
             "pricing_snapshot": {"input_uncached_peak_usd_per_million": 0.3, "output_peak_usd_per_million": 1.2}},
            {"call_id": "chapter-c5-diagram-attempt-1", "stage": "chapter-c5-diagram", "model": "deepseek-flash",
             "elapsed_seconds": 7.0, "prompt_tokens": 500, "completion_tokens": 100}]}), encoding="utf-8")
    (directory / "execution-log.txt").write_text(
        "STEP tts-and-align: PASS in 454.10s\nSTEP draw-things-backgrounds: SKIPPED (x)\nSTEP render: PASS in 622.14s\n"
        "STEP package: FAIL in 3.00s\n", encoding="utf-8")


def test_stage_names_collapse_chapters_into_their_kind():
    assert ingest.normalize_stage("c3-explanation") == "explanation"
    assert ingest.normalize_stage("chapter-c12-information") == "information"
    assert ingest.normalize_stage("chapter-c5-diagram") == "diagram"
    assert ingest.normalize_stage("narration-form") == "narration-form"


def test_a_render_is_read_once_into_calls_and_step_spans(db):
    render_files(db / "r1")
    render = {"id": "r1", "status": "COMPLETED", "resolved_video_form": "information_motion",
              "submitted_at": "2026-10-17T22:32:54+0800", "completed_at": "2026-10-17T22:54:23+0800"}
    script = {"id": "s1", "column_key": "col", "content_matrix_id": "mx", "production": "recipe-a"}
    assert ingest.ingest_render(render, script, str(db / "r1")) == 6
    assert ingest.ingest_render(render, script, str(db / "r1")) == 6
    calls, spans = rows("calls"), {r["stage"]: r for r in rows("spans")}
    assert len(calls) == 2 and len(spans) == 4
    assert calls[0]["stage"] == "render/explanation" and calls[0]["provider"] == "api.deepseek.com"
    assert (calls[0]["snapshot_in_per_m"], calls[0]["snapshot_out_per_m"]) == (0.3, 1.2)
    assert calls[1]["stage"] == "render/diagram" and calls[1]["snapshot_in_per_m"] is None
    assert (calls[0]["script_id"], calls[0]["render_id"], calls[0]["form"], calls[0]["matrix"]) == \
        ("s1", "r1", "information_motion", "mx")
    assert spans["render/tts-and-align"]["duration_s"] == 454.1 and spans["render/package"]["status"] == "failed"
    assert spans["render"]["duration_s"] == 1289.0 and "render/draw-things-backgrounds" not in spans


def test_backfill_takes_only_records_older_than_live_recording_and_only_once(db):
    history = db / "history"
    history.mkdir()
    old = {"ts": "2020-01-01T10:00:00+0800", "job_name": "monitoring/daily", "status": "success",
           "duration_seconds": 60.0, "usage": {"requests": 3, "prompt_tokens": 900, "completion_tokens": 100,
                                               "model": "minimax-m3", "searches": 4}}
    new = dict(old, ts="2999-01-01T10:00:00+0800")
    (history / "monitoring_daily.jsonl").write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n", encoding="utf-8")
    telemetry.record_call("llm", model="live")  # live recording starts here
    assert ingest.backfill_history(str(history)) == 3
    assert ingest.backfill_history(str(history)) == 0
    llm = [r for r in rows("calls") if r["model"] == "minimax-m3"][0]
    assert (llm["requests"], llm["prompt_tokens"], llm["job"], llm["stage"]) == (3, 900, "monitoring/daily", "research")
    assert [r["requests"] for r in rows("calls") if r["kind"] == "search"] == [4]
    assert [r["duration_s"] for r in rows("spans")] == [60.0]


def table(*entries):
    return [pricing.Price(model=m, input=i, output=o, cache_read=c, host=h) for m, i, o, c, h in entries]


def group(**fields):
    base = dict(kind="llm", n=1, prompt=1_000_000, completion=1_000_000, cached=0, provider="", model="m", ts=1.0,
                reported=None, reported_n=0, snap_in=None, snap_out=None)
    return {**base, **fields}


def test_cost_prefers_what_the_provider_charged_then_the_recorded_price_then_the_table():
    prices = table(("m", 1.0, 2.0, None, ""))
    assert pricing.cost_of(group(reported=0.5, reported_n=1), prices) == (0.5, "reported")
    assert pricing.cost_of(group(snap_in=0.3, snap_out=1.2), prices) == (1.5, "snapshot")
    assert pricing.cost_of(group(), prices) == (3.0, "table")
    assert pricing.cost_of(group(model="other"), prices) == (None, "unpriced")


def test_cached_prompt_tokens_use_the_cache_rate_and_free_models_cost_nothing():
    prices = table(("m", 1.0, 2.0, 0.1, ""), ("free", 0, 0, 0, ""))
    assert pricing.cost_of(group(cached=500_000), prices)[0] == pytest.approx(0.5 + 0.05 + 2.0)
    assert pricing.cost_of(group(model="free"), prices) == (0.0, "table")


def test_a_price_naming_the_host_beats_a_general_one_and_failed_requests_are_free():
    prices = table(("m", 1.0, 1.0, None, ""), ("m", 0, 0, None, "opencode.ai"))
    assert pricing.cost_of(group(provider="opencode.ai"), prices) == (0.0, "table")
    assert pricing.cost_of(group(provider="other.com"), prices)[0] == 2.0
    assert pricing.cost_of(group(prompt=0, completion=0), prices) == (0.0, "reported")
    assert pricing.cost_of(group(kind="search"), prices) == (None, "unpriced")


def test_the_shipped_price_table_loads_and_covers_the_models_in_use():
    from pathlib import Path
    prices = pricing.load(str(Path(__file__).resolve().parents[1] / "config"))
    for model in ("deepseek-v4.1-flash", "deepseek-flash", "minimax-m3", "minimax-m3.1-flash-free",
                  "longcat-2.5-preview-free"):
        assert pricing.find(prices, "", model), model
    assert pricing.find(prices, "opencode.ai", "deepseek-v4.1-flash").input == 0
    assert pricing.find(prices, "x.example", "deepseek-v4.1-flash").input == 0.3


def test_summary_and_script_bill_add_up_and_flag_what_could_not_be_priced(db):
    config = db / "config"
    config.mkdir()
    (config / "pricing.yaml").write_text("prices:\n  - {model: m, input: 1.0, output: 2.0}\n", encoding="utf-8")
    with telemetry.track("write", script_id="s1", column_key="col"):
        telemetry.record_call("llm", provider="p", model="m", usage={"prompt_tokens": 1_000_000, "completion_tokens": 0})
        telemetry.record_call("llm", provider="p", model="mystery", usage={"prompt_tokens": 10, "completion_tokens": 5})
    telemetry.record_search("parallel", "", time.monotonic())
    report = query.summary(str(config))
    assert report["llm"]["cost_usd"] == 1.0 and report["llm"]["partial"] and report["llm"]["total_tokens"] == 1_000_015
    assert report["unpriced_models"] == ["p mystery"] and report["search"]["calls"] == 1
    assert {m["model"] for m in report["models"] if m["kind"] == "llm"} == {"m", "mystery"}
    [bill] = query.scripts(str(config), filters={"column_key": "col"})["items"]
    assert bill["script_id"] == "s1" and bill["total_tokens"] == 1_000_015
    detail = query.script_detail(str(config), "s1")
    assert [s["stage"] for s in detail["stages"]] == ["write"] and detail["stages"][0]["span_count"] == 1


def test_the_api_is_for_administrators_only_and_serves_the_summary_and_price_table(client, web_env, monkeypatch):
    # the background reader would otherwise keep writing into whichever test's database is current when it finishes
    monkeypatch.setattr("web.routes.telemetry._ensure_synced", lambda: False)
    from tests.test_assistant_api import login
    assert client.get("/api/telemetry/summary").status_code in (401, 403)
    for role in ("viewer", "editor"):
        login(client, role)
        for url in ("summary", "scripts", "compare", "pricing", "storage", "performance", "assistant"):
            assert client.get(f"/api/telemetry/{url}").status_code == 403, (role, url)
        client.cookies.clear()
    login(client, "admin")
    from core import state
    from web.settings import get_settings
    state.use_state_dir(str(get_settings().paths.state_dir))
    config = Path(get_settings().paths.config_dir)
    config.mkdir(parents=True, exist_ok=True)
    (config / "pricing.yaml").write_text("prices:\n  - {model: m, input: 1.0, output: 2.0}\n", encoding="utf-8")
    telemetry.record_call("llm", provider="p", model="m", usage={"prompt_tokens": 10, "completion_tokens": 5})
    body = client.get("/api/telemetry/summary?days=0").json()
    assert body["enabled"] and body["llm"]["total_tokens"] == 15
    assert client.get("/api/telemetry/scripts").json()["items"] == []
    assert client.get("/api/telemetry/pricing").json()["prices"][0]["model"] == "m"
    unconnected = client.get("/api/telemetry/performance?by=form").json()  # no Bilibili account in this environment
    assert unconnected["connected"] is False and unconnected["items"] == [] and unconnected["groups"] == []
    assert client.get("/api/telemetry/performance?by=script_id").status_code == 422
    nothing = client.get("/api/telemetry/assistant?days=0").json()
    assert nothing["enabled"] and nothing["totals"]["requests"] == 0 and nothing["requests"] == []
    assert client.get("/api/telemetry/assistant/requests/nope").json()["turns"] == []


# --- stage entry points ------------------------------------------------------------------------------------------

def test_a_block_can_judge_its_own_outcome_without_raising(db):
    with telemetry.track("audit", script_id="s1") as span:
        span["status"] = "failed"
    assert rows("spans")[0]["status"] == "failed"


def test_phases_end_each_stage_when_the_next_begins_and_rename_them(db):
    with telemetry.Phases({"写稿": "write", "审校": "audit"}, script_id="s1") as phases:
        phases.enter("拆事实")
        phases.enter("写稿")
        telemetry.record_call("llm", model="m")
        phases.enter("审校")
    spans = rows("spans")
    assert [s["stage"] for s in spans] == ["拆事实", "write", "audit"]
    assert all(s["script_id"] == "s1" and s["status"] == "ok" for s in spans)
    assert rows("calls")[0]["stage"] == "write" and rows("calls")[0]["span_id"] == spans[1]["id"]
    assert telemetry.current() == {}


def test_phases_close_the_open_stage_as_failed_when_the_pipeline_raises(db):
    with pytest.raises(RuntimeError):
        with telemetry.Phases() as phases:
            phases.enter("写稿")
            raise RuntimeError("model gone")
    assert [(s["stage"], s["status"]) for s in rows("spans")] == [("写稿", "failed")]
    assert telemetry.current() == {}


def engine_for(job_result, *, cancelled=False):
    from core.engine import ResearchEngine
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.config_dir = "config"
    engine._cancel = threading.Event()
    if cancelled:
        engine._cancel.set()
    engine._now = lambda: __import__("datetime").datetime(2026, 10, 31, 9, 0, 0)
    engine._run_job = lambda name, verbose=False: job_result
    return engine


def test_a_research_run_is_one_span_billed_to_its_job_and_the_video_that_asked(db):
    with telemetry.bind(script_id="s1", column_key="col"):
        assert engine_for("out.md").run_job("monitoring/daily") == "out.md"
        assert engine_for(None).run_job("monitoring/daily") is None
        assert engine_for(None, cancelled=True).run_job("monitoring/daily") is None
    spans = rows("spans")
    assert [(s["stage"], s["status"], s["job"], s["script_id"]) for s in spans] == [
        ("research", "ok", "monitoring/daily", "s1"), ("research", "failed", "monitoring/daily", "s1"),
        ("research", "cancelled", "monitoring/daily", "s1")]
    assert spans[0]["run_ts"].startswith("2026-10-31T09:00:00")


def test_the_job_pool_starts_each_run_with_the_callers_attribution(db, monkeypatch):
    from web.runner import executor
    seen = []
    monkeypatch.setattr(executor, "ResearchEngine", lambda **k: object())
    monkeypatch.setattr(executor, "_attach_persistence", lambda *a: None)
    monkeypatch.setattr(executor, "run_job_in_thread", lambda *a, **k: seen.append(telemetry.current()))
    monkeypatch.setattr("core.matrices.cap_block", lambda *a, **k: "")
    registry = NS(start=lambda name, by, engine: NS(log_bus=NS(write=lambda e: None), future=None))
    monkeypatch.setattr(executor, "TaskRegistry", lambda: registry)
    with telemetry.bind(script_id="s1"):
        executor.start_job("daily", "video").future.result(timeout=5)
    assert seen[0]["script_id"] == "s1"


def test_script_threads_bind_the_scripts_attribution_and_the_pipelines_steps_become_stages(db, monkeypatch):
    from web.services import video_workflow
    script = {"id": "s1", "column_key": "col", "content_matrix_id": "mx", "production": "recipe-a", "topic": {}}
    updates = []
    monkeypatch.setattr(video_workflow.store, "get_script", lambda _id: script)
    monkeypatch.setattr(video_workflow.store, "update_script", lambda _id, **k: updates.append(k))
    monkeypatch.setattr(video_workflow.video_columns, "get", lambda *_a: NS(writer="adapt"))
    with video_workflow._binding("s1"):
        assert telemetry.current() == {"script_id": "s1", "column_key": "col", "matrix": "mx", "recipe": "recipe-a",
                                       "path": "adapt"}
        with telemetry.Phases(video_workflow.STAGE_NAMES) as phases:
            announce = video_workflow._announce("s1", phases)
            announce("写稿")
            announce("整篇复审")
    assert updates == [{"stage": "写稿"}, {"stage": "整篇复审"}]
    spans = rows("spans")
    assert [s["stage"] for s in spans] == ["write", "audit"] and spans[0]["matrix"] == "mx" and spans[0]["path"] == "adapt"


def test_a_script_without_a_column_still_runs_unattributed(db, monkeypatch):
    from web.services import video_workflow
    monkeypatch.setattr(video_workflow.store, "get_script", lambda _id: None)
    with video_workflow._binding("gone"):
        assert telemetry.current() == {}


def test_approval_records_the_wait_since_the_draft_was_ready_apart_from_production(db):
    from web.services import video_executor
    script = {"id": "s1", "column_key": "col", "updated_at": "2026-10-30T09:00:00+0800"}
    video_executor._record_wait(script, 2, "admin")
    video_executor._record_wait({"id": "s2"}, 1, "admin")  # no timestamp: nothing to measure
    [wait] = rows("spans")
    assert (wait["stage"], wait["script_id"], wait["note"]) == ("await-approval", "s1", "v2 by admin")
    assert wait["duration_s"] > 3600


def test_finished_renders_are_swept_in_the_background_one_read_at_a_time(db, monkeypatch):
    reads, release = [], threading.Event()

    def slow_sweep():
        reads.append(1)
        release.wait(5)
        return 0
    monkeypatch.setattr(ingest, "sweep_renders", slow_sweep)
    ingest.sweep_async()
    ingest.sweep_async()  # a read is already running
    time.sleep(0.2)
    release.set()
    deadline = time.time() + 5
    while ingest._sweeping.locked() and time.time() < deadline:
        time.sleep(0.05)
    assert reads == [1]
    ingest.sweep_async()
    time.sleep(0.2)
    assert reads == [1, 1]


def test_a_scripts_time_is_split_into_production_rendering_and_waiting_without_counting_render_steps_twice(db):
    for stage, seconds in (("research", 100), ("write", 50), ("render", 1000), ("render/tts-and-align", 400),
                           ("render/render", 600), ("await-approval", 3600)):
        telemetry.record_span(stage, duration_s=seconds, script_id="s1", column_key="col")
    telemetry.record_call("llm", model="m", usage={"prompt_tokens": 1, "completion_tokens": 1}, script_id="s1")
    [bill] = query.scripts(str(db))["items"]
    assert (bill["produce_seconds"], bill["render_seconds"], bill["wait_seconds"]) == (150.0, 1000.0, 3600.0)


def test_videos_are_compared_by_the_attribute_asked_for(db):
    config = db / "config"
    config.mkdir()
    (config / "pricing.yaml").write_text("prices:\n  - {model: m, input: 1.0, output: 0}\n", encoding="utf-8")
    for script, column, minutes, tokens in (("a", "news", 10, 1_000_000), ("b", "news", 30, 3_000_000),
                                            ("c", "explainer", 5, 500_000)):
        telemetry.record_call("llm", model="m", usage={"prompt_tokens": tokens, "completion_tokens": 0},
                              script_id=script, column_key=column)
        telemetry.record_span("render", duration_s=minutes * 60, script_id=script, column_key=column)
    result = query.compare(str(config), "column_key")
    news, explainer = result["groups"]
    assert (news["value"], news["scripts"], news["tokens_avg"], news["cost_avg_usd"]) == ("news", 2, 2_000_000, 2.0)
    assert news["render_median_s"] == 1200.0 and explainer["render_median_s"] == 300.0
    with pytest.raises(ValueError):
        query.compare(str(config), "script_id")


def test_the_assistants_model_calls_are_recorded_under_the_assistant_stage(db, monkeypatch):
    from web.services import jobs_assistant

    def application_run(message, history, llm, request, user, **kwargs):
        telemetry.record_call("llm", provider="p", model="m", usage={"prompt_tokens": 10, "completion_tokens": 5})
        return {"answer": message}
    monkeypatch.setattr("web.services.assistant_runtime.run", application_run)
    assert jobs_assistant.run("hi", [], None, None, {"role": "admin"}) == {"answer": "hi"}
    [call] = rows("calls")
    assert call["stage"] == "assistant" and call["prompt_tokens"] == 10
    assert [s["stage"] for s in rows("spans")] == ["assistant"] and telemetry.current() == {}


def _assistant_request(request_id, turns, user="admin", tool="query_jobs", answer_chars=5000):
    """What the assistant runtime does for one user message: a model call per turn, a tool asked for, its answer sized."""
    recorder = assistant.Recorder(request_id, "conv1", user)
    messages = []
    with telemetry.track("assistant", request_id=request_id, conversation_id="conv1"):
        for turn in range(turns):
            recorder.settle(messages)
            with telemetry.bind(turn=str(turn)):
                telemetry.record_call("llm", provider="p", model="m", usage={"prompt_tokens": 1000 * (turn + 1), "completion_tokens": 50,
                                                                              "cache_read_tokens": 400 * turn})
            last = turn == turns - 1
            recorder.round(turn, {"message_chars": 4000 * (turn + 1), "tool_schema_chars": 2000, "tool_count": 12, "context_retired": turn},
                           [] if last else [NS(id=f"{request_id}-{turn}", name=tool)])
            if not last:
                messages.append({"role": "tool", "tool_call_id": f"{request_id}-{turn}", "content": "x" * answer_chars})


def test_the_assistant_is_broken_down_by_request_turn_and_tool(db):
    config = db / "config"
    config.mkdir()
    (config / "pricing.yaml").write_text("prices:\n  - {model: m, input: 1.0, output: 0}\n", encoding="utf-8")
    _assistant_request("r1", 3)
    _assistant_request("r2", 2, user="editor", tool="read_application", answer_chars=100)
    telemetry.record_call("llm", model="m", usage={"prompt_tokens": 7, "completion_tokens": 1}, stage="assistant")  # logged before turns were kept
    result = assistant.report(str(config))
    totals = result["totals"]
    assert (totals["requests"], totals["turns"], totals["turns_per_request"], totals["tool_calls"]) == (2, 5, 2.5, 3)
    assert totals["prompt_tokens"] == 6000 + 3000 and totals["earlier_calls"] == 1 and totals["earlier_prompt_tokens"] == 7
    assert totals["cache_rate"] == round((400 + 800 + 400) / 9000, 4) and totals["tool_schema_share"] is not None
    assert totals["cost_usd"] == 0.009
    by_id = {r["request_id"]: r for r in result["requests"]}
    assert by_id["r1"]["turns"] == 3 and by_id["r1"]["tools"] == ["query_jobs", "query_jobs"] and by_id["r1"]["user"] == "admin"
    assert by_id["r1"]["peak_prompt_tokens"] == 3000 and by_id["r2"]["user"] == "editor" and by_id["r1"]["seconds"] >= 0
    tools = {t["name"]: t for t in result["tools"]}
    assert tools["query_jobs"]["calls"] == 2 and tools["query_jobs"]["avg_chars"] == 5000 and tools["query_jobs"]["requests"] == 1
    assert [t["name"] for t in result["tools"]][0] == "query_jobs"  # biggest answers first
    first, second, third = result["turns"]
    assert (first["turn"], first["requests"], first["prompt_tokens_avg"]) == (0, 2, 1000)
    assert (second["prompt_tokens_avg"], third["requests"]) == (2000, 1) and second["message_chars_avg"] == 8000
    detail = assistant.request_detail(str(config), "r1")
    assert [t["turn"] for t in detail["turns"]] == [0, 1, 2]
    assert detail["turns"][0]["tools"] == [{"name": "query_jobs", "chars": 5000}] and detail["turns"][2]["tools"] == []
    assert detail["turns"][1]["prompt_tokens"] == 2000 and detail["turns"][1]["retired"] == 1
    assert assistant.report(str(config), limit=1)["requests"][0]["request_id"] in ("r1", "r2")


def test_assistant_recording_needs_a_request_and_never_fails_the_assistant(db):
    assistant.Recorder("", "c", "u").round(0, {}, [])
    assert rows("assistant_rounds") == []
    telemetry.use_state_dir(None)
    recorder = assistant.Recorder("r9", "c", "u")
    recorder.round(0, {"message_chars": 1}, [NS(id="a", name="t")])  # no state directory: dropped quietly
    recorder.settle([{"role": "tool", "tool_call_id": "a", "content": "abc"}])


def test_databases_made_before_the_assistant_columns_gain_them(db):
    import sqlite3
    from core.telemetry import store
    head, tail = store._SCHEMA.split("CREATE TABLE IF NOT EXISTS assistant_rounds")
    legacy = sqlite3.connect(db / "telemetry.db")
    legacy.executescript(re.sub(r",\n    (request_id|conversation_id|turn) TEXT NOT NULL DEFAULT ''", "", head))
    legacy.execute("INSERT INTO calls (ts, kind) VALUES (1, 'llm')")
    legacy.commit()
    legacy.close()
    telemetry.use_state_dir(str(db))
    telemetry.record_span("assistant", duration_s=1, request_id="r1")
    assert rows("spans")[0]["request_id"] == "r1" and rows("calls")[0]["request_id"] == ""


def test_each_assistant_request_is_shown_with_what_the_user_asked(db):
    import sqlite3
    from web.services import telemetry_assistant
    (db / "config").mkdir()
    with sqlite3.connect(db / "assistant.db") as conn:
        conn.execute("CREATE TABLE requests (id TEXT, message TEXT)")
        conn.execute("INSERT INTO requests VALUES ('r1', ?)", ("帮我看看\n今天的任务  " + "很长" * 100,))
    _assistant_request("r1", 1)
    _assistant_request("r2", 1)
    [r2, r1] = sorted(telemetry_assistant.report(str(db / "config"), str(db), None, None, 25, 0)["requests"],
                      key=lambda r: r["request_id"], reverse=True)
    assert r1["question"].startswith("帮我看看 今天的任务") and len(r1["question"]) == telemetry_assistant.PREVIEW
    assert r2["question"] == ""


def test_a_real_assistant_request_leaves_a_row_per_turn_with_the_size_of_each_tool_answer(client, web_env, monkeypatch):
    from copy import deepcopy
    from core.llm import ChatResponse, ToolCall
    from tests.test_assistant_api import fake_llm, login
    from web.services.assistant_tools import video
    monkeypatch.setattr(video.video, "home_board", lambda **kwargs: deepcopy(
        {"items": [{"script_id": str(i), "title": f"Video {i}"} for i in range(5)], "failed": 0, "stages": {}, "attention": []}))
    fake_llm(monkeypatch, [ChatResponse(tool_calls=[ToolCall(id="call1", name="query_video", arguments={"action": "board"})]),
                           ChatResponse(content="看板正常。")])
    headers = login(client)
    from web.settings import get_settings
    telemetry.use_state_dir(str(get_settings().paths.state_dir))
    reply = client.post("/api/assistant", headers=headers, json={"message": "看板状态", "request_id": "abcdef123456"}).json()
    assert reply["answer"] == "看板正常。"
    first, second = rows("assistant_rounds")
    assert (first["request_id"], first["turn"], first["user"]) == ("abcdef123456", 0, "admin") and first["conversation_id"]
    tools = json.loads(first["tools"])
    assert [t["name"] for t in tools] == ["query_video"] and tools[0]["chars"] > 0
    assert second["turn"] == 1 and json.loads(second["tools"]) == [] and second["message_chars"] > first["message_chars"]
    assert first["tool_count"] > 0 and first["tool_schema_chars"] > 0
    [span] = [s for s in rows("spans") if s["stage"] == "assistant"]
    assert span["request_id"] == "abcdef123456" and span["conversation_id"] == first["conversation_id"]


def _published(bvid, script, views, pubdate=1000, available=True):
    return {"bvid": bvid, "title": bvid, "pubdate": pubdate, "available": available, "metrics": {"view": views},
            "publication": {"script_id": script, "matrix": "m1"}}


def test_cost_is_set_against_plays_per_video_and_per_group(db):
    config = db / "config"
    config.mkdir()
    (config / "pricing.yaml").write_text("prices:\n  - {model: m, input: 1.0, output: 0}\n", encoding="utf-8")
    for script, column, tokens in (("a", "news", 1_000_000), ("b", "news", 3_000_000), ("c", "deep", 2_000_000)):
        telemetry.record_call("llm", model="m", usage={"prompt_tokens": tokens, "completion_tokens": 0},
                              script_id=script, column_key=column)
    telemetry.record_call("llm", model="mystery", usage={"prompt_tokens": 5, "completion_tokens": 0},
                          script_id="c", column_key="deep")
    telemetry.record_call("llm", model="mystery", usage={"prompt_tokens": 9, "completion_tokens": 0},
                          script_id="d", column_key="deep")
    videos = [_published("BV7", "d", 100), _published("BV1", "a", 1000), _published("BV2", "b", 3000, pubdate=2000),
              _published("BV3", "c", 4000), _published("BV4", "zz", 10), _published("BV5", "a", None)]
    videos[4]["available"] = False
    videos.append({"bvid": "BV6", "title": "not ours", "pubdate": 1, "available": True, "metrics": {"view": 5}, "publication": None})
    result = performance.report(str(config), videos)
    by_id = {i["bvid"]: i for i in result["items"]}
    assert "BV6" not in by_id and [i["bvid"] for i in result["items"]][0] == "BV2"
    assert by_id["BV1"]["cost_usd"] == 0.5 and by_id["BV1"]["cost_per_kview"] == 0.5  # script a is shared with BV5
    assert by_id["BV5"]["view"] is None and by_id["BV5"]["cost_per_kview"] is None
    assert by_id["BV3"]["partial"] and by_id["BV3"]["cost_usd"] == 2.0 and by_id["BV3"]["cost_per_kview"] == 0.5  # a lower bound
    assert by_id["BV7"]["cost_usd"] is None and by_id["BV7"]["cost_per_kview"] is None and by_id["BV7"]["tokens_per_kview"] > 0
    assert by_id["BV4"]["billed"] is False and by_id["BV4"]["cost_usd"] is None
    news = next(g for g in result["groups"] if g["value"] == "news")
    assert (news["videos"], news["watched"], news["views"], news["cost_samples"]) == (3, 2, 4000, 2)
    deep = next(g for g in result["groups"] if g["value"] == "deep")
    assert (deep["watched"], deep["cost_samples"], deep["cost_partial"], deep["cost_per_kview"]) == (2, 1, True, 0.5)
    assert news["cost_partial"] is False and news["cost_per_kview"] == 0.875  # (0.5 + 3.0) per 4000 plays; script a is split between two videos
    with pytest.raises(ValueError):
        performance.report(str(config), videos, "script_id")


# --- keeping and removing records --------------------------------------------------------------------------------

DAY = 86400.0


def seed_ages(*days_ago):
    now = time.time()
    for age in days_ago:
        telemetry.record_call("llm", model="m", ts=now - age * DAY, usage={"prompt_tokens": 10, "completion_tokens": 1})
        telemetry.record_span("write", start=now - age * DAY - 5, end=now - age * DAY)
    return now


def test_storage_reports_counts_range_and_size(db):
    assert retention.stats()["calls"] == 0
    now = seed_ages(2, 40)
    report = retention.stats()
    assert (report["calls"], report["spans"]) == (2, 2)
    assert report["oldest"] == pytest.approx(now - 40 * DAY, abs=1) and report["newest"] == pytest.approx(now - 2 * DAY, abs=1)
    assert report["bytes"] > 0
    telemetry.use_state_dir(None)
    assert retention.stats() == {"enabled": False}


def test_a_dry_run_counts_what_would_go_and_a_purge_removes_exactly_that(db):
    seed_ages(2, 40, 400)
    assert retention.purge(30, dry_run=True) | {"cutoff": 0} == {"enabled": True, "cutoff": 0, "dry_run": True, "calls": 2, "spans": 2}
    assert retention.stats()["calls"] == 3
    result = retention.purge(30)
    assert (result["calls"], result["spans"], result["dry_run"]) == (2, 2, False)
    assert [round((time.time() - r["ts"]) / DAY) for r in rows("calls")] == [2]
    assert len(rows("spans")) == 1


def test_a_purge_keeps_what_was_already_read_from_being_read_and_counted_again(db):
    render_files(db / "r1")
    render = {"id": "r1", "status": "COMPLETED", "resolved_video_form": "f", "submitted_at": "2020-01-01T10:00:00+0800",
              "completed_at": "2020-01-01T10:20:00+0800"}
    ingest.ingest_render(render, {"id": "s1"}, str(db / "r1"))
    ingest._mark("render:r1")
    assert retention.purge(30)["calls"] == 2 and rows("calls") == []
    with telemetry.connect() as conn:
        assert conn.execute("SELECT 1 FROM meta WHERE key = 'render:r1'").fetchone()


def test_purging_refuses_to_delete_everything_and_does_nothing_without_a_database(db):
    seed_ages(2)
    with pytest.raises(ValueError):
        retention.purge(0)
    assert retention.stats()["calls"] == 1
    telemetry.use_state_dir(None)
    assert retention.purge(30)["enabled"] is False


def test_steps_read_from_a_render_log_are_dated_by_the_render_so_they_can_be_purged_with_it(db):
    render_files(db / "r1")
    render = {"id": "r1", "status": "COMPLETED", "submitted_at": "2020-01-01T10:00:00+0800",
              "completed_at": "2020-01-01T10:20:00+0800"}
    ingest.ingest_render(render, {"id": "s1"}, str(db / "r1"))
    assert all(r["ts"] and r["ts"] < time.time() - 365 * DAY for r in rows("spans"))
    assert retention.purge(30)["spans"] == 4


def test_a_database_made_before_spans_had_a_time_gains_one_dated_from_the_render(tmp_path):
    import sqlite3
    path = tmp_path / "telemetry.db"
    legacy = re.sub(r"\n    ts REAL,[^\n]*", "", telemetry.store._SCHEMA, count=1)  # the schema before spans had a time
    conn = sqlite3.connect(path)
    conn.executescript(legacy)
    conn.execute("INSERT INTO spans (id, stage, render_id, start, end) VALUES ('a', 'render', 'r1', 100, 200)")
    conn.execute("INSERT INTO spans (id, stage, render_id) VALUES ('b', 'render/package', 'r1')")
    conn.execute("INSERT INTO spans (id, stage) VALUES ('c', 'write')")
    conn.commit()
    conn.close()
    telemetry.use_state_dir(str(tmp_path))
    telemetry.store._ready.discard(str(path))
    got = {r["id"]: r["ts"] for r in rows("spans")}
    assert got["a"] == 200 and got["b"] == 200 and got["c"] > 1_000_000_000


def test_purge_through_the_api_is_audited_and_validated(client, web_env, monkeypatch):
    from tests.test_assistant_api import login
    from core import state
    from web import audit
    from web.settings import get_settings
    state.use_state_dir(str(get_settings().paths.state_dir))
    seed_ages(2, 400)
    logged = []
    monkeypatch.setattr(audit, "log", lambda action, **k: logged.append((action, k)))
    headers = login(client, "admin")
    assert client.post("/api/telemetry/purge", json={"older_than_days": 0}, headers=headers).status_code == 422
    assert client.post("/api/telemetry/purge", json={"older_than_days": "30"}, headers=headers).status_code == 422
    dry = client.post("/api/telemetry/purge", json={"older_than_days": 30, "dry_run": True}, headers=headers).json()
    assert (dry["calls"], dry["dry_run"]) == (1, True) and not [a for a, _ in logged if a == "telemetry_purge"]
    done = client.post("/api/telemetry/purge", json={"older_than_days": 30}, headers=headers).json()
    assert (done["calls"], done["spans"]) == (1, 1)
    [purge] = [k for a, k in logged if a == "telemetry_purge"]
    assert purge["user"] == "admin" and purge["details"] == {"older_than_days": 30, "calls": 1, "spans": 1}
    assert client.get("/api/telemetry/storage").json()["calls"] == 1
    login(client, "editor")
    assert client.post("/api/telemetry/purge", json={"older_than_days": 30}, headers=headers).status_code == 403
