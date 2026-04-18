"""Site helpers: crawl_zoopla_search / crawl_rightmove_search / crawl_onthemarket_search.

Drives the pagination loop with :class:`SimpleCrawler` against respx-mocked
HTTP and asserts the per-site parsers receive the right HTML and produce
``Listing`` instances.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import respx
from uk_property_listings import (
    SearchQuery,
    SimpleCrawler,
    crawl_onthemarket_search,
    crawl_rightmove_search,
    crawl_zoopla_search,
)
from uk_property_scrapers.schema import Source

SCRAPER_FIXTURES = Path(__file__).resolve().parents[2] / "scrapers" / "tests" / "fixtures"


def _read(path: str) -> str:
    return (SCRAPER_FIXTURES / path).read_text()


class TestCrawlZooplaSearch:
    @respx.mock
    async def test_single_page_returns_listings(self) -> None:
        async with SimpleCrawler() as crawler:
            html = _read("zoopla/search_cambridgeshire_2026-04.html")
            respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
                side_effect=[
                    httpx.Response(200, html=html),
                    httpx.Response(200, html=html),
                ]
            )
            report = await crawl_zoopla_search(
                crawler,
                SearchQuery(location="Cambridge", transaction="sale", max_pages=1),
            )
            assert report.source is Source.ZOOPLA
            assert report.pages_fetched == 1
            assert len(report.listings) >= 10
            assert all(listing.source is Source.ZOOPLA for listing in report.listings)

    @respx.mock
    async def test_pagination_stops_when_no_new_listings(self) -> None:
        async with SimpleCrawler() as crawler:
            html = _read("zoopla/search_cambridgeshire_2026-04.html")
            route = respx.get(
                url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*"
            ).mock(return_value=httpx.Response(200, html=html))
            report = await crawl_zoopla_search(
                crawler,
                SearchQuery(location="Cambridge", transaction="sale", max_pages=5),
            )
            assert report.pages_fetched == 2
            assert route.call_count == 2


class TestCrawlRightmoveSearch:
    @respx.mock
    async def test_single_page_returns_sale_listings(self) -> None:
        async with SimpleCrawler() as crawler:
            html = _read("rightmove/search_cambridge_2026-04.html")
            respx.get(url__regex=r"https://www\.rightmove\.co\.uk/property-for-sale/.*").mock(
                side_effect=[
                    httpx.Response(200, html=html),
                    httpx.Response(200, html=html),
                ]
            )
            report = await crawl_rightmove_search(
                crawler,
                SearchQuery(location="Cambridge", transaction="sale", max_pages=1),
            )
            assert report.source is Source.RIGHTMOVE
            assert report.pages_fetched == 1
            assert len(report.listings) >= 10
            assert all(listing.source is Source.RIGHTMOVE for listing in report.listings)


class TestCrawlOnTheMarketSearch:
    @respx.mock
    async def test_single_page_returns_listings(self) -> None:
        async with SimpleCrawler() as crawler:
            html = _read("onthemarket/search_cambridge_2026-04.html")
            respx.get(url__regex=r"https://www\.onthemarket\.com/for-sale/property/.*").mock(
                side_effect=[
                    httpx.Response(200, html=html),
                    httpx.Response(200, html=html),
                ]
            )
            report = await crawl_onthemarket_search(
                crawler,
                SearchQuery(location="Cambridge", transaction="sale", max_pages=1),
            )
            assert report.source is Source.ONTHEMARKET
            assert report.pages_fetched == 1
            assert len(report.listings) >= 10
            assert all(listing.source is Source.ONTHEMARKET for listing in report.listings)


class TestHydrateDetails:
    @respx.mock
    async def test_zoopla_detail_hydration_merges_fields(self) -> None:
        async with SimpleCrawler() as crawler:
            search_html = _read("zoopla/search_cambridgeshire_2026-04.html")
            detail_html = _read("zoopla/detail_72228361_2026-04.html")
            respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
                side_effect=[
                    httpx.Response(200, html=search_html),
                    httpx.Response(200, html=search_html),
                ]
            )
            respx.get(url__regex=r"https://www\.zoopla\.co\.uk/(for-sale|new-homes)/details/.*").mock(
                return_value=httpx.Response(200, html=detail_html)
            )
            report = await crawl_zoopla_search(
                crawler,
                SearchQuery(location="Cambridge", transaction="sale", max_pages=1),
                hydrate_details=True,
            )
            assert report.detail_pages_fetched >= 1
            hydrated = [
                lst
                for lst in report.listings
                if lst.description and len(lst.description) > 200
            ]
            assert hydrated, "expected at least one listing with a hydrated description"
