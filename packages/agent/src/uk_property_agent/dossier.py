"""Typed :class:`PropertyDossier` + orchestration helper for the agent.

One tool call to :func:`build_property_dossier` produces a single Pydantic
object summarising everything the UK-property stack can say about a
postcode *fast* (no portal scraping — those are left to the per-portal
tools because they're the slow ones).

The dossier is deliberately shaped for LLM consumption:

* every block is independently nullable (we surface
  :class:`DossierError` entries for any source that fails or is skipped
  due to missing credentials);
* counts and magnitudes live at the top of each block so the LLM can
  skim before diving into arrays;
* raw payloads from the underlying clients are stored compactly (trimmed
  arrays, per-category counts) — enough context for grounded reasoning
  but cheap enough for the prompt.

Because all the heavy lifting is already implemented in the individual
:mod:`uk_property_agent.tools` coroutines, the builder just delegates to
the same factories via :class:`uk_property_agent.tools.ToolContext`.
That keeps the dossier deterministic to test and means the prompt-caching
tier (see :mod:`uk_property_agent.prompts`) can trust a stable shape.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from uk_property_apis.idox import KNOWN_COUNCILS
from uk_property_apis.postcodes import PostcodeResult
from uk_property_avm import comparables_from_ppd, estimate_value
from uk_property_geo import AmenityCategory

if TYPE_CHECKING:
    from uk_property_agent.tools import ToolContext


logger = logging.getLogger("uk_property_agent.dossier")


_DEFAULT_AMENITIES: tuple[AmenityCategory, ...] = (
    AmenityCategory.RAIL_STATION,
    AmenityCategory.BUS_STOP,
    AmenityCategory.SUPERMARKET,
    AmenityCategory.SCHOOL,
    AmenityCategory.PARK,
    AmenityCategory.GP,
    AmenityCategory.PHARMACY,
)


class DossierError(BaseModel):
    """One source failure captured without collapsing the whole dossier."""

    model_config = ConfigDict(extra="forbid")

    source: str
    error_type: str
    message: str


class DossierLocation(BaseModel):
    """Resolved postcode geography (postcodes.io)."""

    model_config = ConfigDict(extra="forbid")

    postcode: str
    lat: float | None = None
    lng: float | None = None
    country: str | None = None
    region: str | None = None
    admin_district: str | None = None
    admin_ward: str | None = None
    parish: str | None = None
    parliamentary_constituency: str | None = None
    lsoa: str | None = None
    msoa: str | None = None
    outcode: str | None = None
    nuts: str | None = None


class DossierAVM(BaseModel):
    """Summary of the in-process median-baseline valuation."""

    model_config = ConfigDict(extra="forbid")

    estimate_gbp: int | None = None
    low_gbp: int | None = None
    high_gbp: int | None = None
    basis: str | None = None
    confidence: str | None = None
    comparables_used: int | None = None
    ppd_rows_fetched: int | None = None
    min_transfer_date: str | None = None
    property_type: str | None = None
    years_back: int | None = None


class DossierSaleStat(BaseModel):
    """Compact summary of a recent PPD sale."""

    model_config = ConfigDict(extra="forbid")

    price_gbp: int
    transfer_date: str
    property_type: str | None = None
    paon: str | None = None
    saon: str | None = None
    street: str | None = None
    postcode: str | None = None


class DossierPPD(BaseModel):
    """Price Paid Data stats for the postcode."""

    model_config = ConfigDict(extra="forbid")

    rows_fetched: int
    median_price_gbp: int | None = None
    mean_price_gbp: int | None = None
    min_price_gbp: int | None = None
    max_price_gbp: int | None = None
    recent_sales: list[DossierSaleStat] = Field(default_factory=list)


class DossierAmenityCategory(BaseModel):
    """Counts and nearest-hit snapshot for one category."""

    model_config = ConfigDict(extra="forbid")

    category: str
    count: int
    nearest_name: str | None = None
    nearest_distance_m: float | None = None


class DossierNeighbourhood(BaseModel):
    """OSM amenities + listed-building context around the postcode."""

    model_config = ConfigDict(extra="forbid")

    radius_m: float
    categories: list[DossierAmenityCategory]
    listed_buildings_within_radius: int | None = None


class DossierCrime(BaseModel):
    """``data.police.uk`` summary for a short window."""

    model_config = ConfigDict(extra="forbid")

    months_back: int
    total_incidents: int | None = None
    top_categories: list[dict[str, Any]] = Field(default_factory=list)


class DossierFlood(BaseModel):
    """Active EA flood warnings near the postcode."""

    model_config = ConfigDict(extra="forbid")

    active_warnings: int
    worst_severity_level: int | None = None


class DossierEPC(BaseModel):
    """EPC register summary (present only when credentials available)."""

    model_config = ConfigDict(extra="forbid")

    count: int
    top_ratings: dict[str, int] = Field(default_factory=dict)


class DossierPlanning(BaseModel):
    """Idox planning applications for the postcode's council, when mappable."""

    model_config = ConfigDict(extra="forbid")

    council_slug: str
    council_name: str
    transport: str | None = None
    recent_count: int
    recent_since_days: int


