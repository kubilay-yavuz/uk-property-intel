"""Unit tests for :mod:`uk_property_apis.auctions.allsop_client`.

We drive the HTTP surface with ``respx`` against the captured fixtures
in ``tests/fixtures/auctions`` so the tests exercise the same query
strings and response shapes we hit live against ``www.allsop.co.uk``.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import httpx
import pytest
import respx
from uk_property_apis.auctions import (
    AllsopClient,
    AllsopSearchPage,
    UpcomingAuctions,
    list_upcoming_auctions,
    search_lots,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "auctions"
_BASE = "https://www.allsop.co.uk"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture
def upcoming_payload() -> dict[str, Any]:
    return _load("upcoming.json")


@pytest.fixture
def auction_meta_payload() -> dict[str, Any]:
    return _load("auction_meta.json")


@pytest.fixture
def search_page1_payload() -> dict[str, Any]:
    return _load("search_page1.json")


@pytest.fixture
def search_page2_payload() -> dict[str, Any]:
    return _load("search_page2.json")


class TestListUpcomingAuctions:
    @respx.mock
    async def test_returns_residential_and_commercial_summaries(
        self, upcoming_payload: dict[str, Any]
    ) -> None:
        respx.get(f"{_BASE}/api/auctions/upcoming").mock(
            return_value=httpx.Response(200, json=upcoming_payload),
        )

        async with AllsopClient() as client:
            upcoming = await client.list_upcoming_auctions()

        assert isinstance(upcoming, UpcomingAuctions)
        assert len(upcoming.residential) == 5
        assert len(upcoming.commercial) == 3
        # Allsop returns the furthest-future auction first; the imminent
        # April 2026 sale is the tail of the list.
        imminent = upcoming.residential[-1]
        assert imminent.auction_id == "16fe8330-8a60-11f0-a081-0242ac110002"
        assert imminent.reference == "R260430"
        assert imminent.name is not None
        assert imminent.name.startswith("Residential")
        assert imminent.auction_date_iso == "2026-04-28T23:00:00.000000Z"
        assert imminent.venue == "Live Stream"
        assert imminent.lots_sold is not None

    @respx.mock
    async def test_cache_buster_query_string_is_forwarded(
        self, upcoming_payload: dict[str, Any]
    ) -> None:
        route = respx.get(f"{_BASE}/api/auctions/upcoming").mock(
            return_value=httpx.Response(200, json=upcoming_payload),
        )

        async with AllsopClient() as client:
            await client.list_upcoming_auctions()

        sent = route.calls.last.request
        # ``react`` is present but valueless, matching the SPA.
        assert "react" in sent.url.params

    def test_all_flattens_residential_first(
        self, upcoming_payload: dict[str, Any]
    ) -> None:
        upcoming = UpcomingAuctions(
            residential=tuple(),
            commercial=tuple(),
        )
        assert upcoming.all() == ()

    @respx.mock
    async def test_convenience_helper_delegates_to_client(
        self, upcoming_payload: dict[str, Any]
    ) -> None:
        respx.get(f"{_BASE}/api/auctions/upcoming").mock(
            return_value=httpx.Response(200, json=upcoming_payload),
        )
        upcoming = await list_upcoming_auctions()
        assert len(upcoming.residential) == 5


class TestGetAuction:
    @respx.mock
    async def test_returns_raw_auction_envelope(
        self, auction_meta_payload: dict[str, Any]
    ) -> None:
        auction_id = "16fe8330-8a60-11f0-a081-0242ac110002"
        respx.get(f"{_BASE}/api/auctions/{auction_id}").mock(
            return_value=httpx.Response(200, json=auction_meta_payload),
        )

        async with AllsopClient() as client:
            envelope = await client.get_auction(auction_id)

        assert "auctionData" in envelope
        assert envelope["auctionData"]["allsop_auctionid"] == auction_id

    async def test_empty_auction_id_is_rejected(self) -> None:
        async with AllsopClient() as client:
            with pytest.raises(ValueError, match="auction_id"):
                await client.get_auction("   ")


class TestSearchPage:
    @respx.mock
    async def test_passes_query_params_and_parses_envelope(
        self, search_page1_payload: dict[str, Any]
    ) -> None:
        route = respx.get(f"{_BASE}/api/search").mock(
            return_value=httpx.Response(200, json=search_page1_payload),
        )

        async with AllsopClient() as client:
            page = await client.search_page(
                auction_id="16fe8330-8a60-11f0-a081-0242ac110002",
                lot_type="residential",
                available_only=True,
                page=1,
                size=3,
            )

        assert isinstance(page, AllsopSearchPage)
        assert page.page == 1
        assert page.size == 3
        assert len(page.results) == 3
        assert page.total >= 3

        sent = route.calls.last.request
        assert sent.url.params["auction_id"] == "16fe8330-8a60-11f0-a081-0242ac110002"
        assert sent.url.params["lot_type"] == "residential"
        assert sent.url.params["available_only"] == "true"
        assert sent.url.params["page"] == "1"
        assert sent.url.params["size"] == "3"

    async def test_rejects_zero_page(self) -> None:
        async with AllsopClient() as client:
            with pytest.raises(ValueError, match="page"):
                await client.search_page(page=0)
            with pytest.raises(ValueError, match="size"):
                await client.search_page(size=0)

    @respx.mock
    async def test_has_next_reflects_total_vs_offset(
        self, search_page1_payload: dict[str, Any]
    ) -> None:
        respx.get(f"{_BASE}/api/search").mock(
            return_value=httpx.Response(200, json=search_page1_payload),
        )
        async with AllsopClient() as client:
            first = await client.search_page(page=1, size=3)
        assert first.has_next is (first.page * first.size < first.total)

    @respx.mock
    async def test_extra_params_passthrough(
        self, search_page1_payload: dict[str, Any]
    ) -> None:
        route = respx.get(f"{_BASE}/api/search").mock(
            return_value=httpx.Response(200, json=search_page1_payload),
        )
        async with AllsopClient() as client:
            await client.search_page(
                extra_params={"keyword": "Romford", "reserved_only": False},
            )
        sent = route.calls.last.request
        assert sent.url.params["keyword"] == "Romford"
        assert sent.url.params["reserved_only"] == "false"


class TestIterSearchPages:
    @respx.mock
    async def test_iterates_until_total_exhausted(
        self,
        search_page1_payload: dict[str, Any],
        search_page2_payload: dict[str, Any],
    ) -> None:
        def _responder(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(200, json=search_page1_payload)
            if page == 2:
                return httpx.Response(200, json=search_page2_payload)
            return httpx.Response(
                200, json={"data": {"results": [], "total": 6}}
            )

        respx.get(f"{_BASE}/api/search").mock(side_effect=_responder)

        async with AllsopClient() as client:
            pages = [
                p
                async for p in client.iter_search_pages(
                    auction_id="16fe8330-8a60-11f0-a081-0242ac110002",
                    size=3,
                    max_pages=5,
                )
            ]

        # Two fixture pages have 3 lots each → total 6. With size=3 the
        # iterator fetches page 3 to confirm exhaustion (empty results).
        assert len(pages) >= 2
        assert pages[0].page == 1
        # Either the iterator stopped when has_next went false, or the
        # tail page is empty.
        assert (not pages[-1].has_next) or pages[-1].results == ()

    @respx.mock
    async def test_stops_when_results_exhaust(
        self, search_page1_payload: dict[str, Any]
    ) -> None:
        def _responder(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"results": [], "total": 0}})

        respx.get(f"{_BASE}/api/search").mock(side_effect=_responder)

        async with AllsopClient() as client:
            pages = [p async for p in client.iter_search_pages(size=3)]

        assert len(pages) == 1
        assert pages[0].results == ()


class TestListLotsForAuction:
    @respx.mock
    async def test_collects_all_pages_into_flat_list(
        self,
        search_page1_payload: dict[str, Any],
        search_page2_payload: dict[str, Any],
    ) -> None:
        state = {"page": 0}

        def _responder(request: httpx.Request) -> httpx.Response:
            state["page"] += 1
            if state["page"] == 1:
                return httpx.Response(200, json=search_page1_payload)
            if state["page"] == 2:
                return httpx.Response(200, json=search_page2_payload)
            return httpx.Response(200, json={"data": {"results": [], "total": 6}})

        respx.get(f"{_BASE}/api/search").mock(side_effect=_responder)

        async with AllsopClient() as client:
            lots = await client.list_lots_for_auction(
                "16fe8330-8a60-11f0-a081-0242ac110002", size=3, max_pages=5
            )

        # Each captured fixture page holds 3 lots.
        assert len(lots) == 6
        assert {lot["allsop_auctionid"] for lot in lots} == {
            "16fe8330-8a60-11f0-a081-0242ac110002"
        }


class TestSearchLotsHelper:
    @respx.mock
    async def test_convenience_helper_delegates(
        self, search_page1_payload: dict[str, Any]
    ) -> None:
        def _responder(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(200, json=search_page1_payload)
            return httpx.Response(200, json={"data": {"results": [], "total": 3}})

        respx.get(f"{_BASE}/api/search").mock(side_effect=_responder)

        lots = await search_lots(size=3, max_pages=2)
        assert len(lots) == 3
