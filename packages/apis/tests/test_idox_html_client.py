"""End-to-end tests for ``HTMLPlanningClient`` using ``respx`` fixtures.

These tests replay the real HTML transport handshake (form GET →
firstPage POST → pagedSearchResults GET → applicationDetails GET) with
the captured Lambeth fixtures so the client can be validated without
touching a live council portal.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import ValidationError
from uk_property_apis.idox import (
    KNOWN_COUNCILS,
    HTMLPlanningClient,
    TooManyResultsError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "idox"
LAMBETH = KNOWN_COUNCILS["lambeth"]

FORM_URL = "https://planning.lambeth.gov.uk/online-applications/search.do"
FIRST_PAGE_URL = "https://planning.lambeth.gov.uk/online-applications/simpleSearchResults.do"
PAGED_URL = "https://planning.lambeth.gov.uk/online-applications/pagedSearchResults.do"
DETAIL_URL = "https://planning.lambeth.gov.uk/online-applications/applicationDetails.do"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _form_data_from_request(request: httpx.Request) -> dict[str, str]:
    """Decode a captured form-encoded POST body."""

    body = request.content.decode("utf-8")
    return dict(parse_qsl(body, keep_blank_values=True))


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


# ── Page fetches ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_first_page_uses_csrf_and_parses_results() -> None:
    form_html = _html("lambeth_search_form.html")
    results_html = _html("lambeth_results_acre_lane_page1.html")
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            form_route = respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=form_html)
            )
            first_route = respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(200, html=results_html)
            )
            page = await client.fetch_first_page("Acre Lane")

        assert form_route.call_count == 1
        assert _query(str(form_route.calls.last.request.url)) == {
            "action": "simple",
            "searchType": "Application",
        }
        post_request = first_route.calls.last.request
        assert _query(str(post_request.url)) == {"action": "firstPage"}
        body = _form_data_from_request(post_request)
        assert body["_csrf"] == "5207cd64-572a-4dae-aa01-1dc12d00bb99"
        assert body["searchCriteria.simpleSearchString"] == "Acre Lane"
        assert body["searchCriteria.simpleSearch"] == "true"
        assert body["searchType"] == "Application"

        assert page.current_page == 1
        assert len(page.applications) == 10
        assert page.applications[0].reference == "26/00115/VOC"


@pytest.mark.asyncio
async def test_fetch_first_page_rejects_empty_query() -> None:
    async with HTMLPlanningClient(LAMBETH) as client, respx.mock():
        with pytest.raises(ValidationError, match="non-empty"):
            await client.fetch_first_page("   ")


@pytest.mark.asyncio
async def test_fetch_first_page_raises_when_form_missing_csrf() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(
                    200,
                    html="<html><body>no form here</body></html>",
                )
            )
            with pytest.raises(ValidationError, match="_csrf"):
                await client.fetch_first_page("Acre Lane")


@pytest.mark.asyncio
async def test_too_many_results_propagates_as_typed_exception() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_too_many.html"),
                )
            )
            with pytest.raises(TooManyResultsError):
                await client.fetch_first_page("London")


@pytest.mark.asyncio
async def test_no_results_short_circuits_without_error() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_no_results.html"),
                )
            )
            page = await client.fetch_first_page("ZZZZZZ")
        assert page.applications == []
        assert page.total_results == 0


# ── Pagination ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_paginates_across_two_pages() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page1.html"),
                )
            )
            paged_route = respx_mock.get(PAGED_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page2.html"),
                )
            )
            results = await client.search("Acre Lane", max_pages=2)

        assert len(results) == 20
        refs = [app.reference for app in results]
        assert refs[0] == "26/00115/VOC"
        # Distinct keyVals across both pages.
        assert len({app.key_val for app in results}) == 20

        paged_params = _query(str(paged_route.calls.last.request.url))
        assert paged_params["action"] == "page"
        assert paged_params["searchCriteria.page"] == "2"


@pytest.mark.asyncio
async def test_max_results_caps_pagination_early() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page1.html"),
                )
            )
            # Should stop before hitting the paged route — don't register it.
            results = await client.search("Acre Lane", max_pages=5, max_results=7)
        assert len(results) == 7


@pytest.mark.asyncio
async def test_search_stops_when_next_page_empty() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page1.html"),
                )
            )
            # page 2 returns empty — iterator should terminate cleanly.
            empty_page2 = _html("lambeth_results_no_results.html")
            respx_mock.get(PAGED_URL).mock(
                return_value=httpx.Response(200, html=empty_page2)
            )
            results = await client.search("Acre Lane", max_pages=10)
        assert len(results) == 10


@pytest.mark.asyncio
async def test_csrf_is_cached_across_calls() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            form_route = respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page1.html"),
                )
            )
            await client.fetch_first_page("Acre Lane")
            await client.fetch_first_page("Acre Lane")
        # Form fetched once even though POSTed twice.
        assert form_route.call_count == 1


# ── Ergonomic lookups ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_reference_returns_exact_match() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page1.html"),
                )
            )
            match = await client.get_by_reference("26/00115/VOC")
        assert match is not None
        assert match.reference == "26/00115/VOC"
        assert match.key_val == "T8V6IZBOGCP00"


@pytest.mark.asyncio
async def test_get_by_reference_returns_none_for_no_results() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_no_results.html"),
                )
            )
            match = await client.get_by_reference("99/99999/ZZZ")
        assert match is None


@pytest.mark.asyncio
async def test_get_by_key_val_parses_detail_page() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            detail_route = respx_mock.get(DETAIL_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_detail_26_00115_voc.html"),
                )
            )
            detail = await client.get_by_key_val("T8V6IZBOGCP00")

        params = _query(str(detail_route.calls.last.request.url))
        assert params == {"activeTab": "summary", "keyVal": "T8V6IZBOGCP00"}
        assert detail.reference == "26/00115/VOC"
        assert detail.document_count == 10
        assert detail.status == "Awaiting decision"


@pytest.mark.asyncio
async def test_get_by_key_val_rejects_empty() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with pytest.raises(ValidationError, match="non-empty"):
            await client.get_by_key_val("   ")


@pytest.mark.asyncio
async def test_get_detail_by_reference_chains_search_and_detail() -> None:
    async with HTMLPlanningClient(LAMBETH) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(FORM_URL).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(FIRST_PAGE_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_acre_lane_page1.html"),
                )
            )
            respx_mock.get(DETAIL_URL).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_detail_26_00115_voc.html"),
                )
            )
            detail = await client.get_detail_by_reference("26/00115/VOC")
        assert detail is not None
        assert detail.reference == "26/00115/VOC"
        assert detail.document_count == 10


# ── Misc ─────────────────────────────────────────────────────────────


def test_council_property_exposes_config() -> None:
    client = HTMLPlanningClient(LAMBETH)
    assert client.council.slug == "lambeth"
    # Ensure we didn't accidentally swap the role of ArcGIS URL.
    assert "lambeth.gov.uk" in client.council.public_access_base_url


@pytest.mark.asyncio
async def test_client_works_without_arcgis_configured() -> None:
    # Westminster has no arcgis_base_url — HTML transport must still work.
    westminster = KNOWN_COUNCILS["westminster"]
    assert westminster.arcgis_base_url is None
    form_url = f"{westminster.public_access_base_url}/online-applications/search.do"
    first_url = (
        f"{westminster.public_access_base_url}/online-applications/"
        "simpleSearchResults.do"
    )
    async with HTMLPlanningClient(westminster) as client:
        with respx.mock(assert_all_called=True) as respx_mock:
            respx_mock.get(form_url).mock(
                return_value=httpx.Response(200, html=_html("lambeth_search_form.html"))
            )
            respx_mock.post(first_url).mock(
                return_value=httpx.Response(
                    200,
                    html=_html("lambeth_results_no_results.html"),
                )
            )
            page = await client.fetch_first_page("anything")
        assert page.applications == []
        assert page.total_results == 0
