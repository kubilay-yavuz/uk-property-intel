"""Tests for the VOA council-tax-band client and HTML parser.

Fixtures under ``tests/fixtures/voa/`` are real responses captured from the
live ``tax.service.gov.uk`` service on 2026-04-18. The tests exercise:

- Pure parser functions (``extract_csrf_token``, ``parse_search_page``, etc.)
  against the saved HTML directly.
- The async ``VOAClient.search_by_postcode`` full flow using ``respx`` to
  replay the GET-search-form → POST-search → GET-paginate sequence.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import ValidationError
from uk_property_apis.voa import CouncilTaxBand, CouncilTaxSearchPage, VOAClient
from uk_property_apis.voa.parser import (
    extract_csrf_token,
    extract_next_postcode_token,
    extract_total_results,
    is_no_results_page,
    parse_results_page,
    parse_search_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "voa"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ── Parser tests (pure, no HTTP) ────────────────────────────────────────────


class TestParser:
    def test_extract_csrf_token(self) -> None:
        html = _read("search_form.html")
        token = extract_csrf_token(html)
        assert token is not None
        assert len(token) > 40  # real tokens are long hex+timestamp+hex strings
        assert re.match(r"^[A-Za-z0-9]+-\d+-[A-Za-z0-9]+$", token) is not None

    def test_extract_csrf_token_missing(self) -> None:
        assert extract_csrf_token("<html><body>no form</body></html>") is None

    def test_is_no_results_page_true(self) -> None:
        assert is_no_results_page(_read("no_results_cb1_2jw.html")) is True

    def test_is_no_results_page_false(self) -> None:
        assert is_no_results_page(_read("results_ec1v_3ap_page0.html")) is False

    def test_extract_total_results(self) -> None:
        html = _read("results_ec1v_3ap_page0.html")
        assert extract_total_results(html) == 47

    def test_extract_total_results_none_when_missing(self) -> None:
        assert extract_total_results("<html><body>no banner</body></html>") is None

    def test_extract_next_postcode_token(self) -> None:
        html = _read("results_ec1v_3ap_page0.html")
        token = extract_next_postcode_token(html)
        assert token == "FHZgUQHx98Z_Vs3AtRYs2A"

    def test_extract_next_postcode_token_none_on_empty_page(self) -> None:
        assert extract_next_postcode_token(_read("no_results_cb1_2jw.html")) is None

    def test_parse_results_page_rows_shape(self) -> None:
        rows = parse_results_page(_read("results_ec1v_3ap_page0.html"))
        assert len(rows) == 20  # one page of results
        first = rows[0]
        assert first.property_id == "74d40278-2544-a339-4930-fdb2e7bdecb3"
        assert first.address == "FLAT 1, 17 PEAR TREE STREET, LONDON, EC1V 3AP"
        assert first.postcode == "EC1V 3AP"
        assert first.band == "F"
        assert first.local_authority == "Islington"
        assert first.local_authority_url == "http://www.islington.gov.uk"

    def test_parse_results_page_no_results_returns_empty(self) -> None:
        rows = parse_results_page(_read("no_results_cb1_2jw.html"))
        assert rows == []

    def test_parse_results_page_missing_table_returns_empty(self) -> None:
        assert parse_results_page("<html><body>no table</body></html>") == []

    def test_parse_results_page_all_bands_valid_letters(self) -> None:
        rows = parse_results_page(_read("results_ec1v_3ap_page0.html"))
        for row in rows:
            assert row.band in {"A", "B", "C", "D", "E", "F", "G", "H", "I"}

    def test_parse_search_page_happy(self) -> None:
        page = parse_search_page(_read("results_ec1v_3ap_page0.html"))
        assert isinstance(page, CouncilTaxSearchPage)
        assert len(page.rows) == 20
        assert page.total_results == 47
        assert page.has_next_page is True
        assert page.next_postcode_token == "FHZgUQHx98Z_Vs3AtRYs2A"

    def test_parse_search_page_no_results(self) -> None:
        page = parse_search_page(_read("no_results_cb1_2jw.html"))
        assert page.rows == []
        assert page.next_postcode_token is None
        assert page.has_next_page is False

    def test_parse_page_1_shape(self) -> None:
        rows = parse_results_page(_read("results_ec1v_3ap_page1.html"))
        assert len(rows) == 20  # page 1 (second 20) before final stub page
        # All rows share the same postcode.
        assert {row.postcode for row in rows} == {"EC1V 3AP"}


# ── Client tests (respx replay of the real flow) ────────────────────────────


def _mock_voa_flow(
    *,
    form_html: str | None = None,
    post_html: str | None = None,
    page1_html: str | None = None,
    post_status: int = 200,
) -> None:
    """Install respx routes covering the full VOA search flow.

    Routes are registered most-specific first because respx matches in
    registration order: the paginated GET (``?postcode=X&page=N``) must be
    registered before the bare GET used to fetch the CSRF form, otherwise
    the latter swallows the former.
    """

    if page1_html is not None:
        respx.get(
            url__regex=r"https://www\.tax\.service\.gov\.uk/check-council-tax-band/search\?.*page=\d",
        ).mock(
            return_value=httpx.Response(
                200, text=page1_html, headers={"content-type": "text/html"}
            )
        )
    respx.get(
        url__regex=r"https://www\.tax\.service\.gov\.uk/check-council-tax-band/search/?$",
    ).mock(
        return_value=httpx.Response(
            200, text=form_html or _read("search_form.html"), headers={"content-type": "text/html"}
        )
    )
    respx.post(url="https://www.tax.service.gov.uk/check-council-tax-band/search").mock(
        return_value=httpx.Response(
            post_status,
            text=post_html or "",
            headers={"content-type": "text/html"} if post_html else {},
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_search_by_postcode_happy() -> None:
    _mock_voa_flow(
        form_html=_read("search_form.html"),
        post_html=_read("results_ec1v_3ap_page0.html"),
        page1_html=_read("results_ec1v_3ap_page1.html"),
    )
    async with VOAClient() as client:
        rows = await client.search_by_postcode("EC1V 3AP", max_pages=2)

    # Page 0 (20 rows) + page 1 (20 rows) = 40 out of 47 total — capped at max_pages=2.
    assert len(rows) == 40
    assert all(isinstance(r, CouncilTaxBand) for r in rows)
    assert rows[0].property_id == "74d40278-2544-a339-4930-fdb2e7bdecb3"
    assert rows[0].band == "F"
    assert rows[0].local_authority == "Islington"


@pytest.mark.asyncio
@respx.mock
async def test_search_by_postcode_no_results_returns_empty() -> None:
    _mock_voa_flow(
        form_html=_read("search_form.html"),
        post_html=_read("no_results_cb1_2jw.html"),
    )
    async with VOAClient() as client:
        rows = await client.search_by_postcode("CB1 2JW")
    assert rows == []


@pytest.mark.asyncio
@respx.mock
async def test_search_scottish_postcode_short_circuits() -> None:
    # No routes registered — if the client actually hits the network respx would assert.
    async with VOAClient() as client:
        rows = await client.search_by_postcode("EH1 1YZ")
    assert rows == []


@pytest.mark.asyncio
async def test_invalid_postcode_raises_validation_error() -> None:
    async with VOAClient() as client:
        with pytest.raises(ValidationError):
            await client.search_by_postcode("NOT-A-POSTCODE")


@pytest.mark.asyncio
@respx.mock
async def test_missing_csrf_token_raises_validation_error() -> None:
    respx.get(url="https://www.tax.service.gov.uk/check-council-tax-band/search").mock(
        return_value=httpx.Response(
            200,
            text="<html><body>no form rendered</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    async with VOAClient() as client:
        with pytest.raises(ValidationError, match="csrfToken"):
            await client.search_by_postcode("EC1V 3AP")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_page_requires_token_past_page_zero() -> None:
    async with VOAClient() as client:
        with pytest.raises(ValidationError, match="postcode_token is required"):
            await client.fetch_page("EC1V 3AP", page=2)


@pytest.mark.asyncio
@respx.mock
async def test_single_page_no_next_link_stops_paging() -> None:
    # One-page results (no pagination controls) — client should not attempt
    # to follow a next-page link.
    fake_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    tiny_results = (
        '<html><head><title>Search results for SW1A 1AA</title></head><body>'
        '<h1 class="govuk-heading-l">Search results for SW1A 1AA</h1>'
        '<table class="govuk-table" id="search-results-table">'
        "<thead></thead><tbody>"
        '<tr class="govuk-table__row">'
        '<td class="govuk-table__cell">'
        f'<a href="/check-council-tax-band/property/{fake_id}" class="govuk-link" '
        'title="Property details for 1 MAIN, LONDON, SW1A 1AA (Band A)">1 MAIN, LONDON, SW1A 1AA</a>'
        "</td>"
        '<td class="govuk-table__cell">A</td>'
        '<td class="govuk-table__cell"><a href="http://example.gov.uk">Westminster</a></td>'
        "</tr>"
        "</tbody></table></body></html>"
    )
    _mock_voa_flow(post_html=tiny_results)
    async with VOAClient() as client:
        rows = await client.search_by_postcode("SW1A 1AA")
    assert len(rows) == 1
    assert rows[0].postcode == "SW1A 1AA"
    assert rows[0].band == "A"
    assert rows[0].property_id == fake_id
