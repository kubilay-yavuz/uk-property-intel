"""Listings search utilities for UK property portals.

Public API:

* :class:`SearchQuery` + :class:`TransactionKind` - structured query.
* :func:`build_zoopla_search_url`, :func:`build_rightmove_search_url`,
  :func:`build_onthemarket_search_url` - pure URL builders.
* :class:`SimpleCrawler` - httpx-only crawler.
* :class:`CrawlerProtocol`, :class:`FetchResult`, :class:`FetcherError` -
  crawler interface.
* :func:`crawl_zoopla_search`, :func:`crawl_rightmove_search`,
  :func:`crawl_onthemarket_search` + :class:`CrawlReport` - pagination loops.
"""

from __future__ import annotations

from uk_property_listings.search import (
    CrawlReport,
    crawl_onthemarket_search,
    crawl_rightmove_search,
    crawl_zoopla_search,
)
from uk_property_listings.simple_crawler import SimpleCrawler
from uk_property_listings.types import (
    CrawlerProtocol,
    FetcherError,
    FetchResult,
    SearchQuery,
    TransactionKind,
)
from uk_property_listings.urls import (
    build_onthemarket_search_url,
    build_rightmove_search_url,
    build_zoopla_search_url,
)

__all__ = [
    "CrawlReport",
    "CrawlerProtocol",
    "FetchResult",
    "FetcherError",
    "SearchQuery",
    "SimpleCrawler",
    "TransactionKind",
    "build_onthemarket_search_url",
    "build_rightmove_search_url",
    "build_zoopla_search_url",
    "crawl_onthemarket_search",
    "crawl_rightmove_search",
    "crawl_zoopla_search",
]
