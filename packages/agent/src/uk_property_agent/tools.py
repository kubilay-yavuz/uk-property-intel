"""LangChain tools wrapping the UK property data layer.

Each tool takes a small typed input and returns a JSON-serialisable dict (or a
list of them). Keeping tool signatures narrow and the outputs schema-stable
makes it straightforward for an LLM to use them and for us to unit-test.

Tools are built via :func:`build_tools` with a :class:`ToolContext` that holds
the (injectable) crawler factory and API client factories. This dependency
injection makes the tools deterministically testable without live networks.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field
from uk_property_apis import (
    ApplicationDetail,
    ArcGISPlanningClient,
    CompaniesHouseClient,
    CouncilConfig,
    EPCClient,
    FloodClient,
    HTMLPlanningClient,
    LandRegistryClient,
    PlanningClient,
    PoliceClient,
    PostcodesClient,
    build_landlord_graph,
)
from uk_property_apis.idox import KNOWN_COUNCILS, get_council
from uk_property_avm import comparables_from_ppd, estimate_value
from uk_property_geo import AmenityCategory, OverpassClient, haversine_m
from uk_property_listings import (
    SearchQuery,
    SimpleCrawler,
    crawl_onthemarket_search,
    crawl_rightmove_search,
    crawl_zoopla_search,
)


@asynccontextmanager
async def _default_crawler_factory():
    """Default httpx-only crawler factory.

    The public agent uses :class:`uk_property_listings.SimpleCrawler` - no
    anti-bot moat, no Playwright fallback. For production-grade reliability
    configure this :class:`ToolContext` with a crawler factory backed by the
    private ``uk-property-apify-shared`` ``Crawler`` or by one of the hosted
    Apify actors.
    """
    async with SimpleCrawler.from_env() as crawler:
        yield crawler


@dataclass
class ToolContext:
    """Dependency-injectable factories for tools.

    In production, most fields default to "construct from env". In tests,
    callers pass pre-built clients or respx-mocked ones.
    """

    crawler_factory: Callable[[], Any] = field(default=_default_crawler_factory)
    postcodes_factory: Callable[[], PostcodesClient] = PostcodesClient
    epc_factory: Callable[[], EPCClient] | None = None
    land_registry_factory: Callable[[], LandRegistryClient] = LandRegistryClient
    planning_factory: Callable[[], PlanningClient] = PlanningClient
    police_factory: Callable[[], PoliceClient] = PoliceClient
    flood_factory: Callable[[], FloodClient] = FloodClient
    companies_house_factory: Callable[[], CompaniesHouseClient] | None = None
    overpass_factory: Callable[[], OverpassClient] = OverpassClient
    arcgis_planning_factory: Callable[[CouncilConfig], ArcGISPlanningClient] = ArcGISPlanningClient
    html_planning_factory: Callable[[CouncilConfig], HTMLPlanningClient] = HTMLPlanningClient

    @classmethod
    def from_env(cls) -> ToolContext:
        """Build a context wired to production factories.

        EPC and Companies House factories are added only if the relevant
        credentials are present; otherwise those tools are silently dropped.
        """
        ctx = cls()
        if os.getenv("EPC_AUTH_EMAIL") and os.getenv("EPC_AUTH_TOKEN"):
            ctx.epc_factory = EPCClient
        if os.getenv("COMPANIES_HOUSE_API_KEY"):
            ctx.companies_house_factory = CompaniesHouseClient
        return ctx


class SearchListingsArgs(BaseModel):
    """Shared input schema for portal search tools."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(..., min_length=1, description="Town, postcode, or region.")
    transaction: str = Field("sale", description="'sale' or 'rent'.")
    min_price: int | None = Field(None, ge=0, description="Min price in GBP.")
    max_price: int | None = Field(None, ge=0, description="Max price in GBP.")
    min_beds: int | None = Field(None, ge=0, le=20)
    max_beds: int | None = Field(None, ge=0, le=20)
    max_pages: int = Field(1, ge=1, le=5, description="Number of search pages to fetch.")


class PostcodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    postcode: str = Field(..., min_length=2)


class LatLngArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class LatLngRadiusArgs(LatLngArgs):
    radius_m: float = Field(500.0, ge=50.0, le=10_000.0)


class CrimeNearArgs(LatLngArgs):
    months_back: int = Field(3, ge=1, le=24)


class EPCSearchArgs(PostcodeArgs):
    size: int = Field(50, ge=1, le=200)


class CompanyLookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_number: str = Field(..., min_length=1)


class CompanySearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-text query: company name, number, or previous name. Matches "
            "the `/search/companies` endpoint behaviour."
        ),
    )
    items_per_page: int = Field(20, ge=1, le=100)


class CompanySubresourceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_number: str = Field(..., min_length=1)
    items_per_page: int = Field(25, ge=1, le=100)


class EstimatePropertyValueArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    postcode: str = Field(
        ...,
        min_length=2,
        description="Target UK postcode. Normalised internally.",
    )
    property_type: Literal["D", "S", "T", "F", "O"] | None = Field(
        default=None,
        description=(
            "Land Registry PPD property-type code. D=Detached, S=Semi-detached, "
            "T=Terraced, F=Flat/maisonette, O=Other. When supplied the model "
            "tightens the band by matching like-with-like first."
        ),
    )
    years_back: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Only consider sales within the last N years (default 3).",
    )


class DistanceBetweenPostcodesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_postcode: str = Field(..., min_length=2)
    to_postcode: str = Field(..., min_length=2)


class AmenitiesNearPostcodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    postcode: str = Field(..., min_length=2)
    categories: list[str] = Field(
        default_factory=lambda: [AmenityCategory.RAIL_STATION.value],
        description=("One or more categories from: " + ", ".join(c.value for c in AmenityCategory)),
    )
    radius_m: float = Field(500.0, ge=50.0, le=5_000.0)
    limit: int = Field(15, ge=1, le=50)


class SearchPlanningArgs(BaseModel):
    """Input for :func:`search_planning_applications` — ArcGIS-preferred, HTML fallback."""

    model_config = ConfigDict(extra="forbid")

    council: str = Field(
        ...,
        min_length=1,
        description=(
            "Council slug such as 'lambeth'. Call list_planning_councils for the full registry."
        ),
    )
    mode: Literal["recent", "search"] = Field(
        "recent",
        description=(
            "'recent' returns applications modified in the last ``since_days`` "
            "(ArcGIS councils only). 'search' does a substring lookup on "
            "address / description using whichever transport is available."
        ),
    )
    query: str | None = Field(
        default=None,
        description="Required in 'search' mode — e.g. an address fragment or keyword.",
    )
    query_target: Literal["address", "description"] = Field(
        "address",
        description="Which field the 'search' substring filters against.",
    )
    since_days: int = Field(
        7,
        ge=1,
        le=365,
        description="Look-back window for 'recent' mode. Ignored otherwise.",
    )
    max_results: int = Field(
        25,
        ge=1,
        le=200,
        description="Hard cap on the number of rows returned.",
    )


class PlanningDetailArgs(BaseModel):
    """Input for :func:`lookup_planning_application` — always HTML transport."""

    model_config = ConfigDict(extra="forbid")

    council: str = Field(..., min_length=1)
    reference: str | None = Field(
        default=None,
        description="Human planning reference (e.g. '23/00123/FUL').",
    )
    key_val: str | None = Field(
        default=None,
        description="Idox internal KEYVAL — takes precedence over 'reference' when set.",
    )


class LandlordGraphArgs(BaseModel):
    """Input for :func:`landlord_network_for_company`."""

    model_config = ConfigDict(extra="forbid")

    company_number: str = Field(..., min_length=1)
    depth: int = Field(
        2,
        ge=1,
        le=3,
        description=(
            "Traversal hops. 1 = immediate officers + PSCs; 2 = also each "
            "officer's other appointments (canonical landlord portfolio); "
            "3 = recursive (can fan out fast)."
        ),
    )
    max_companies: int = Field(50, ge=1, le=500)
    max_officers: int = Field(200, ge=1, le=1000)
    expand_corporate_pscs: bool = Field(default=True)


def _listing_to_dict(listing: Any) -> dict[str, Any]:
    """Best-effort dict for a :class:`Listing` - strip None, nest prices simply."""
    return listing.model_dump(mode="json", exclude_none=True)


