"""The credibility label: how far a version's words can be trusted, from its checks and paragraph marks."""

from core.video import credibility
from core.video.adapt import review


def version(marks, *, model_ran=True, serious=0, rules=(), length="OK"):
    paragraphs = [{"text": "一句话。", "evidence": m} for m in marks]
    issues = [{"audit": "fact", "rule": "D7", "severity": "serious", "section": 0, "paragraph": 0, "problem": "x",
               "why": "", "suggestion": ""}] * serious
    issues += [review.issue(r, review.MUST, "x", 0, 0) for r in rules]
    checks = review.assemble([i for i in issues if i.get("source") != "model" and i["rule"] != "D7"],
                             {"issues": []} if model_ran else None, {"issues": [i for i in issues if i["rule"] == "D7"]} if model_ran else None,
                             None, [{"paragraphs": paragraphs}])
    return {"sections": [{"title": "t", "paragraphs": paragraphs}], "meta": {"checks": checks},
            "length_check": {"status": length}}


def test_each_tier():
    assert credibility.label(version(["CITED"] * 4))["tier"] == "trusted"
    assert credibility.label(version(["CITED", "UNCITED", "UNCITED", "UNCITED"]))["tier"] == "partial"
    assert credibility.label(version(["CITED"] * 4, model_ran=False))["tier"] == "unaudited"
    assert credibility.label(version(["CITED"] * 4, serious=1))["tier"] == "serious"
    label = credibility.label(version(["CITED", "NEEDS_REVIEW"]))
    assert label["tier"] == "verify" and "第 1 节第 2 段" in " ".join(label["reasons"])
    assert credibility.label(version(["CITED"] * 4, rules=("D5",)))["tier"] == "spoken"
    assert credibility.label(None)["tier"] == "unaudited"


def test_an_unaudited_version_is_never_called_trusted_and_the_reasons_say_why():
    label = credibility.label(version(["CITED"] * 4, model_ran=False, length="SHORT"))
    assert label["tier"] == "unaudited"
    assert any("没有人核对" in r for r in label["reasons"]) and any("长度" in r for r in label["reasons"])
    assert "还在进行" in " ".join(credibility.label(version(["CITED"] * 4, model_ran=False), auditing=True)["reasons"])


def test_the_worst_finding_wins():
    label = credibility.label(version(["CITED"] * 4, serious=1, rules=("D5",), model_ran=True))
    assert label["tier"] == "serious" and len(label["reasons"]) >= 2