class PropertyDossier(BaseModel):
    """Top-level dossier: one postcode → every fast signal we can gather."""

    model_config = ConfigDict(extra="forbid")

    postcode: str
    generated_at: str
    location: DossierLocation | None = None
    avm: DossierAVM | None = None
    ppd: DossierPPD | None = None
    neighbourhood: DossierNeighbourhood | None = None
    crime: DossierCrime | None = None
    flood: DossierFlood | None = None
    epc: DossierEPC | None = None
    planning: DossierPlanning | None = None
    errors: list[DossierError] = Field(default_factory=list)


class DossierOptions(BaseModel):
    """Shape the dossier build. All fields optional with sensible defaults."""

    model_config = ConfigDict(extra="forbid")

    property_type: Literal["D", "S", "T", "F", "O"] | None = Field(default=None)
    years_back: int = Field(default=3, ge=1, le=10)
    amenity_radius_m: float = Field(default=800.0, ge=100.0, le=5_000.0)
    listed_radius_m: float = Field(default=500.0, ge=100.0, le=5_000.0)
    crime_months_back: int = Field(default=3, ge=1, le=24)
    recent_sales_limit: int = Field(default=5, ge=0, le=50)
    include_planning: bool = Field(default=True)
    planning_since_days: int = Field(default=30, ge=1, le=365)
    amenity_categories: list[AmenityCategory] = Field(
        default_factory=lambda: list(_DEFAULT_AMENITIES)
    )


async def build_property_dossier(
    *,
    postcode: str,
    ctx: ToolContext,
    options: DossierOptions | None = None,
) -> PropertyDossier:
    """Assemble a :class:`PropertyDossier` for ``postcode``.

    Runs every source in parallel via :func:`asyncio.gather` with
    ``return_exceptions=True`` so one upstream outage can't kill the
    whole dossier — failures land as :class:`DossierError` rows.
    """
    options = options or DossierOptions()

    lookup = await _lookup_postcode(postcode, ctx=ctx)
    errors: list[DossierError] = []
    location_block: DossierLocation | None = None

    if isinstance(lookup, PostcodeResult):
        location_block = _location_from_lookup(lookup)
    else:
        errors.append(lookup)

    lat: float | None = None
    lng: float | None = None
    resolved_postcode = postcode.strip().upper()
    if isinstance(lookup, PostcodeResult):
        lat = lookup.latitude
        lng = lookup.longitude
        resolved_postcode = lookup.postcode

    coros: dict[str, Any] = {}

    coros["avm"] = _run_avm(
        postcode=resolved_postcode,
        property_type=options.property_type,
        years_back=options.years_back,
        ctx=ctx,
    )

    if lat is not None and lng is not None:
        coros["neighbourhood"] = _run_neighbourhood(
            postcode=resolved_postcode,
            lat=lat,
            lng=lng,
            categories=options.amenity_categories,
            radius_m=options.amenity_radius_m,
            listed_radius_m=options.listed_radius_m,
            ctx=ctx,
        )
        coros["crime"] = _run_crime(
            lat=lat,
            lng=lng,
            months_back=options.crime_months_back,
            ctx=ctx,
        )
        coros["flood"] = _run_flood(lat=lat, lng=lng, ctx=ctx)

    if ctx.epc_factory is not None:
        coros["epc"] = _run_epc(postcode=resolved_postcode, ctx=ctx)

    if options.include_planning and location_block is not None:
        council = _council_for_district(location_block.admin_district)
        if council is not None:
            coros["planning"] = _run_planning(
                council_slug=council,
                since_days=options.planning_since_days,
                ctx=ctx,
            )

    keys = list(coros.keys())
    results = await asyncio.gather(*coros.values(), return_exceptions=True)

    dossier_fields: dict[str, Any] = {
        "postcode": resolved_postcode,
        "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "location": location_block,
    }
    for key, outcome in zip(keys, results, strict=True):
        if isinstance(outcome, BaseException):
            errors.append(
                DossierError(
                    source=key,
                    error_type=type(outcome).__name__,
                    message=str(outcome),
                )
            )
            continue
        block, block_errors = outcome
        if key == "avm":
            avm_block, ppd_block = block
            dossier_fields["avm"] = avm_block
            dossier_fields["ppd"] = ppd_block
        else:
            dossier_fields[key] = block
        errors.extend(block_errors)

    dossier_fields.setdefault("ppd", None)
    dossier_fields["errors"] = errors
    return PropertyDossier.model_validate(dossier_fields)


