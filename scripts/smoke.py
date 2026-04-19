"""Live smoke harness: hit every public API we have a client for and print proof.

Unit tests are all ``respx``-mocked. They prove our code handles the shape we
*expect* - nothing about whether the real endpoint still returns that shape
today. This script closes that gap.

It exercises every free public API we depend on end-to-end:

- postcodes.io (no auth)
- HMLR Price Paid (no auth)
- data.police.uk (no auth)
- Environment Agency flood monitoring (no auth)
- planning.data.gov.uk (no auth)
- OSM Overpass (no auth)
- EPC Open Data Communities (BasicAuth - skipped if creds missing)
- Companies House (API key - skipped if missing)
- ONS Beta API (no auth)
- ONS Nomis labour market (no auth)
- Natural England MAGIC WFS (no auth)
- Contracts Finder tenders (no auth)
- VOA council tax (scraper)
- Idox ArcGIS + HTML planning (scrapers)
- Rightmove / Zoopla / OnTheMarket HTML parsers (fixture-driven)
- listings live GETs (block-expected, report only)

Run:

    uv run scripts/smoke.py

or target a subset:

    uv run scripts/smoke.py postcodes police flood

Exit code is non-zero if *any* non-skipped probe fails, so this works as a
"something broke in the wild" canary in CI / cron.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from uk_property_apis import (
    CompaniesHouseClient,
    EPCClient,
    FloodClient,
    LandRegistryClient,
    ONSClient,
    PlanningClient,
    PoliceClient,
    PostcodesClient,
    VOAClient,
)
from uk_property_apis.idox import (
    KNOWN_COUNCILS,
    ArcGISPlanningClient,
    HTMLPlanningClient,
)
from uk_property_apis.natural_england import NaturalEnglandClient
from uk_property_apis.ons_nomis import NomisClient
from uk_property_apis.tenders import ContractsFinderClient, TenderQuery
from uk_property_geo import AmenityCategory, OverpassClient

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "packages" / "scrapers" / "tests" / "fixtures"

REFERENCE_POSTCODE = "SW1A 1AA"  # Buckingham Palace - a postcode that definitely exists
CAMBRIDGE_STATION_POSTCODE = "CB1 2JW"
CAMBRIDGE_STATION_LAT = 52.1941
CAMBRIDGE_STATION_LNG = 0.1370


@dataclass
class ProbeResult:
    name: str
    status: str  # "ok", "skip", "fail"
    detail: str
    elapsed_ms: int
    sample: dict[str, Any] = field(default_factory=dict)


ProbeFn = Callable[[], Awaitable[ProbeResult]]


def _ok(name: str, detail: str, elapsed_ms: int, **sample: Any) -> ProbeResult:
    return ProbeResult(name=name, status="ok", detail=detail, elapsed_ms=elapsed_ms, sample=sample)


def _skip(name: str, reason: str) -> ProbeResult:
    return ProbeResult(name=name, status="skip", detail=reason, elapsed_ms=0)


def _fail(name: str, exc: BaseException, elapsed_ms: int) -> ProbeResult:
    short = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
    return ProbeResult(name=name, status="fail", detail=short, elapsed_ms=elapsed_ms)


async def _timed(name: str, body: Callable[[], Awaitable[ProbeResult]]) -> ProbeResult:
    """Wrap a probe body, catching exceptions + measuring wall time."""

    start = time.perf_counter()
    try:
        result = await body()
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return _fail(name, exc, elapsed_ms)
    if result.elapsed_ms == 0:
        result.elapsed_ms = int((time.perf_counter() - start) * 1000)
    return result


async def probe_postcodes() -> ProbeResult:
    async def _body() -> ProbeResult:
        async with PostcodesClient() as client:
            r = await client.lookup_postcode(REFERENCE_POSTCODE)
        if r.latitude is None or r.longitude is None:
            raise RuntimeError("postcodes.io returned no lat/lng")
        return _ok(
            "postcodes.io",
            f"lookup({REFERENCE_POSTCODE}) -> lat={r.latitude:.4f} lng={r.longitude:.4f} "
            f"ward={r.admin_ward!r} district={r.admin_district!r}",
            0,
            postcode=r.postcode,
            lat=r.latitude,
            lng=r.longitude,
            ward=r.admin_ward,
            district=r.admin_district,
        )

    return await _timed("postcodes.io", _body)


async def probe_land_registry() -> ProbeResult:
    # EC1V 3AP (the HMLR docs' reference flat at Orchard Building, Pear Tree
    # Street, Islington) is always in the dataset. SE1 9SG (near London
    # Bridge) is a fallback. If both are empty, the API has changed.
    candidates = ["EC1V 3AP", "SE1 9SG"]

    async def _body() -> ProbeResult:
        async with LandRegistryClient() as client:
            records: list[Any] = []
            used_postcode = ""
            for pc in candidates:
                records = await client.search_by_postcode(pc, page_size=5, expand=True)
                if records:
                    used_postcode = pc
                    break
        if not records:
            raise RuntimeError(
                f"HMLR returned 0 Price Paid records for any of {candidates}"
            )
        first = records[0]
        return _ok(
            "HMLR Price Paid",
            f"search_by_postcode({used_postcode!r}, expand=True) -> {len(records)} records, "
            f"latest £{first.price:,} on {first.transfer_date} ({first.property_type})",
            0,
            postcode=used_postcode,
            n_records=len(records),
            latest_price=first.price,
            latest_date=first.transfer_date,
            latest_tenure=first.tenure,
        )

    return await _timed("HMLR Price Paid", _body)


async def probe_police() -> ProbeResult:
    async def _body() -> ProbeResult:
        async with PoliceClient() as client:
            summary = await client.crime_stats_near(
                CAMBRIDGE_STATION_LAT, CAMBRIDGE_STATION_LNG, months_back=3
            )
        total = sum(summary.by_category.values())
        top = sorted(summary.by_category.items(), key=lambda kv: -kv[1])[:3]
        top_s = ", ".join(f"{k}={v}" for k, v in top) or "(none)"
        return _ok(
            "data.police.uk",
            f"crime_stats_near(CB1 2JW, months_back=3) -> "
            f"months={len(summary.months)} total_crimes={total} top=[{top_s}]",
            0,
            total=total,
            months=summary.months,
            top=top,
        )

    return await _timed("data.police.uk", _body)


async def probe_flood() -> ProbeResult:
    async def _body() -> ProbeResult:
        async with FloodClient() as client:
            # Use central London - typically a handful of Thames warnings active.
            warnings = await client.active_floods_near(
                51.5074, -0.1278, distance_km=50.0
            )
        return _ok(
            "EA flood-monitoring",
            f"active_floods_near(London, 50km) -> {len(warnings)} warnings",
            0,
            count=len(warnings),
            severities=sorted({w.severity_level for w in warnings if w.severity_level is not None}),
        )

    return await _timed("EA flood-monitoring", _body)


async def probe_planning() -> ProbeResult:
    async def _body() -> ProbeResult:
        async with PlanningClient() as client:
            page = await client.fetch_listed_buildings_near(
                CAMBRIDGE_STATION_LAT, CAMBRIDGE_STATION_LNG, radius_m=500.0
            )
        return _ok(
            "planning.data.gov.uk",
            f"fetch_listed_buildings_near(Cambridge station, 500m) -> {page.count} entities",
            0,
            count=page.count,
            first=(page.entities[0].name if page.entities else None),
        )

    return await _timed("planning.data.gov.uk", _body)


async def probe_overpass() -> ProbeResult:
    async def _body() -> ProbeResult:
        async with OverpassClient() as client:
            hits = await client.amenities_near(
                CAMBRIDGE_STATION_LAT,
                CAMBRIDGE_STATION_LNG,
                radius_m=500.0,
                categories=[AmenityCategory.CAFE, AmenityCategory.RAIL_STATION],
            )
        cafes = [h for h in hits if h.category == AmenityCategory.CAFE]
        stations = [h for h in hits if h.category == AmenityCategory.RAIL_STATION]
        return _ok(
            "OSM Overpass",
            f"amenities_near(Cambridge station, 500m, [cafe, rail_station]) -> "
            f"{len(cafes)} cafes, {len(stations)} rail stations",
            0,
            cafes=len(cafes),
            rail_stations=len(stations),
            nearest=(hits[0].name if hits else None),
        )

    return await _timed("OSM Overpass", _body)


async def probe_epc() -> ProbeResult:
    email = os.environ.get("EPC_AUTH_EMAIL")
    token = os.environ.get("EPC_AUTH_TOKEN")
    if not email or not token:
        return _skip(
            "EPC Open Data", "EPC_AUTH_EMAIL / EPC_AUTH_TOKEN not set"
        )

    async def _body() -> ProbeResult:
        auth = httpx.BasicAuth(email, token)
        async with EPCClient(auth=auth) as client:
            page = await client.search_domestic(postcode="CB1 2JW", size=5)
        first = page.rows[0] if page.rows else None
        return _ok(
            "EPC Open Data",
            f"search_domestic(CB1 2JW, size=5) -> {len(page.rows)} rows, "
            f"first={(first and first.address)!r}",
            0,
            count=len(page.rows),
            first_address=(first and first.address),
            first_rating=(first and first.current_energy_rating),
        )

    return await _timed("EPC Open Data", _body)


async def probe_companies_house() -> ProbeResult:
    api_key = os.environ.get("COMPANIES_HOUSE_API_KEY")
    if not api_key:
        return _skip("Companies House", "COMPANIES_HOUSE_API_KEY not set")

    async def _body() -> ProbeResult:
        async with CompaniesHouseClient(api_key=api_key) as client:
            page = await client.search_companies("Barratt Developments", items_per_page=3)
        first = page.items[0] if page.items else None
        return _ok(
            "Companies House",
            f"search_companies('Barratt Developments', 3) -> {len(page.items)} hits, "
            f"first={first and first.title!r}",
            0,
            count=len(page.items),
            first=(first and first.title),
        )

    return await _timed("Companies House", _body)


def _load_fixture(relpath: str) -> str:
    path = FIXTURES_ROOT / relpath
    return path.read_text(encoding="utf-8")


def _listing_price_desc(listing: Any) -> str:
    """Render a short price summary for either sale or rent."""

    sp = getattr(listing, "sale_price", None)
    if sp is not None and sp.amount_pence is not None:
        return f"£{sp.amount_pence // 100:,} ({sp.qualifier.value})"
    if sp is not None:
        return f"{sp.raw!r} ({sp.qualifier.value})"
    rp = getattr(listing, "rent_price", None)
    if rp is not None and rp.amount_pence is not None:
        return f"£{rp.amount_pence // 100:,}/{rp.period.value}"
    return "(no price)"


async def probe_rightmove_parser() -> ProbeResult:
    async def _body() -> ProbeResult:
        from uk_property_scrapers.rightmove import (
            extract_listing_urls,
            parse_detail_page,
            parse_search_results,
        )
        from uk_property_scrapers.schema import TransactionType

        search_html = _load_fixture("rightmove/search_cambridge_2026-04.html")
        detail_html = _load_fixture("rightmove/detail_173261858_2026-04.html")
        urls = extract_listing_urls(search_html)
        search_rows = parse_search_results(
            search_html, transaction_type=TransactionType.SALE
        )
        detail = parse_detail_page(
            detail_html, transaction_type=TransactionType.SALE
        )
        if not urls or not search_rows or detail is None:
            raise RuntimeError(
                f"Rightmove parser returned empty: urls={len(urls)} "
                f"search_rows={len(search_rows)} detail={detail is not None}"
            )
        return _ok(
            "Rightmove parser",
            f"fixture(2026-04) -> {len(urls)} urls, {len(search_rows)} cards, "
            f"detail={_listing_price_desc(detail)}, bedrooms={detail.bedrooms}",
            0,
            n_urls=len(urls),
            n_cards=len(search_rows),
            detail_price_pence=(
                detail.sale_price.amount_pence if detail.sale_price else None
            ),
            detail_bedrooms=detail.bedrooms,
        )

    return await _timed("Rightmove parser", _body)


async def probe_zoopla_parser() -> ProbeResult:
    async def _body() -> ProbeResult:
        from uk_property_scrapers.schema import TransactionType
        from uk_property_scrapers.zoopla import (
            extract_listing_urls,
            parse_detail_page,
            parse_search_results,
        )

        search_html = _load_fixture("zoopla/search_cambridgeshire_2026-04.html")
        detail_html = _load_fixture("zoopla/detail_72228361_2026-04.html")
        urls = extract_listing_urls(search_html)
        search_rows = parse_search_results(
            search_html, transaction_type=TransactionType.SALE
        )
        detail = parse_detail_page(
            detail_html, transaction_type=TransactionType.SALE
        )
        if not urls or not search_rows or detail is None:
            raise RuntimeError(
                f"Zoopla parser returned empty: urls={len(urls)} "
                f"search_rows={len(search_rows)} detail={detail is not None}"
            )
        return _ok(
            "Zoopla parser",
            f"fixture(2026-04) -> {len(urls)} urls, {len(search_rows)} cards, "
            f"detail={_listing_price_desc(detail)}, bedrooms={detail.bedrooms}",
            0,
            n_urls=len(urls),
            n_cards=len(search_rows),
            detail_price_pence=(
                detail.sale_price.amount_pence if detail.sale_price else None
            ),
            detail_bedrooms=detail.bedrooms,
        )

    return await _timed("Zoopla parser", _body)


async def probe_onthemarket_parser() -> ProbeResult:
    async def _body() -> ProbeResult:
        from uk_property_scrapers.onthemarket import (
            extract_listing_urls,
            parse_detail_page,
            parse_search_results,
        )
        from uk_property_scrapers.schema import TransactionType

        search_html = _load_fixture("onthemarket/search_cambridge_2026-04.html")
        detail_html = _load_fixture("onthemarket/detail_18999957_2026-04.html")
        urls = extract_listing_urls(search_html)
        search_rows = parse_search_results(
            search_html, transaction_type=TransactionType.SALE
        )
        detail = parse_detail_page(
            detail_html, transaction_type=TransactionType.SALE
        )
        if not urls or not search_rows or detail is None:
            raise RuntimeError(
                f"OnTheMarket parser returned empty: urls={len(urls)} "
                f"search_rows={len(search_rows)} detail={detail is not None}"
            )
        return _ok(
            "OnTheMarket parser",
            f"fixture(2026-04) -> {len(urls)} urls, {len(search_rows)} cards, "
            f"detail={_listing_price_desc(detail)}, bedrooms={detail.bedrooms}",
            0,
            n_urls=len(urls),
            n_cards=len(search_rows),
            detail_price_pence=(
                detail.sale_price.amount_pence if detail.sale_price else None
            ),
            detail_bedrooms=detail.bedrooms,
        )

    return await _timed("OnTheMarket parser", _body)


async def probe_listings_live() -> ProbeResult:
    """Best-effort single GET to each portal landing page.

    These sites are behind Cloudflare / anti-bot. We use a realistic UA but
    expect many responses to be 403 or challenge pages. We *do not* fail the
    smoke on a block - we report exactly what we got so drift is visible.
    """

    async def _body() -> ProbeResult:
        ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
        )
        targets = [
            ("rightmove", "https://www.rightmove.co.uk/properties/173261858"),
            ("zoopla", "https://www.zoopla.co.uk/for-sale/details/72228361/"),
            ("onthemarket", "https://www.onthemarket.com/details/18999957/"),
        ]
        statuses: dict[str, str] = {}
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": ua, "Accept": "text/html"},
        ) as client:
            for name, url in targets:
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as exc:
                    statuses[name] = f"ERR:{type(exc).__name__}"
                    continue
                body = resp.text[:2000].lower()
                blocked = any(
                    marker in body
                    for marker in (
                        "cloudflare",
                        "attention required",
                        "just a moment",
                        "captcha",
                    )
                )
                statuses[name] = f"{resp.status_code}{'+blocked' if blocked else ''}"

        detail = ", ".join(f"{k}={v}" for k, v in statuses.items())
        return _ok(
            "listings live",
            f"httpx direct GET -> {detail} (block expected; Apify residential "
            "proxy needed for real scraping)",
            0,
            statuses=statuses,
        )

    return await _timed("listings live", _body)


async def probe_agent_chain() -> ProbeResult:
    """End-to-end chain: postcode lookup -> overpass amenities.

    This is the exact sequence the agent's ``amenities_near_postcode`` tool
    runs, so if this is green the tool works in production.
    """

    async def _body() -> ProbeResult:
        async with PostcodesClient() as pc_client:
            pc = await pc_client.lookup_postcode("CB1 2JW")
        if pc.latitude is None or pc.longitude is None:
            raise RuntimeError("postcodes.io returned no coords")
        async with OverpassClient() as op_client:
            hits = await op_client.amenities_near(
                pc.latitude,
                pc.longitude,
                radius_m=500.0,
                categories=[
                    AmenityCategory.RAIL_STATION,
                    AmenityCategory.SCHOOL,
                    AmenityCategory.HOSPITAL,
                ],
            )
        nearest = hits[0] if hits else None
        nearest_desc = (
            f"{nearest.name} ({nearest.category.value}, {nearest.distance_m:.0f}m)"
            if nearest is not None
            else "(none)"
        )
        return _ok(
            "agent chain",
            f"postcode(CB1 2JW) -> coords -> overpass(500m, [rail_station, school, "
            f"hospital]) -> {len(hits)} hits, nearest={nearest_desc}",
            0,
            n_hits=len(hits),
            nearest=nearest_desc,
        )

    return await _timed("agent chain", _body)


async def probe_voa() -> ProbeResult:
    # EC1V 3AP is the same flat-above-shops postcode we use for HMLR; ~40 rows
    # split across two pages, so this exercises pagination + band extraction.
    async def _body() -> ProbeResult:
        async with VOAClient() as client:
            rows = await client.search_by_postcode("EC1V 3AP", max_pages=3)
        if not rows:
            raise RuntimeError("VOA returned 0 council-tax rows for EC1V 3AP")
        bands = sorted({r.band for r in rows})
        authorities = sorted({r.local_authority for r in rows if r.local_authority})
        return _ok(
            "VOA council tax",
            f"search_by_postcode('EC1V 3AP') -> {len(rows)} rows, "
            f"bands={bands}, authority={authorities[0] if authorities else '?'}",
            0,
            n_rows=len(rows),
            bands=bands,
            first_address=rows[0].address,
        )

    return await _timed("VOA council tax", _body)


async def probe_planning_arcgis_lambeth() -> ProbeResult:
    """Lambeth's Idox FeatureServer is the canonical A5 ArcGIS council.

    We hit the ``?f=json`` service info (proves the endpoint is up and the
    schema still matches ``ArcGISFeatureServerInfo``), then issue a
    ``returnCountOnly=true`` query (proves the layer is queryable), then
    pull ``recent_applications(since=now-7d, max_results=5)`` - the exact
    sequence the ``search_planning_applications(mode='recent')`` agent
    tool runs in production.
    """

    async def _body() -> ProbeResult:
        from datetime import UTC, datetime, timedelta

        council = KNOWN_COUNCILS["lambeth"]
        async with ArcGISPlanningClient(council) as client:
            info = await client.get_service_info()
            total = await client.count()
            since = datetime.now(tz=UTC) - timedelta(days=7)
            recent = await client.recent_applications(since=since, max_results=5)
        layer_ids = sorted({layer.id for layer in info.layers})
        newest = recent[0] if recent else None
        newest_desc = (
            f"{newest.reference} @ {newest.address[:40]!r}" if newest else "(none)"
        )
        return _ok(
            "Idox ArcGIS (Lambeth)",
            f"fs_info(layers={layer_ids}, max_record={info.max_record_count}) "
            f"count={total:,} recent(7d,<=5) -> {len(recent)} apps, newest={newest_desc}",
            0,
            total_count=total,
            recent_count=len(recent),
            newest_reference=(newest.reference if newest else None),
            newest_modified=(
                newest.last_modified.isoformat() if newest and newest.last_modified else None
            ),
            layers=layer_ids,
            max_record_count=info.max_record_count,
        )

    return await _timed("Idox ArcGIS (Lambeth)", _body)


async def probe_planning_html_westminster() -> ProbeResult:
    """Westminster has no ArcGIS FeatureServer, so it's the canonical
    exerciser of the slow HTML fallback.

    We fetch a known historic reference ``24/00001/FULL`` via
    ``HTMLPlanningClient.search`` to confirm:

    * the Public Access ``search.do`` form still issues a parsable CSRF
    * ``simpleSearchResults.do?action=firstPage`` still renders an
      ``<ul id="searchresults">`` page that our parser consumes
    * Westminster hasn't been re-platformed away from Idox Public Access

    We deliberately search a *common address substring* rather than a
    reference so we're not dependent on any single long-lived case.
    """

    async def _body() -> ProbeResult:
        council = KNOWN_COUNCILS["westminster"]
        async with HTMLPlanningClient(council) as client:
            rows = await client.search("Victoria", max_results=5)
        first = rows[0] if rows else None
        return _ok(
            "Idox HTML (Westminster)",
            f"search('Victoria', max=5) -> {len(rows)} apps"
            + (
                f", first {first.reference} @ {first.address[:40]!r}"
                if first
                else ""
            ),
            0,
            count=len(rows),
            first_reference=(first.reference if first else None),
            first_key_val=(first.key_val if first else None),
            first_status=(first.status if first else None),
        )

    return await _timed("Idox HTML (Westminster)", _body)


async def probe_ons() -> ProbeResult:
    async def _body() -> ProbeResult:
        async with ONSClient() as client:
            # Census 2021 TS001 (usual residents) is always at edition=2021, version=3.
            meta = await client.dataset_version("TS001", edition="2021", version=3)
        return _ok(
            "ONS Nomis",
            f"dataset_version('TS001', 2021, v3) -> id={meta.id} edition={meta.edition} "
            f"dims={len(meta.dimensions)}",
            0,
            dataset_id=meta.id,
            edition=meta.edition,
            dims=len(meta.dimensions),
        )

    return await _timed("ONS Beta", _body)


async def probe_nomis() -> ProbeResult:
    """Exercise the ONS Nomis labour-market client against a real geography.

    We deliberately call the generic :meth:`NomisClient.observations` rather
    than a convenience wrapper (``claimant_count`` etc.) because Nomis needs
    both a ``TYPE{N}`` pseudo-geography *and* the correct measure codelist
    for each dataset. The measure codes baked into the convenience methods
    predate the live-smoke harness and are wrong for the real API — known
    gap, tracked in STATUS.md. The generic transport is what production
    code relies on, so that's what we canary.

    ``TYPE499`` is Nomis's pseudo-geography for "all countries" — it always
    returns a non-empty result for ``NM_162_1`` (claimant count).
    """

    async def _body() -> ProbeResult:
        dataset = "NM_162_1"
        async with NomisClient() as client:
            result = await client.observations(
                dataset,
                "TYPE499",
                measures="20100,20200,20201",
                date="latest",
            )
        if not result.observations:
            raise RuntimeError(
                f"{dataset}?geography=TYPE499 returned 0 observations - "
                "upstream drift"
            )
        first = result.observations[0]
        geo = first.get("geography", {}).get("description")
        value = first.get("obs_value", {}).get("value")
        time_period = first.get("time", {}).get("value")
        return _ok(
            "ONS Nomis labour",
            f"observations({dataset}, TYPE499, measures=20100) -> "
            f"{len(result.observations)} obs, first={geo!r} @ {time_period} "
            f"= {value}",
            0,
            count=len(result.observations),
            sample_geography=geo,
            sample_time=time_period,
            sample_value=value,
        )

    return await _timed("ONS Nomis labour", _body)


async def probe_natural_england() -> ProbeResult:
    """Hit the Natural England MAGIC WFS for a known-designated location.

    New Forest National Park (50.90, -1.58) reliably intersects the
    ``National_Parks_England`` layer, so it's the single-call canary we
    use. If zero designations come back we treat it as a fail — that
    means the WFS has moved again.
    """

    async def _body() -> ProbeResult:
        lat, lng = 50.90, -1.58
        async with NaturalEnglandClient() as client:
            designations = await client.designations_at(lat, lng)

        np_count = len(designations.national_parks)
        aonb_count = len(designations.aonb)
        sssi_count = len(designations.sssi)
        aw_count = len(designations.ancient_woodland)

        if np_count == 0:
            # New Forest *is* a National Park. Zero hits means the WFS
            # layer name or schema changed and we need to react.
            raise RuntimeError(
                "designations_at(New Forest) returned 0 national parks — WFS drift likely"
            )
        first = designations.national_parks[0].name
        return _ok(
            "Natural England WFS",
            f"designations_at(New Forest) -> NP={np_count} ({first!r}) "
            f"AONB={aonb_count} SSSI={sssi_count} AW={aw_count} "
            "(green_belt migrated to planning.data.gov.uk)",
            0,
            national_parks=np_count,
            aonbs=aonb_count,
            sssis=sssi_count,
            ancient_woodlands=aw_count,
        )

    return await _timed("Natural England WFS", _body)


async def probe_tenders() -> ProbeResult:
    """POST a real search against Contracts Finder.

    A broad property-CPV query (``45200000`` - building construction)
    reliably returns tender rows. We don't pin a count — CF results
    churn — but we do insist on at least one hit so parsing is exercised.
    """

    async def _body() -> ProbeResult:
        async with ContractsFinderClient() as cf:
            tenders = await cf.search_tenders(
                TenderQuery(cpv_codes=["45200000"], limit=10)
            )
        if not tenders:
            raise RuntimeError(
                "Contracts Finder returned 0 tenders for CPV 45200000 — "
                "schema drift or upstream outage"
            )
        sample = tenders[0]
        title = (sample.title or "")[:60]
        return _ok(
            "Contracts Finder",
            f"search_tenders(CPV=45200000, limit=10) -> {len(tenders)} rows, "
            f"first={sample.source_id!r} title={title!r}",
            0,
            count=len(tenders),
            first_id=sample.source_id,
            first_title=title,
        )

    return await _timed("Contracts Finder", _body)


PROBES: dict[str, ProbeFn] = {
    "postcodes": probe_postcodes,
    "land_registry": probe_land_registry,
    "police": probe_police,
    "flood": probe_flood,
    "planning": probe_planning,
    "overpass": probe_overpass,
    "ons": probe_ons,
    "nomis": probe_nomis,
    "natural_england": probe_natural_england,
    "tenders": probe_tenders,
    "voa": probe_voa,
    "idox_arcgis_lambeth": probe_planning_arcgis_lambeth,
    "idox_html_westminster": probe_planning_html_westminster,
    "rightmove_parser": probe_rightmove_parser,
    "zoopla_parser": probe_zoopla_parser,
    "onthemarket_parser": probe_onthemarket_parser,
    "listings_live": probe_listings_live,
    "agent_chain": probe_agent_chain,
    "epc": probe_epc,
    "companies_house": probe_companies_house,
}


def _render(results: list[ProbeResult]) -> None:
    print()
    print("=" * 80)
    print("UK Property Intel - live smoke results")
    print("=" * 80)
    for r in results:
        mark = {"ok": "[OK  ]", "skip": "[SKIP]", "fail": "[FAIL]"}[r.status]
        print(f"{mark} {r.name:28s} {r.elapsed_ms:>5d} ms   {r.detail}")
    print("-" * 80)
    ok = sum(1 for r in results if r.status == "ok")
    skip = sum(1 for r in results if r.status == "skip")
    fail = sum(1 for r in results if r.status == "fail")
    print(f"{ok} ok, {skip} skip, {fail} fail")
    print("=" * 80)


async def _main(selected: list[str] | None) -> int:
    targets = selected or list(PROBES)
    unknown = [t for t in targets if t not in PROBES]
    if unknown:
        print(f"Unknown probe(s): {unknown}. Known: {sorted(PROBES)}", file=sys.stderr)
        return 2

    # Run sequentially - each probe is tiny but we want clean log output and
    # don't want to hammer any one endpoint in parallel.
    results: list[ProbeResult] = []
    for name in targets:
        print(f"-> probing {name}…", flush=True)
        try:
            results.append(await PROBES[name]())
        except Exception as exc:
            traceback.print_exc()
            results.append(_fail(name, exc, 0))

    _render(results)
    return 0 if not any(r.status == "fail" for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "probes",
        nargs="*",
        help=f"Subset of probes to run. Default: all. Known: {', '.join(sorted(PROBES))}",
    )
    args = parser.parse_args()
    rc = asyncio.run(_main(args.probes or None))
    sys.exit(rc)


if __name__ == "__main__":
    main()