def _run_search_tool(
    ctx: ToolContext,
    *,
    crawl_fn: Callable[..., Awaitable[Any]],
    source: str,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def _tool(
        location: str,
        transaction: str = "sale",
        min_price: int | None = None,
        max_price: int | None = None,
        min_beds: int | None = None,
        max_beds: int | None = None,
        max_pages: int = 1,
    ) -> dict[str, Any]:
        txn = "sale" if transaction not in {"sale", "rent"} else transaction
        async with ctx.crawler_factory() as crawler:
            report = await crawl_fn(
                crawler,
                SearchQuery(
                    location=location,
                    transaction=txn,
                    min_price=min_price,
                    max_price=max_price,
                    min_beds=min_beds,
                    max_beds=max_beds,
                    max_pages=max_pages,
                ),
                hydrate_details=False,
            )
        return {
            "source": source,
            "query": {
                "location": location,
                "transaction": txn,
                "min_price": min_price,
                "max_price": max_price,
                "min_beds": min_beds,
                "max_beds": max_beds,
            },
            "pages_fetched": report.pages_fetched,
            "listings": [_listing_to_dict(lst) for lst in report.listings],
            "errors": report.errors,
        }

    return _tool


def build_tools(ctx: ToolContext | None = None) -> list[StructuredTool]:
    """Build the full tool list for the UK property agent.

    Pass a custom :class:`ToolContext` to swap out factories (e.g. for tests).
    """
    ctx = ctx or ToolContext()

    tools: list[StructuredTool] = []

    tools.append(
        StructuredTool.from_function(
            name="search_zoopla",
            description=(
                "Search Zoopla for-sale or to-rent listings. Prices in GBP. "
                "Returns listings in the canonical Listing schema (prices in pence). "
                "Prefer this first for broad queries in England/Wales."
            ),
            args_schema=SearchListingsArgs,
            coroutine=_run_search_tool(ctx, crawl_fn=crawl_zoopla_search, source="zoopla"),
        )
    )
    tools.append(
        StructuredTool.from_function(
            name="search_rightmove",
            description=(
                "Search Rightmove listings. Largest UK portal; use when Zoopla "
                "coverage seems thin, or to cross-check prices. Same schema."
            ),
            args_schema=SearchListingsArgs,
            coroutine=_run_search_tool(ctx, crawl_fn=crawl_rightmove_search, source="rightmove"),
        )
    )
    tools.append(
        StructuredTool.from_function(
            name="search_onthemarket",
            description=(
                "Search OnTheMarket listings. Agent-led portal useful for new "
                "instructions not yet on Zoopla/Rightmove."
            ),
            args_schema=SearchListingsArgs,
            coroutine=_run_search_tool(
                ctx, crawl_fn=crawl_onthemarket_search, source="onthemarket"
            ),
        )
    )

    async def _lookup_postcode(postcode: str) -> dict[str, Any]:
        client = ctx.postcodes_factory()
        async with client:
            result = await client.lookup_postcode(postcode)
        return result.model_dump(mode="json", exclude_none=True)

    tools.append(
        StructuredTool.from_function(
            name="lookup_postcode",
            description=(
                "Look up geography for a UK postcode (lat/lng, ward, district, "
                "country, region). Essential to resolve a postcode to lat/lng "
                "before calling spatial tools like crime or listed buildings."
            ),
            args_schema=PostcodeArgs,
            coroutine=_lookup_postcode,
        )
    )

    async def _sold_prices(postcode: str) -> dict[str, Any]:
        client = ctx.land_registry_factory()
        async with client:
            rows = await client.search_by_postcode(postcode, expand=True)
        return {
            "postcode": postcode,
            "count": len(rows),
            "records": [row.model_dump(mode="json", exclude_none=True) for row in rows],
        }

    tools.append(
        StructuredTool.from_function(
            name="sold_prices_for_postcode",
            description=(
                "Return HM Land Registry Price Paid records for a postcode, "
                "including price, transfer_date, property type. Good for "
                "sanity-checking asking prices and tracking historical growth."
            ),
            args_schema=PostcodeArgs,
            coroutine=_sold_prices,
        )
    )

    async def _estimate_property_value(
        postcode: str,
        property_type: str | None = None,
        years_back: int = 3,
    ) -> dict[str, Any]:
        cutoff = (datetime.now(tz=UTC).date() - timedelta(days=365 * years_back)).isoformat()
        client = ctx.land_registry_factory()
        async with client:
            rows = await client.search_by_postcode(postcode, expand=True)
        comps = comparables_from_ppd(rows, min_transfer_date=cutoff)
        estimate = estimate_value(postcode, comps, property_type=property_type)
        return {
            "postcode": postcode.strip().upper(),
            "property_type": property_type,
            "years_back": years_back,
            "min_transfer_date": cutoff,
            "ppd_rows_fetched": len(rows),
            "comparables_considered": len(comps),
            "estimate": estimate.model_dump(mode="json", exclude_none=True),
        }

    tools.append(
        StructuredTool.from_function(
            name="estimate_property_value",
            description=(
                "Estimate a UK property's market value from Land Registry "
                "Price Paid Data using a rolling-median baseline AVM. Fetches "
                "PPD sales for the target postcode within the last "
                "`years_back` years, then picks the tightest matching tier "
                "(postcode+type, postcode, postcode-area+type, postcode-area, "
                "national) and returns a central estimate plus low/high band "
                "and a confidence label. Prefer this over eyeballing "
                "`sold_prices_for_postcode` when the user asks 'what's it "
                "worth?'."
            ),
            args_schema=EstimatePropertyValueArgs,
            coroutine=_estimate_property_value,
        )
    )

    if ctx.epc_factory is not None:
        epc_factory = ctx.epc_factory

        async def _epc_search(postcode: str, size: int = 50) -> dict[str, Any]:
            client = epc_factory()
            async with client:
                page = await client.search_domestic(postcode=postcode, size=size)
            return {
                "postcode": postcode,
                "count": len(page.rows),
                "next_search_after": page.next_search_after,
                "rows": [row.model_dump(mode="json", exclude_none=True) for row in page.rows],
            }

        tools.append(
            StructuredTool.from_function(
                name="epc_certificates_for_postcode",
                description=(
                    "Return domestic EPC certificates registered at a UK postcode. "
                    "Requires EPC_AUTH_EMAIL / EPC_AUTH_TOKEN env vars - if those "
                    "aren't set this tool won't be available."
                ),
                args_schema=EPCSearchArgs,
                coroutine=_epc_search,
            )
        )

    async def _crime_near(lat: float, lng: float, months_back: int = 3) -> dict[str, Any]:
        client = ctx.police_factory()
        async with client:
            summary = await client.crime_stats_near(lat, lng, months_back=months_back)
        return summary.model_dump(mode="json", exclude_none=True)

    tools.append(
        StructuredTool.from_function(
            name="crime_stats_near",
            description=(
                "Aggregate reported street crime near a lat/lng for the last N "
                "months (from data.police.uk). Returns totals by category and "
                "per-month breakdown. Use after resolving a postcode to lat/lng."
            ),
            args_schema=CrimeNearArgs,
            coroutine=_crime_near,
        )
    )

    async def _listed_buildings(lat: float, lng: float, radius_m: float = 500.0) -> dict[str, Any]:
        client = ctx.planning_factory()
        async with client:
            page = await client.fetch_listed_buildings_near(lat, lng, radius_m=radius_m)
        return page.model_dump(mode="json", exclude_none=True)

    tools.append(
        StructuredTool.from_function(
            name="listed_buildings_near",
            description=(
                "Return listed (protected heritage) buildings within `radius_m` "
                "metres of a lat/lng from planning.data.gov.uk. Useful for "
                "gauging conservation constraints on an area."
            ),
            args_schema=LatLngRadiusArgs,
            coroutine=_listed_buildings,
        )
    )

    async def _flood_warnings(lat: float, lng: float) -> dict[str, Any]:
        client = ctx.flood_factory()
        async with client:
            floods = await client.active_floods_near(lat, lng)
        return {
            "lat": lat,
            "lng": lng,
            "count": len(floods),
            "items": [f.model_dump(mode="json", exclude_none=True) for f in floods],
        }

    tools.append(
        StructuredTool.from_function(
            name="flood_warnings_near",
            description=(
                "Active flood warnings within the default radius of a lat/lng "
                "(Environment Agency). Use to assess current flood risk context."
            ),
            args_schema=LatLngArgs,
            coroutine=_flood_warnings,
        )
    )

    if ctx.companies_house_factory is not None:
        ch_factory = ctx.companies_house_factory

        async def _company_search(query: str, items_per_page: int = 20) -> dict[str, Any]:
            client = ch_factory()
            async with client:
                response = await client.search_companies(query, items_per_page=items_per_page)
            return response.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="search_companies",
                description=(
                    "Free-text search of Companies House (UK). Returns up to "
                    "`items_per_page` hits with company name, number, type, "
                    "status, date of creation, and registered office. Use this "
                    "to resolve an agent or landlord's name to a company number "
                    "before calling the deeper profile/officers/psc tools."
                ),
                args_schema=CompanySearchArgs,
                coroutine=_company_search,
            )
        )

        async def _company_profile(company_number: str) -> dict[str, Any]:
            client = ch_factory()
            async with client:
                profile = await client.get_company(company_number)
            return profile.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="company_profile",
                description=(
                    "Fetch the full Companies House profile for a UK company "
                    "number: status, incorporation date, registered office "
                    "address, SIC codes, accounts status. Use when you have a "
                    "company number and need the canonical record."
                ),
                args_schema=CompanyLookupArgs,
                coroutine=_company_profile,
            )
        )

        async def _company_officers(
            company_number: str, items_per_page: int = 35
        ) -> dict[str, Any]:
            client = ch_factory()
            async with client:
                response = await client.get_officers(company_number, items_per_page=items_per_page)
            return response.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="company_officers",
                description=(
                    "List officers (directors, secretaries, LLP members) for a "
                    "Companies House company, including appointment / "
                    "resignation dates, role, nationality and occupation. Good "
                    "for 'who runs this property company' questions."
                ),
                args_schema=CompanySubresourceArgs,
                coroutine=_company_officers,
            )
        )

        async def _company_psc(company_number: str, items_per_page: int = 35) -> dict[str, Any]:
            client = ch_factory()
            async with client:
                response = await client.get_psc(company_number, items_per_page=items_per_page)
            return response.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="company_psc",
                description=(
                    "Persons with Significant Control for a Companies House "
                    "company: individuals / entities that own >25% of shares "
                    "or voting rights, or otherwise control the company. "
                    "Use to uncover the ultimate beneficial owner behind a "
                    "property-holding SPV."
                ),
                args_schema=CompanySubresourceArgs,
                coroutine=_company_psc,
            )
        )

        async def _company_filings(company_number: str, items_per_page: int = 25) -> dict[str, Any]:
            client = ch_factory()
            async with client:
                response = await client.get_filing_history(
                    company_number, items_per_page=items_per_page
                )
            return response.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="company_filings",
                description=(
                    "Filing history for a Companies House company: accounts, "
                    "confirmation statements, charge registrations, director "
                    "changes, etc. Ordered most-recent-first. Useful to check "
                    "whether accounts are overdue or a company is dormant."
                ),
                args_schema=CompanySubresourceArgs,
                coroutine=_company_filings,
            )
        )

        async def _company_charges(company_number: str, items_per_page: int = 25) -> dict[str, Any]:
            client = ch_factory()
            async with client:
                response = await client.get_charges(company_number, items_per_page=items_per_page)
            return response.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="company_charges",
                description=(
                    "Registered charges (mortgages / debentures) for a "
                    "Companies House company, including status, amount secured "
                    "and persons entitled. Lets the agent reason about whether "
                    "a property-holding company is encumbered."
                ),
                args_schema=CompanySubresourceArgs,
                coroutine=_company_charges,
            )
        )

    async def _distance_between_postcodes(from_postcode: str, to_postcode: str) -> dict[str, Any]:
        client = ctx.postcodes_factory()
        async with client:
            src = await client.lookup_postcode(from_postcode)
            dst = await client.lookup_postcode(to_postcode)
        if src.latitude is None or src.longitude is None:
            raise ValueError(f"{from_postcode} has no coordinates in postcodes.io")
        if dst.latitude is None or dst.longitude is None:
            raise ValueError(f"{to_postcode} has no coordinates in postcodes.io")
        metres = haversine_m(src.latitude, src.longitude, dst.latitude, dst.longitude)
        return {
            "from_postcode": src.postcode,
            "to_postcode": dst.postcode,
            "from_lat_lng": {"lat": src.latitude, "lng": src.longitude},
            "to_lat_lng": {"lat": dst.latitude, "lng": dst.longitude},
            "distance_m": round(metres, 1),
            "distance_km": round(metres / 1000.0, 3),
            "distance_miles": round(metres / 1609.344, 3),
        }

    tools.append(
        StructuredTool.from_function(
            name="distance_between_postcodes",
            description=(
                "Straight-line (great-circle) distance in metres/km/miles between "
                "two UK postcodes. Uses postcodes.io for lookup + haversine. "
                "Use as a cheap proxy for 'how far' questions before reaching "
                "for routing; not road or transit time."
            ),
            args_schema=DistanceBetweenPostcodesArgs,
            coroutine=_distance_between_postcodes,
        )
    )

    async def _amenities_near_postcode(
        postcode: str,
        categories: list[str] | None = None,
        radius_m: float = 500.0,
        limit: int = 15,
    ) -> dict[str, Any]:
        cats: list[AmenityCategory] = []
        for raw in categories or []:
            try:
                cats.append(AmenityCategory(raw))
            except ValueError:
                continue
        if not cats:
            cats = [AmenityCategory.RAIL_STATION]

        pc_client = ctx.postcodes_factory()
        async with pc_client:
            lookup = await pc_client.lookup_postcode(postcode)

        if lookup.latitude is None or lookup.longitude is None:
            raise ValueError(f"{postcode} has no coordinates in postcodes.io")

        overpass = ctx.overpass_factory()
        async with overpass:
            hits = await overpass.amenities_near(
                lookup.latitude,
                lookup.longitude,
                radius_m=radius_m,
                categories=cats,
            )

        trimmed = hits[:limit]
        return {
            "postcode": lookup.postcode,
            "origin": {"lat": lookup.latitude, "lng": lookup.longitude},
            "radius_m": radius_m,
            "categories": [c.value for c in cats],
            "count": len(hits),
            "items": [h.model_dump(mode="json", exclude_none=True) for h in trimmed],
            "truncated": len(hits) > len(trimmed),
        }

    tools.append(
        StructuredTool.from_function(
            name="amenities_near_postcode",
            description=(
                "Return nearby OpenStreetMap amenities around a UK postcode. "
                "Categories: "
                + ", ".join(c.value for c in AmenityCategory)
                + ". Results are sorted by distance ascending. Use for "
                "'10-min walk to a station', 'schools within 500 m', etc."
            ),
            args_schema=AmenitiesNearPostcodeArgs,
            coroutine=_amenities_near_postcode,
        )
    )

    async def _list_planning_councils() -> dict[str, Any]:
        rows = [
            {
                "slug": c.slug,
                "name": c.name,
                "public_access_base_url": c.public_access_base_url,
                "supports_arcgis": c.arcgis_base_url is not None,
                "supports_recent": c.arcgis_base_url is not None,
                "supports_search": True,
            }
            for c in KNOWN_COUNCILS.values()
        ]
        rows.sort(key=lambda row: row["slug"])
        return {"count": len(rows), "councils": rows}

    tools.append(
        StructuredTool.from_function(
            name="list_planning_councils",
            description=(
                "List every UK council this agent can query for planning "
                "applications. Each entry reports whether the council "
                "publishes the ArcGIS FeatureServer (which unlocks the "
                "'recent' mode on search_planning_applications) or only "
                "responds to the slower HTML form-POST fallback."
            ),
            args_schema=BaseModel,
            coroutine=_list_planning_councils,
        )
    )

    async def _search_planning_applications(
        council: str,
        mode: str = "recent",
        query: str | None = None,
        query_target: str = "address",
        since_days: int = 7,
        max_results: int = 25,
    ) -> dict[str, Any]:
        from uk_property_agent.apify_mode import (
            maybe_delegate_search_planning_applications,
        )

        delegated = await maybe_delegate_search_planning_applications(
            council=council,
            mode=mode,
            query=query,
            query_target=query_target,
            since_days=since_days,
            max_results=max_results,
        )
        if delegated is not None:
            return delegated

        try:
            config = get_council(council)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

        supports_arcgis = config.arcgis_base_url is not None
        if mode == "recent" and not supports_arcgis:
            raise ValueError(
                f"Council {config.slug!r} does not publish an ArcGIS "
                "FeatureServer; 'recent' mode requires ArcGIS. Use "
                "mode='search' with an address / keyword instead."
            )
        cleaned_query = (query or "").strip()
        if mode == "search" and not cleaned_query:
            raise ValueError("mode='search' requires a non-empty query")

        transport: str
        apps: list[Any]
        if mode == "recent":
            since = datetime.now(tz=UTC) - timedelta(days=since_days)
            client = ctx.arcgis_planning_factory(config)
            async with client:
                apps = await client.recent_applications(since=since, max_results=max_results)
            transport = "arcgis"
        elif mode == "search":
            if supports_arcgis:
                client = ctx.arcgis_planning_factory(config)
                async with client:
                    if query_target == "description":
                        apps = await client.search_by_description(
                            cleaned_query, max_results=max_results
                        )
                    else:
                        apps = await client.search_by_address(
                            cleaned_query, max_results=max_results
                        )
                transport = "arcgis"
            else:
                html_client = ctx.html_planning_factory(config)
                async with html_client:
                    apps = await html_client.search(cleaned_query, max_results=max_results)
                transport = "html"
        else:  # pragma: no cover - guarded by the schema
            raise ValueError(f"Unknown mode {mode!r}")

        return {
            "council": config.slug,
            "council_name": config.name,
            "mode": mode,
            "query": cleaned_query or None,
            "query_target": query_target if mode == "search" else None,
            "since_days": since_days if mode == "recent" else None,
            "transport": transport,
            "count": len(apps),
            "applications": [app.model_dump(mode="json", exclude_none=True) for app in apps],
        }

    tools.append(
        StructuredTool.from_function(
            name="search_planning_applications",
            description=(
                "Search UK council planning applications (Idox Public "
                "Access). Use mode='recent' to fetch applications modified "
                "in the last N days (ArcGIS-backed councils only — call "
                "list_planning_councils to see which) or mode='search' to "
                "substring-match an address or description. ArcGIS is "
                "preferred automatically; the slower HTML form-POST is "
                "used only when no FeatureServer exists."
            ),
            args_schema=SearchPlanningArgs,
            coroutine=_search_planning_applications,
        )
    )

    async def _lookup_planning_application(
        council: str,
        reference: str | None = None,
        key_val: str | None = None,
    ) -> dict[str, Any]:
        try:
            config = get_council(council)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        if not (reference or key_val):
            raise ValueError(
                "Provide at least one of 'key_val' or 'reference' to identify the application"
            )

        html_client = ctx.html_planning_factory(config)
        detail: ApplicationDetail | None = None
        async with html_client:
            if key_val:
                detail = await html_client.get_by_key_val(key_val.strip())
            else:
                detail = await html_client.get_detail_by_reference((reference or "").strip())

        return {
            "council": config.slug,
            "council_name": config.name,
            "transport": "html",
            "found": detail is not None,
            "application": (
                detail.model_dump(mode="json", exclude_none=True) if detail is not None else None
            ),
        }

    tools.append(
        StructuredTool.from_function(
            name="lookup_planning_application",
            description=(
                "Fetch the full Idox detail page for one planning "
                "application, including case officer, decision, documents, "
                "and related cases. Lookup by Idox KEYVAL (preferred when "
                "known) or by planning reference. Always uses the HTML "
                "transport because the richer metadata is not exposed on "
                "the ArcGIS FeatureServer."
            ),
            args_schema=PlanningDetailArgs,
            coroutine=_lookup_planning_application,
        )
    )

    if ctx.companies_house_factory is not None:
        ch_factory_graph = ctx.companies_house_factory

        async def _landlord_network_for_company(
            company_number: str,
            depth: int = 2,
            max_companies: int = 50,
            max_officers: int = 200,
            expand_corporate_pscs: bool = True,
        ) -> dict[str, Any]:
            from uk_property_agent.apify_mode import (
                maybe_delegate_landlord_network_for_company,
            )

            delegated = await maybe_delegate_landlord_network_for_company(
                company_number=company_number,
                depth=depth,
                max_companies=max_companies,
                max_officers=max_officers,
                expand_corporate_pscs=expand_corporate_pscs,
            )
            if delegated is not None:
                return delegated

            client = ch_factory_graph()
            async with client:
                graph = await build_landlord_graph(
                    client,
                    company_number,
                    depth=depth,
                    max_companies=max_companies,
                    max_officers=max_officers,
                    expand_corporate_pscs=expand_corporate_pscs,
                )
            return graph.model_dump(mode="json", exclude_none=True)

        tools.append(
            StructuredTool.from_function(
                name="landlord_network_for_company",
                description=(
                    "Map the Companies House landlord network rooted at a "
                    "UK company number: its officers, PSCs, each officer's "
                    "other appointments (portfolio discovery), and any "
                    "corporate PSC's own controllers. Returns a structured "
                    "graph with nodes and directed control edges. Depth=2 "
                    "is the canonical 'landlord portfolio' layer. Requires "
                    "COMPANIES_HOUSE_API_KEY."
                ),
                args_schema=LandlordGraphArgs,
                coroutine=_landlord_network_for_company,
            )
        )

    return tools


ALL_TOOLS: list[StructuredTool] = build_tools()
