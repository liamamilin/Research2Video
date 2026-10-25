"""Title edits must be based on the final spoken words and cannot replace them."""
import copy

from core.video import headline
from tests.test_video_editorial import Queue


def test_title_selection_uses_final_narration_and_keeps_body_unchanged():
    sections = [{"title": "内部提纲", "paragraphs": [{"text": "断网以后，改动会先存在本机。", "review_reason": "内部审稿意见"}]}]
    before = copy.deepcopy(sections)
    client = Queue({"candidates": [{"title": "断网改的文档，联网后还能找回来", "reason": "改动保存的具体处境",
                                    "quotes": ["断网以后，改动会先存在本机。"]}], "choice": 0, "reason": "比抽象的工作台更具体"})
    result = headline.finish(client, title="给文档补工作台", sections=sections, column_style="普通观众")
    assert result["applied"] and result["title"] == "断网改的文档，联网后还能找回来"
    assert sections == before
    request = client.calls[0][-1]["content"]
    assert "内部审稿意见" not in request and "内部提纲" not in request


def test_title_with_fabricated_evidence_retains_original_and_records_failure():
    client = Queue({"candidates": [{"title": "效率翻倍", "reason": "收益", "quotes": ["实测效率翻倍"]}], "choice": 0, "reason": "收益具体"})
    result = headline.finish(client, title="文档支持离线编辑", sections=[{"paragraphs": [{"text": "支持离线保存。"}]}], column_style="快讯")
    assert not result["applied"] and result["title"] == "文档支持离线编辑" and result["error"]


def test_title_selection_can_keep_original_without_inventing_alternatives():
    client = Queue({"candidates": [], "choice": -1, "reason": "现有标题已兑现看点"})
    result = headline.finish(client, title="文档支持离线编辑", sections=[{"paragraphs": [{"text": "支持离线保存。"}]}], column_style="快讯")
    assert not result["applied"] and "error" not in result
