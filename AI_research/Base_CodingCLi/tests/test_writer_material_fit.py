"""The writer's original material stays inside the model window (the 11-07 weekly deep dive overflowed it)."""
from web.services import video_workflow
from web.services.video_workflow import MATERIAL_SEPARATOR as SEP, fit_material


def _block(n: int, size: int = 100) -> str:
    return f"### [{n}] https://example.com/a{n}\n" + "字" * size


def test_material_that_fits_is_sent_unchanged():
    material = SEP.join(_block(i) for i in range(5))
    assert fit_material("报告 https://example.com/a1", material, limit=10_000) == (material, {})


def test_oversized_material_keeps_only_the_blocks_the_report_cites_in_order():
    material = SEP.join(_block(i) for i in range(50))
    report = "报告引用 https://example.com/a7 和 https://example.com/a3"
    kept, fit = fit_material(report, material, limit=1_000)
    assert kept == SEP.join([_block(3), _block(7)])
    assert fit["blocks"] == 50 and fit["cited"] == 2 and fit["kept"] == 2


def test_cited_blocks_that_still_overflow_are_taken_in_order_until_full():
    material = SEP.join(_block(i, 400) for i in range(10))
    report = " ".join(f"https://example.com/a{i}" for i in range(10))
    kept, fit = fit_material(report, material, limit=len(report) + 1_000)
    assert len(report) + len(kept) <= len(report) + 1_000
    assert fit["cited"] == 10 and 0 < fit["kept"] < 10 and kept.startswith(_block(0, 400))


def test_the_default_limit_is_the_research_window():
    from core import research_batches
    big = "x" * (research_batches.MAX_PROMPT_CHARS + 10)
    kept, fit = fit_material("", big)
    assert kept == "" and fit["cited"] == 0
    assert video_workflow.fit_material("", "small") == ("small", {})
