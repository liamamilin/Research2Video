"""Usage and timing records: attribution, the LLM and search hooks, render ingestion, backfill, pricing, the API."""
import json
import threading
from pathlib import Path
import time
from types import SimpleNamespace as NS

import pytest

from core import telemetry
from core.llm import LLMClient, LLMConfig
from core.telemetry import ingest, pricing, query


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


def test_the_api_needs_a_login_and_serves_the_summary_and_price_table(client, web_env):
    from tests.test_assistant_api import login
    assert client.get("/api/telemetry/summary").status_code in (401, 403)
    login(client, "viewer")
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