async def _lookup_postcode(
    postcode: str,
    *,
    ctx: ToolContext,
) -> PostcodeResult | DossierError:
    try:
        client = ctx.postcodes_factory()
        async with client:
            return await client.lookup_postcode(postcode)
    except Exception as exc:
        logger.warning("postcodes.io lookup failed for %s: %s", postcode, exc)
        return DossierError(
            source="postcodes",
            error_type=type(exc).__name__,
            message=str(exc),
        )


def _location_from_lookup(lookup: PostcodeResult) -> DossierLocation:
    return DossierLocation(
        postcode=lookup.postcode,
        lat=lookup.latitude,
        lng=lookup.longitude,
        country=lookup.country,
        region=lookup.region,
        admin_district=lookup.admin_district,
        admin_ward=lookup.admin_ward,
        parish=lookup.parish,
        parliamentary_constituency=lookup.parliamentary_constituency,
        lsoa=lookup.lsoa,
        msoa=lookup.msoa,
        outcode=lookup.outcode,
        nuts=lookup.nuts,
    )


async def _run_avm(
    *,
    postcode: str,
    property_type: str | None,
    years_back: int,
    ctx: ToolContext,
) -> tuple[tuple[DossierAVM | None, DossierPPD | None], list[DossierError]]:
    """Compute AVM + PPD summary from Land Registry.

    Packaged together because both use the same PPD fetch — we don't
    want two round-trips for one postcode. Returns
    ``((avm_block, ppd_block), errors)`` which the caller spreads into
    the top-level dossier (``ppd`` lives as a sibling of ``avm``).
    """
    errors: list[DossierError] = []
    try:
        client = ctx.land_registry_factory()
        async with client:
            rows = await client.search_by_postcode(postcode, expand=True)
    except Exception as exc:
        errors.append(
            DossierError(
                source="land-registry",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        return (None, None), errors

    cutoff = (
        datetime.now(tz=UTC).date() - timedelta(days=365 * years_back)
    ).isoformat()
    comps = comparables_from_ppd(rows, min_transfer_date=cutoff)
    try:
        estimate = estimate_value(postcode, comps, property_type=property_type)
    except Exception as exc:
        errors.append(
            DossierError(
                source="avm",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        estimate = None

    ppd_block = _ppd_block_from_rows(rows)

    avm_block: DossierAVM | None = None
    if estimate is not None:
        avm_block = DossierAVM(
            estimate_gbp=estimate.estimate_gbp,
            low_gbp=estimate.low_gbp,
            high_gbp=estimate.high_gbp,
            basis=estimate.basis,
            confidence=estimate.confidence,
            comparables_used=estimate.comparables_used,
            ppd_rows_fetched=len(rows),
            min_transfer_date=cutoff,
            property_type=property_type,
            years_back=years_back,
        )

    return (avm_block, ppd_block), errors


def _ppd_block_from_rows(rows: list[Any]) -> DossierPPD | None:
    if not rows:
        return DossierPPD(rows_fetched=0)

    raw_prices = [getattr(row, "price", None) for row in rows]
    prices: list[int] = [int(p) for p in raw_prices if isinstance(p, int) and p > 0]

    median_gbp: int | None = sorted(prices)[len(prices) // 2] if prices else None
    mean_gbp: int | None = sum(prices) // len(prices) if prices else None
    min_gbp: int | None = min(prices) if prices else None
    max_gbp: int | None = max(prices) if prices else None

    def transfer_date_key(row: Any) -> str:
        return str(getattr(row, "transfer_date", "") or "")

    recent = sorted(rows, key=transfer_date_key, reverse=True)
    recent_stats = [
        DossierSaleStat(
            price_gbp=int(r.price),
            transfer_date=str(r.transfer_date),
            property_type=getattr(r, "property_type", None),
            paon=getattr(r, "paon", None),
            saon=getattr(r, "saon", None),
            street=getattr(r, "street", None),
            postcode=getattr(r, "postcode", None),
        )
        for r in recent[:5]
        if getattr(r, "price", None) and getattr(r, "transfer_date", None)
    ]

    return DossierPPD(
        rows_fetched=len(rows),
        median_price_gbp=median_gbp,
        mean_price_gbp=mean_gbp,
        min_price_gbp=min_gbp,
        max_price_gbp=max_gbp,
        recent_sales=recent_stats,
    )


async def _run_neighbourhood(
    *,
    postcode: str,
    lat: float,
    lng: float,
    categories: list[AmenityCategory],
    radius_m: float,
    listed_radius_m: float,
    ctx: ToolContext,
) -> tuple[DossierNeighbourhood | None, list[DossierError]]:
    """Fetch Overpass amenities + listed-building count in parallel."""
    errors: list[DossierError] = []

    async def _fetch_amenities() -> dict[str, Any]:
        client = ctx.overpass_factory()
        async with client:
            hits = await client.amenities_near(
                lat,
                lng,
                radius_m=radius_m,
                categories=list(categories),
            )
        return {"hits": hits}

    async def _fetch_listed() -> int | None:
        client = ctx.planning_factory()
        async with client:
            page = await client.fetch_listed_buildings_near(
                lat, lng, radius_m=listed_radius_m
            )
        entities = getattr(page, "entities", None) or []
        return len(entities)

    amenity_result, listed_result = await asyncio.gather(
        _fetch_amenities(),
        _fetch_listed(),
        return_exceptions=True,
    )

    categories_block: list[DossierAmenityCategory] = []
    if isinstance(amenity_result, Exception):
        errors.append(
            DossierError(
                source="overpass",
                error_type=type(amenity_result).__name__,
                message=str(amenity_result),
            )
        )
    else:
        hits = amenity_result["hits"]
        by_category: dict[str, list[Any]] = {}
        for hit in hits:
            by_category.setdefault(hit.category, []).append(hit)
        for category in categories:
            bucket = by_category.get(category.value, [])
            nearest = bucket[0] if bucket else None
            categories_block.append(
                DossierAmenityCategory(
                    category=category.value,
                    count=len(bucket),
                    nearest_name=getattr(nearest, "name", None) if nearest else None,
                    nearest_distance_m=(
                        round(nearest.distance_m, 1) if nearest else None
                    ),
                )
            )

    listed_count: int | None = None
    if isinstance(listed_result, Exception):
        errors.append(
            DossierError(
                source="listed-buildings",
                error_type=type(listed_result).__name__,
                message=str(listed_result),
            )
        )
    else:
        listed_count = listed_result

    block = DossierNeighbourhood(
        radius_m=radius_m,
        categories=categories_block,
        listed_buildings_within_radius=listed_count,
    )
    return block, errors


async def _run_crime(
    *,
    lat: float,
    lng: float,
    months_back: int,
    ctx: ToolContext,
) -> tuple[DossierCrime | None, list[DossierError]]:
    try:
        client = ctx.police_factory()
        async with client:
            summary = await client.crime_stats_near(lat, lng, months_back=months_back)
    except Exception as exc:
        return None, [
            DossierError(
                source="police",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        ]

    by_category = getattr(summary, "by_category", None) or {}
    total = sum(int(v) for v in by_category.values()) if by_category else None
    top = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return (
        DossierCrime(
            months_back=months_back,
            total_incidents=total,
            top_categories=[{"category": k, "count": v} for k, v in top],
        ),
        [],
    )


async def _run_flood(
    *,
    lat: float,
    lng: float,
    ctx: ToolContext,
) -> tuple[DossierFlood | None, list[DossierError]]:
    try:
        client = ctx.flood_factory()
        async with client:
            floods = await client.active_floods_near(lat, lng)
    except Exception as exc:
        return None, [
            DossierError(
                source="flood",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        ]

    severities: list[int] = []
    for f in floods:
        raw = getattr(f, "severity_level", None)
        if raw is None:
            continue
        try:
            severities.append(int(raw))
        except (TypeError, ValueError):
            continue
    return (
        DossierFlood(
            active_warnings=len(floods),
            worst_severity_level=min(severities) if severities else None,
        ),
        [],
    )


async def _run_epc(
    *,
    postcode: str,
    ctx: ToolContext,
) -> tuple[DossierEPC | None, list[DossierError]]:
    assert ctx.epc_factory is not None
    try:
        client = ctx.epc_factory()
        async with client:
            page = await client.search_domestic(postcode=postcode, size=100)
    except Exception as exc:
        return None, [
            DossierError(
                source="epc",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        ]

    ratings: dict[str, int] = {}
    for row in page.rows:
        rating = getattr(row, "current_energy_rating", None)
        if not rating:
            continue
        ratings[str(rating)] = ratings.get(str(rating), 0) + 1

    return (
        DossierEPC(count=len(page.rows), top_ratings=dict(sorted(ratings.items()))),
        [],
    )


async def _run_planning(
    *,
    council_slug: str,
    since_days: int,
    ctx: ToolContext,
) -> tuple[DossierPlanning | None, list[DossierError]]:
    errors: list[DossierError] = []
    config = KNOWN_COUNCILS[council_slug]
    transport: str = "html"
    apps: list[Any] = []

    try:
        if config.arcgis_base_url is not None:
            since = datetime.now(tz=UTC) - timedelta(days=since_days)
            client = ctx.arcgis_planning_factory(config)
            async with client:
                apps = await client.recent_applications(since=since, max_results=25)
            transport = "arcgis"
        else:
            html_client = ctx.html_planning_factory(config)
            async with html_client:
                apps = await html_client.search("", max_results=25)
    except Exception as exc:
        errors.append(
            DossierError(
                source="planning",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        return None, errors

    return (
        DossierPlanning(
            council_slug=council_slug,
            council_name=config.name,
            transport=transport,
            recent_count=len(apps),
            recent_since_days=since_days,
        ),
        errors,
    )


def _council_for_district(admin_district: str | None) -> str | None:
    """Best-effort map from postcodes.io ``admin_district`` to a known council slug.

    The public library only ships two reference councils (Lambeth and
    Westminster). We return ``None`` for anything else so the dossier
    simply skips planning data — the full 340-council registry lives in
    the private ``uk-property-apify-shared`` package. If a hosted
    ``uk-property-apify`` dependency is installed, its lookup takes
    precedence automatically because the agent's local path falls
    through to ``maybe_delegate_search_planning_applications`` (which
    ultimately uses the hosted actor's own much richer registry).
    """
    if admin_district is None:
        return None
    slug = admin_district.strip().lower().replace(" ", "-")
    if slug in KNOWN_COUNCILS:
        return slug
    return None


__all__ = [
    "DossierAVM",
    "DossierAmenityCategory",
    "DossierCrime",
    "DossierEPC",
    "DossierError",
    "DossierFlood",
    "DossierLocation",
    "DossierNeighbourhood",
    "DossierOptions",
    "DossierPPD",
    "DossierPlanning",
    "DossierSaleStat",
    "PropertyDossier",
    "build_property_dossier",
]
