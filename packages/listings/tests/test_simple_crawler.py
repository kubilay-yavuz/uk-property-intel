"""Smoke tests for :class:`SimpleCrawler`."""

from __future__ import annotations

import httpx
import pytest
import respx
from uk_property_listings import FetcherError, SimpleCrawler


class TestSimpleCrawler:
    @respx.mock
    async def test_returns_fetch_result_on_200(self) -> None:
        respx.get("https://example.com/foo").mock(
            return_value=httpx.Response(200, html="<html><body>ok</body></html>")
        )
        async with SimpleCrawler() as crawler:
            result = await crawler.fetch("https://example.com/foo")
        assert result.status_code == 200
        assert "ok" in result.html
        assert result.final_url == "https://example.com/foo"

    @respx.mock
    async def test_raises_fetcher_error_on_4xx(self) -> None:
        respx.get("https://example.com/missing").mock(return_value=httpx.Response(404, text="nope"))
        async with SimpleCrawler() as crawler:
            with pytest.raises(FetcherError, match="HTTP 404"):
                await crawler.fetch("https://example.com/missing")

    @respx.mock
    async def test_raises_fetcher_error_on_transport_error(self) -> None:
        respx.get("https://example.com/boom").mock(side_effect=httpx.ConnectError("down"))
        async with SimpleCrawler() as crawler:
            with pytest.raises(FetcherError, match="httpx error"):
                await crawler.fetch("https://example.com/boom")

    @respx.mock
    async def test_accepts_expect_search_markers_kwarg(self) -> None:
        """Protocol compatibility: must accept the kwarg even though it's unused."""
        respx.get("https://example.com/search").mock(
            return_value=httpx.Response(200, html="<html></html>")
        )
        async with SimpleCrawler() as crawler:
            result = await crawler.fetch("https://example.com/search", expect_search_markers=True)
        assert result.status_code == 200
