import copy
import urllib.error
import pytest

import g2.run_g2 as g2
from g4.qc import G4QCError, _check_budget, check_case_semantics


def test_model_call_budget_rejects_before_network_or_artifact_write(tmp_path, monkeypatch):
    monkeypatch.setattr(g2, "CALL_RECORDS", [{"call_id": str(i)} for i in range(g2.MAX_TOTAL_CALLS)])
    monkeypatch.setattr(g2, "CALLS_DIR", tmp_path / "calls")
    with pytest.raises(g2.G2Fault, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        g2.deepseek_json_call("narration", [], {}, 1)
    assert not (tmp_path / "calls").exists()


def test_transport_failure_consumes_call_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(g2, "PROJECT", tmp_path)
    monkeypatch.setattr(g2, "CALLS_DIR", tmp_path / "calls")
    monkeypatch.setattr(g2, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(g2, "CALL_RECORDS", [])
    monkeypatch.setattr(g2, "MAX_TOTAL_CALLS", 1)
    monkeypatch.setattr(g2, "load_deepseek_api_key", lambda _: "test-key")
    monkeypatch.setattr(g2.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    with pytest.raises(g2.G2Fault, match="DEEPSEEK_UNAVAILABLE"):
        g2.deepseek_json_call("narration", [], {}, 1)
    assert len(g2.CALL_RECORDS) == 1
    assert g2.CALL_RECORDS[0]["status"] == "failed"
    with pytest.raises(g2.G2Fault, match="MODEL_CALL_BUDGET_EXHAUSTED"):
        g2.deepseek_json_call("narration", [], {}, 2)


def test_budget_checker_rejects_cost_above_documented_cap():
    with pytest.raises(G4QCError, match="exceeds"):
        _check_budget({"calls": [{}, {}], "max_calls_per_run": 7,
                       "peak_uncached_cost_upper_bound_usd": 0.10,
                       "configured_worst_case_budget_usd": 0.09})


def test_case_b_requires_real_two_sided_comparison():
    storyboard = {"scenes": [
        {"component": "title_card", "props": {}},
        {"component": "comparison", "props": {
            "left": {"label": "批处理", "items": ["先积累", "再集中处理"]},
            "right": {"label": "流处理", "items": ["数据到达", "持续处理"]}}},
        {"component": "concept_reveal", "props": {}}]}
    ir = {"scenes": [{}, {}, {}]}
    check_case_semantics("case-b", "批处理与流处理", storyboard, ir, {}, "")
    broken = copy.deepcopy(storyboard)
    broken["scenes"][1]["props"]["right"]["label"] = "批处理"
    broken["scenes"][1]["props"]["right"]["items"] = ["先积累", "再集中处理"]
    with pytest.raises(G4QCError, match="both batch and stream"):
        check_case_semantics("case-b", "批处理与流处理", broken, ir, {}, "")


def test_case_c_requires_exact_zero_based_fictional_data():
    source = "全部为虚构演示数据"
    expected = {"scenes": [
        {"component": "title_card", "props": {}},
        {"component": "bar_chart", "props": {
            "categories": ["蓝色纸飞机", "黄色纸飞机", "红色纸飞机"],
            "values": [2, 4, 6], "unit": "米", "baseline_zero": True}},
        {"component": "concept_reveal", "props": {}}]}
    ir = {"scenes": [{}, {}, {}]}
    check_case_semantics("case-c", source, expected, ir, {}, "虚构演示数据")
    wrong = copy.deepcopy(expected)
    wrong["scenes"][1]["props"]["values"][2] = 5
    with pytest.raises(G4QCError, match="differs from its fixed source data"):
        check_case_semantics("case-c", source, wrong, ir, {}, "虚构演示数据")
