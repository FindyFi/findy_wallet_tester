"""A tooltip spends its 200 characters on the wallet's words, not on our tags.

A typed failure headline carries its exception class, a `[category]` tag and a `[flow]` tag.
The cell already names the category, so `_detail` drops those before it cuts the text. An
untyped headline is left alone: there the class is often the only signal.
"""
import html

import generate_compact_report as report

TYPED = ("base.outcome.FlowFailure: [rejected] [credential_flow] hovi accepted the offer and "
         "then failed: the issuer said no")


def _title(cell):
    out = report._outcome_td(cell)
    return html.unescape(out.split('title="', 1)[1].split('">', 1)[0])


def _cell(headline):
    category, detail = report.classify_failure(f"E   {headline}\n")
    return {"outcome": "Failed", "reason": category, "detail": detail}


def test_a_typed_headline_loses_its_class_category_and_flow_tag():
    cell = _cell(TYPED)
    assert cell["reason"] == "rejected"
    assert cell["detail"] == "hovi accepted the offer and then failed: the issuer said no"
    assert _title(cell).splitlines()[-1] == cell["detail"]


def test_only_one_flow_tag_is_dropped():
    cell = _cell("FlowFailure: [rejected] [credential_flow] [after consent] said no")
    assert cell["detail"] == "[after consent] said no"
    cell = _cell("FlowFailure: [rejected] [credential_flow] [verification_flow] said no")
    assert cell["detail"] == "[verification_flow] said no"


def test_a_typed_headline_without_a_flow_tag_keeps_the_rest():
    assert _cell("AssertionError: [not_stored] Credential 'x' is missing")["detail"] == \
        "Credential 'x' is missing"


def test_the_strip_comes_before_the_cut_so_the_freed_characters_show_words():
    words = "w" * 300
    cell = _cell(f"FlowFailure: [rejected] [credential_flow] {words}")
    assert cell["detail"] == "w" * (report.DETAIL_MAX - 1) + "…"
    assert len(_title(cell).splitlines()[-1]) == report.DETAIL_MAX


def test_an_untyped_headline_passes_through_unchanged():
    head = "selenium.common.exceptions.TimeoutException: Message: no such element"
    assert _cell(head)["detail"] == head
    head = "RuntimeError: [credential_flow] something broke"
    assert _cell(head)["detail"] == head


def test_an_unknown_bracket_word_is_not_read_as_a_category():
    head = "FlowFailure: [bogus] [credential_flow] text"
    assert _cell(head)["detail"] == head


def test_a_headline_that_is_only_tags_falls_back_to_the_whole_text():
    head = "FlowFailure: [rejected] [credential_flow]"
    assert _cell(head)["detail"] == head


def test_a_summary_json_error_line_is_stripped_the_same_way():
    assert report._detail("FlowFailure: [rejected] [credential_flow] boom [no_retry]") == "boom"


def test_a_step_tag_after_the_category_is_not_read_as_a_flow():
    head = "FlowFailure: [rejected] [after deeplink] error screen"
    assert _cell(head)["detail"] == "[after deeplink] error screen"


def test_no_retry_is_dropped_before_the_cut():
    words = "w" * 190
    assert report._detail(f"FlowFailure: [rejected] [credential_flow] {words} [no_retry]") == words
