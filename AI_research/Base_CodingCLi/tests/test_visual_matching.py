from core.video import visual_matching as match


def need(text, kind="pricing"):
    return {"claims": text, "text": text, "kind": kind}


def candidate(text):
    return {"id": "c1", "text": text, "box": {"height": 300}}


def test_pricing_requires_matching_amount_currency_tier_and_billing_conditions():
    wanted = need("Acme Pro 月付价格是20美元每月。")
    assert match.compatible(wanted, candidate("Acme Pro monthly pricing: $20 per month."), "Acme Pricing")[0]
    for text in ("Acme Pro monthly pricing: $25 per month.", "Acme Pro monthly pricing: €20 per month.",
                 "Acme Basic monthly pricing: $20 per month.", "Acme Pro costs $20 per month, billed annually.",
                 "Acme Pro costs $20 per year.", "OtherTool Pro monthly pricing: $20 per month."):
        assert not match.compatible(wanted, candidate(text), "Pricing")[0], text


def test_pricing_does_not_relax_token_units_or_seat_conditions():
    wanted = need("Acme 输入价格为每百万token 5美元。")
    assert match.compatible(wanted, candidate("Acme input pricing: $5 / 1M tokens."), "Acme")[0]
    assert not match.compatible(wanted, candidate("Acme output pricing: $5 / 1M tokens."), "Acme")[0]
    wanted = need("Acme Pro 每用户每月20美元。")
    assert not match.compatible(wanted, candidate("Acme Pro monthly team plan: $20 per month."), "Acme")[0]


def test_version_and_product_mismatch_are_rejected_even_if_vendor_matches():
    wanted = need("Acme GPT-6 支持网页截图功能。", "feature")
    assert not match.compatible(wanted, candidate("Acme GPT-5 screenshot feature supports web pages."), "Acme")[0]
    assert match.compatible(wanted, candidate("GPT-6 screenshot feature supports web pages."), "Acme GPT-6")[0]


def test_chinese_narration_can_match_english_feature_region_without_inventing_urls():
    wanted = need("Playwright 支持指定元素截图功能。", "feature")
    assert match.compatible(wanted, candidate("Element screenshot: capture a single element of a page."), "Playwright Python")[0]


def test_compact_specific_region_ranks_above_a_long_page():
    wanted = need("Acme 支持元素截图功能。", "feature")
    short = candidate("Acme element screenshot feature captures page content.")
    long = {**candidate(short["text"] + " unrelated site navigation" * 180), "id": "large", "box": {"height": 1300}}
    assert match.rank(wanted, {"title": "Acme", "candidates": [long, short]})[0]["id"] == "c1"
