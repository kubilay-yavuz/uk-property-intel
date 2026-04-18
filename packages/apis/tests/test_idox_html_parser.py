"""Tests for the IDOX HTML parser.

Fixtures under ``tests/fixtures/idox/`` are real HTML responses captured
from Lambeth Public Access on 2026-04-18:

* ``lambeth_search_form.html`` — initial GET of the simple-search form.
* ``lambeth_results_acre_lane_page1.html`` / ``…_page2.html`` — POST of
  the 'Acre Lane' query + the next paged GET.
* ``lambeth_results_no_results.html`` — empty-state response.
* ``lambeth_results_too_many.html`` — 'Too many results' refusal.
* ``lambeth_detail_26_00115_voc.html`` — applicationDetails.do summary
  tab for planning reference 26/00115/VOC (Lidl / Acre Lane).

All tests operate on the fixture strings directly; no HTTP calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from uk_property_apis.idox import KNOWN_COUNCILS
from uk_property_apis.idox.html_parser import (
    TooManyResultsError,
    all_keyvals_on_page,
    extract_csrf_token,
    is_no_results_page,
    is_too_many_results_page,
    next_page_number,
    paged_search_params,
    parse_detail_page,
    parse_results_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "idox"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


LAMBETH = KNOWN_COUNCILS["lambeth"]


# ── Message-box states ─────────────────────────────────────────────────


class TestMessageBoxes:
    def test_no_results_detected(self) -> None:
        html = _html("lambeth_results_no_results.html")
        assert is_no_results_page(html) is True
        assert is_too_many_results_page(html) is False

    def test_too_many_detected(self) -> None:
        html = _html("lambeth_results_too_many.html")
        assert is_too_many_results_page(html) is True
        assert is_no_results_page(html) is False

    def test_valid_results_page_is_neither(self) -> None:
        html = _html("lambeth_results_acre_lane_page1.html")
        assert is_no_results_page(html) is False
        assert is_too_many_results_page(html) is False

    def test_search_form_is_neither(self) -> None:
        # The search form carries a welcome ``messagebox announcement``;
        # parsers must skip it so the form page isn't misclassified.
        html = _html("lambeth_search_form.html")
        assert is_no_results_page(html) is False
        assert is_too_many_results_page(html) is False


# ── CSRF token extraction ──────────────────────────────────────────────


class TestCSRF:
    def test_token_from_simple_search_form(self) -> None:
        html = _html("lambeth_search_form.html")
        assert extract_csrf_token(html) == "5207cd64-572a-4dae-aa01-1dc12d00bb99"

    def test_missing_form_returns_none(self) -> None:
        assert extract_csrf_token("<html><body>no form here</body></html>") is None


# ── Results-page parser ────────────────────────────────────────────────


class TestParseResultsPage:
    def test_parses_ten_applications_with_all_fields(self) -> None:
        html = _html("lambeth_results_acre_lane_page1.html")
        page = parse_results_page(html, council=LAMBETH)
        assert len(page.applications) == 10
        assert page.current_page == 1
        assert page.total_results == 960

        first = page.applications[0]
        assert first.council == "lambeth"
        assert first.reference == "26/00115/VOC"
        assert first.key_val == "T8V6IZBOGCP00"
        assert first.address == "71-73 Lidl Store Acre Lane London Lambeth SW2 5TN"
        assert "Variation of Condition 5" in first.description
        assert first.status == "Awaiting decision"
        assert first.received_date == date(2026, 1, 14)
        assert first.validated_date == date(2026, 1, 15)
        assert first.detail_url == (
            "https://planning.lambeth.gov.uk/online-applications/"
            "applicationDetails.do?activeTab=summary&keyVal=T8V6IZBOGCP00"
        )

    def test_applications_are_unique_by_key_val(self) -> None:
        # Ensures we aren't accidentally double-counting the ``keyVals``
        # hidden input (which also contains every keyVal on the page).
        html = _html("lambeth_results_acre_lane_page1.html")
        page = parse_results_page(html, council=LAMBETH)
        key_vals = [app.key_val for app in page.applications]
        assert len(set(key_vals)) == len(key_vals)

    def test_page2_is_detected_as_current_page_2(self) -> None:
        html = _html("lambeth_results_acre_lane_page2.html")
        page = parse_results_page(html, council=LAMBETH)
        assert page.current_page == 2
        assert len(page.applications) == 10
        assert page.total_results == 960

    def test_too_many_raises(self) -> None:
        html = _html("lambeth_results_too_many.html")
        with pytest.raises(TooManyResultsError):
            parse_results_page(html, council=LAMBETH)

    def test_no_results_returns_empty_page(self) -> None:
        html = _html("lambeth_results_no_results.html")
        page = parse_results_page(html, council=LAMBETH)
        assert page.applications == []
        assert page.total_results == 0
        assert page.current_page == 1


class TestNextPage:
    def test_next_page_from_page1(self) -> None:
        html = _html("lambeth_results_acre_lane_page1.html")
        assert next_page_number(html) == 2

    def test_next_page_from_page2(self) -> None:
        html = _html("lambeth_results_acre_lane_page2.html")
        # Page 2 of 96 — pager advertises pages 3+ so next is 3.
        assert next_page_number(html) == 3

    def test_no_pager_no_next(self) -> None:
        html = "<html><body><ul id='searchresults'></ul></body></html>"
        assert next_page_number(html) is None


class TestAllKeyVals:
    def test_returns_ten_ordered_keyvals(self) -> None:
        html = _html("lambeth_results_acre_lane_page1.html")
        keyvals = all_keyvals_on_page(html)
        # Page advertises all 10 applications as well as shortcut links
        # for map and print, which reuse the same keyVals; parser must
        # de-duplicate.
        expected_prefix = [
            "T8V6IZBOGCP00",
            "T6QSJMBOL5Z00",
            "T4XWFLBOIEY00",
            "T4XWFVBOIEZ00",
            "T3RB3OBOGUI00",
            "T2Y7W0BOFUL00",
            "T2KERDBON2O00",
            "SYQ24WBOIMS00",
            "SWXH9VBOGL100",
            "SWXH9WBOGL200",
        ]
        assert keyvals[:10] == expected_prefix


class TestPagedSearchParams:
    def test_extracts_page_param(self) -> None:
        next_url = "/online-applications/pagedSearchResults.do?action=page&searchCriteria.page=7"
        params = paged_search_params(next_url)
        assert params["action"] == "page"
        assert params["searchCriteria.page"] == "7"


# ── Detail-page parser ─────────────────────────────────────────────────


class TestParseDetailPage:
    def test_summary_tab_parses_expected_fields(self) -> None:
        html = _html("lambeth_detail_26_00115_voc.html")
        detail = parse_detail_page(
            html,
            council=LAMBETH,
            key_val="T8V6IZBOGCP00",
        )
        assert detail.council == "lambeth"
        assert detail.reference == "26/00115/VOC"
        assert detail.key_val == "T8V6IZBOGCP00"
        assert detail.address == "71-73 Lidl Store Acre Lane London Lambeth SW2 5TN"
        assert detail.received_date == date(2026, 1, 14)
        assert detail.validated_date == date(2026, 1, 15)
        assert detail.status == "Awaiting decision"
        # 'Unknown' / 'Not Available' get collapsed to None.
        assert detail.appeal_status is None
        assert detail.appeal_decision is None
        # Associated-info advertises document/case/property counts.
        assert detail.document_count == 10
        assert detail.related_case_count == 1
        assert detail.related_property_count == 1
        # Proposal (richer than the list-page description) is preserved.
        assert detail.proposal is not None
        assert "Variation of Condition 5" in detail.proposal

    def test_recovers_keyval_when_not_supplied(self) -> None:
        html = _html("lambeth_detail_26_00115_voc.html")
        detail = parse_detail_page(html, council=LAMBETH)
        assert detail.key_val == "T8V6IZBOGCP00"

    def test_missing_keyval_raises(self) -> None:
        # Stripped HTML with the table gone: parser should refuse.
        html = (
            "<html><body><table id='simpleDetailsTable'><tr>"
            "<th scope='row'>Reference</th><td>XX/0001</td>"
            "</tr></table></body></html>"
        )
        with pytest.raises(ValueError, match="keyVal"):
            parse_detail_page(html, council=LAMBETH)
