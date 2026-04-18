"""Per-site pagination helpers that wire any ``CrawlerProtocol`` to the parsers.

These helpers give callers a single async function per portal that produces
:class:`~uk_property_scrapers.schema.Listing` instances. They handle:

* URL construction from a structured :class:`SearchQuery`.
* Pagination (best-effort - first N pages or until no new results).
* Detail-page fetch + parse, including deduplication.

The crawler argument is duck-typed via
:class:`~uk_property_listings.types.CrawlerProtocol`, so both the public
:class:`SimpleCrawler` and the private production ``Crawler`` (which lives in
the private ``uk-property-apify`` repo) work.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from uk_property_scrapers import onthemarket as otm
from uk_property_scrapers import rightmove as rm
from uk_property_scrapers import zoopla as zp
from uk_property_scrapers.schema import Listing, Source, TransactionType

from uk_property_listings.types import (
    CrawlerProtocol,
    FetcherError,
    SearchQuery,
    TransactionKind,
)
from uk_property_listings.urls import (
    build_onthemarket_search_url,
    build_rightmove_search_url,
    build_zoopla_search_url,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CrawlReport:
    """Summary of a site crawl - useful for actor output and observability."""

    source: Source
    pages_fetched: int
    detail_pages_fetched: int
    listings: list[Listing]
    errors: list[str]


async def crawl_zoopla_search(
    crawler: CrawlerProtocol,
    query: SearchQuery,
    *,
    hydrate_details: bool = False,
) -> CrawlReport:
    """Fetch a Zoopla search - paginate, parse, optionally fetch detail pages."""
    txn = _txn(query.transaction)
    return await _crawl_search(
        crawler=crawler,
        query=query,
        source=Source.ZOOPLA,
        url_builder=build_zoopla_search_url,
        parse_search=lambda html, _url: zp.parse_search_results(html, transaction_type=txn),
        parse_detail=lambda html, url: zp.parse_detail_page(
            html, source_url=url, transaction_type=txn
        ),
        hydrate_details=hydrate_details,
    )


async def crawl_rightmove_search(
    crawler: CrawlerProtocol,
    query: SearchQuery,
    *,
    hydrate_details: bool = False,
) -> CrawlReport:
    """Fetch a Rightmove search - paginate, parse, optionally fetch details."""
    txn = _txn(query.transaction)
    return await _crawl_search(
        crawler=crawler,
        query=query,
        source=Source.RIGHTMOVE,
        url_builder=build_rightmove_search_url,
        parse_search=lambda html, _url: rm.parse_search_results(html, transaction_type=txn),
        parse_detail=lambda html, url: rm.parse_detail_page(
            html, source_url=url, transaction_type=txn
        ),
        hydrate_details=hydrate_details,
    )


async def crawl_onthemarket_search(
    crawler: CrawlerProtocol,
    query: SearchQuery,
    *,
    hydrate_details: bool = False,
) -> CrawlReport:
    """Fetch an OnTheMarket search - paginate, parse, optionally fetch details."""
    txn = _txn(query.transaction)
    return await _crawl_search(
        crawler=crawler,
        query=query,
        source=Source.ONTHEMARKET,
        url_builder=build_onthemarket_search_url,
        parse_search=lambda html, _url: otm.parse_search_results(html, transaction_type=txn),
        parse_detail=lambda html, url: otm.parse_detail_page(
            html, source_url=url, transaction_type=txn
        ),
        hydrate_details=hydrate_details,
    )


async def _crawl_search(
    *,
    crawler: CrawlerProtocol,
    query: SearchQuery,
    source: Source,
    url_builder,
    parse_search,
    parse_detail,
    hydrate_details: bool,
) -> CrawlReport:
    listings: dict[str, Listing] = {}
    errors: list[str] = []
    pages_fetched = 0

    for page in range(1, query.max_pages + 1):
        url = url_builder(query, page=page)
        try:
            result = await crawler.fetch(url, expect_search_markers=True)
        except FetcherError as exc:
            errors.append(f"search page {page}: {exc}")
            logger.warning("crawl failed for %s page %d: %s", source, page, exc)
            break

        pages_fetched += 1
        page_listings = parse_search(result.html, result.final_url)
        new_count = 0
        for listing in page_listings:
            key = f"{listing.source_id}"
            if key in listings:
                continue
            listings[key] = listing
            new_count += 1
        if new_count == 0:
            logger.info("no new %s listings on page %d; stopping", source.value, page)
            break

    detail_fetched = 0
    if hydrate_details and listings:
        detail_fetched = await _hydrate_details(
            crawler=crawler,
            listings=listings,
            parse_detail=parse_detail,
            errors=errors,
        )

    return CrawlReport(
        source=source,
        pages_fetched=pages_fetched,
        detail_pages_fetched=detail_fetched,
        listings=list(listings.values()),
        errors=errors,
    )


async def _hydrate_details(
    *,
    crawler: CrawlerProtocol,
    listings: dict[str, Listing],
    parse_detail,
    errors: list[str],
) -> int:
    sem = asyncio.Semaphore(2)
    hydrated = 0

    async def fetch_one(key: str, base: Listing) -> None:
        nonlocal hydrated
        async with sem:
            try:
                result = await crawler.fetch(str(base.source_url))
            except FetcherError as exc:
                errors.append(f"detail {base.source_id}: {exc}")
                return
            try:
                detailed = parse_detail(result.html, result.final_url)
            except Exception as exc:
                errors.append(f"parse detail {base.source_id}: {exc}")
                return
            if detailed is not None:
                listings[key] = _merge_listings(base, detailed)
                hydrated += 1

    await asyncio.gather(*(fetch_one(k, v) for k, v in list(listings.items())))
    return hydrated


def _merge_listings(card: Listing, detail: Listing) -> Listing:
    """Overlay detail fields on top of a card listing.

    The detail page almost always has strictly more info (description, photos,
    agent phone); we prefer detail for every populated field but fall back to
    card for anything the detail parser couldn't find.
    """
    card_data = card.model_dump()
    detail_data = detail.model_dump()
    merged: dict[str, object] = {}
    for key, detail_value in detail_data.items():
        if _is_populated(detail_value):
            merged[key] = detail_value
        else:
            merged[key] = card_data.get(key)
    raw_merged = {**card_data.get("raw_site_fields", {}), **detail_data.get("raw_site_fields", {})}
    merged["raw_site_fields"] = raw_merged
    return Listing.model_validate(merged)


def _is_populated(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _txn(kind: TransactionKind) -> TransactionType:
    return TransactionType.SALE if kind == "sale" else TransactionType.RENT
