"""Agent tools that only exist when a matching Apify actor is reachable.

Unlike :mod:`uk_property_agent.tools` (which pairs every tool with a
local fallback), the tools in this module are *thin* wrappers over
hosted Apify actors that have no local-library equivalent:

* ``find_auction_properties`` delegates to the ``uk-auctions`` actor
  (Allsop + Auction House UK + Savills + iamsold discovery + lot
  enrichment), because the underlying scrapers are proprietary.
* ``find_public_tenders`` delegates to the ``uk-tenders`` actor
  (Contracts Finder + Find a Tender unification), because FTS requires
  a private API key the actor ships with.
* ``demographics_for_area`` delegates to the ``uk-demographics`` actor
  (ONS Nomis + Census 2021 + IMD 2019 + MHCLG projections), because
  the orchestration + CSV ingestion is actor-hosted.
* ``climate_risk_for_point`` delegates to the ``uk-climate-risk`` actor
  (EA floods + NCERM coastal + BGS geohazards + UKCP18 + radon + AQ),
  because the BGS / NCERM / UKCP seeds are actor-hosted.

Each tool is registered via :func:`build_apify_only_tools` iff
``ApifyDelegation.resolve(<slug>)`` returns a client for its actor —
i.e. iff ``APIFY_API_TOKEN`` + either ``APIFY_USERNAME`` or the
actor-specific ``APIFY_ACTOR_*`` override are configured. Without
that, the agent's tool list simply doesn't advertise them so the LLM
won't try (and fail) to call them.

Actor rows are re-validated through the canonical Pydantic models
from :mod:`uk_property_scrapers` / :mod:`uk_property_apis` where a
public model exists (``AuctionLot``, ``Tender``). For the actors that
emit a bespoke envelope with no public canonical model
(demographics / climate-risk), the rows are passed through as dicts
with a stable shape that mirrors the actor's ``_envelope_for``
helper.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from uk_property_apify_client import ApifyDelegation, DelegationError
from uk_property_apis import Tender
from uk_property_scrapers import AuctionLot

_AuctionSource = Literal["allsop", "auction_house_uk", "savills_auctions", "iamsold"]
_AuctionLotType = Literal["residential", "commercial"]


class FindAuctionPropertiesArgs(BaseModel):
    """Input for :func:`find_auction_properties`.

    Two calling modes:

    * **Discovery** (default) — supply ``discover=True`` and optionally
      narrow via ``sources`` / ``lot_type``. The actor fans out across
      each configured auction house, finds upcoming auctions, and
      enumerates every lot.
    * **Explicit** — supply ``auction_ids`` + ``source`` to pull one
      or more auction catalogues directly.
    """

    model_config = ConfigDict(extra="forbid")

    discover: bool = Field(
        default=True,
        description=(
            "If true, let the actor discover upcoming auctions across "
            "every configured source. Ignored when ``auction_ids`` is "
            "set (explicit mode)."
        ),
    )
    sources: list[_AuctionSource] = Field(
        default_factory=list,
        description=(
            "Which auction houses to include in discovery. Leave empty "
            "for all registered sources. Ignored when ``auction_ids`` "
            "is set."
        ),
    )
    lot_type: _AuctionLotType | None = Field(
        default=None,
        description=(
            "Filter discovery to one lot type. Leave unset to include "
            "both residential and commercial. Ignored when "
            "``auction_ids`` is set."
        ),
    )
    auction_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit auction IDs for the configured ``source``. Allsop "
            "UUIDs, Auction House UK numeric IDs, Savills slug+IDs, or "
            "'live' for iamsold. When supplied, discovery is skipped."
        ),
    )
    source: _AuctionSource | None = Field(
        default=None,
        description=(
            "Which auction house owns ``auction_ids``. Required when "
            "``auction_ids`` is non-empty."
        ),
    )
    max_auctions: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Hard cap on auctions pulled per run.",
    )
    available_only: bool = Field(
        default=False,
        description=(
            "If true, only return lots still in 'available' state; "
            "default False surfaces the sold / unsold breakdown."
        ),
    )


_TenderSourceName = Literal["contracts-finder", "find-a-tender"]
_TenderStatusName = Literal[
    "planned", "open", "closed", "awarded", "cancelled", "complete", "unknown"
]
_DEFAULT_TENDER_SOURCES: list[_TenderSourceName] = [
    "contracts-finder",
    "find-a-tender",
]


class FindPublicTendersArgs(BaseModel):
    """Input for :func:`find_public_tenders`."""

    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(
        default=None,
        description=(
            "Free-text keyword for Contracts Finder server-side match "
            "and Find a Tender client-side match."
        ),
    )
    cpv_codes: list[str] = Field(
        default_factory=list,
        description=(
            "CPV codes to include. Accepts full 8-digit codes or parent "
            "prefixes. Property-intel prefixes: 45 (construction), 70 "
            "(real estate), 71 (architectural / engineering), 77 "
            "(landscaping), 90 (waste / cleaning)."
        ),
    )
    regions: list[str] = Field(
        default_factory=list,
        description="UK region filter.",
    )
    postcode: str | None = Field(
        default=None,
        description=(
            "Centroid postcode for radius search. Contracts Finder "
            "only — pair with ``radius_km``."
        ),
    )
    radius_km: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Contracts Finder radius in kilometres.",
    )
    statuses: list[_TenderStatusName] = Field(
        default_factory=list,
        description="Filter on canonical Tender.status values.",
    )
    value_low: int | None = Field(
        default=None,
        ge=0,
        description="Lower bound (GBP) on contract value.",
    )
    value_high: int | None = Field(
        default=None,
        ge=0,
        description="Upper bound (GBP) on contract value.",
    )
    published_from: str | None = Field(
        default=None,
        description="ISO 8601 lower bound on publication date.",
    )
    published_to: str | None = Field(
        default=None,
        description="ISO 8601 upper bound on publication date.",
    )
    limit_per_source: int = Field(
        default=50,
        ge=1,
        le=500,
        description=(
            "Max rows per source. Total rows returned across both "
            "registers is capped at ``2 * limit_per_source``."
        ),
    )
    sources: list[_TenderSourceName] = Field(
        default_factory=lambda: list(_DEFAULT_TENDER_SOURCES),
        description=(
            "Registers to query. Default both. Drop 'find-a-tender' if "
            "the actor doesn't have an FTS_API_KEY configured."
        ),
    )


_DemographicsSource = Literal["nomis", "census", "imd", "mhclg"]
_DEFAULT_DEMOGRAPHICS_SOURCES: list[_DemographicsSource] = [
    "nomis",
    "census",
    "imd",
    "mhclg",
]
_DEFAULT_CENSUS_TABLES: list[str] = ["TS001", "TS044", "TS021", "TS067"]


class DemographicsForAreaArgs(BaseModel):
    """Input for :func:`demographics_for_area`."""

    model_config = ConfigDict(extra="forbid")

    postcode: str = Field(
        ...,
        min_length=2,
        description=(
            "UK postcode (full or outcode) identifying the LA / LSOA to "
            "pull demographics for. The actor resolves the postcode to "
            "the enclosing LSOA + Lower-Tier LA via postcodes.io."
        ),
    )
    sources: list[_DemographicsSource] = Field(
        default_factory=lambda: list(_DEFAULT_DEMOGRAPHICS_SOURCES),
        description=(
            "Which sources to query. 'nomis' = ONS Nomis labour market "
            "(LTLA); 'census' = Census 2021 bulk tables (LTLA); 'imd' "
            "= IMD 2019 decile (LSOA); 'mhclg' = MHCLG household "
            "projections + Housing Delivery Test (LTLA)."
        ),
    )
    census_tables: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_CENSUS_TABLES),
        description=(
            "Census 2021 bulk-table codes. Default covers TS001 "
            "(usual residents), TS044 (housing tenure), TS021 "
            "(ethnic group) and TS067 (qualifications)."
        ),
    )


_ClimateRiskSource = Literal[
    "flood", "coastal", "bgs", "ukcp18", "radon", "air_quality"
]
_DEFAULT_CLIMATE_SOURCES: list[_ClimateRiskSource] = [
    "flood",
    "coastal",
    "bgs",
    "ukcp18",
    "radon",
    "air_quality",
]
_DEFAULT_UKCP18_SCENARIOS: list[str] = ["rcp85"]
_DEFAULT_UKCP18_EPOCHS: list[str] = ["2050", "2070"]


class ClimateRiskForPointArgs(BaseModel):
    """Input for :func:`climate_risk_for_point`."""

    model_config = ConfigDict(extra="forbid")

    postcode: str | None = Field(
        default=None,
        description="UK postcode. Either this or ``lat`` + ``lng``.",
    )
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)
    sources: list[_ClimateRiskSource] = Field(
        default_factory=lambda: list(_DEFAULT_CLIMATE_SOURCES),
        description=(
            "Which sources to query. 'flood' = EA flood warnings + "
            "areas + stations; 'coastal' = NCERM 2024 erosion + SMP "
            "shoreline; 'bgs' = BGS shrink-swell + landslide inventory; "
            "'ukcp18' = regional climate projections; 'radon' = UKHSA "
            "radon band; 'air_quality' = DEFRA UK-AIR stations."
        ),
    )
    flood_radius_km: int = Field(default=10, ge=1, le=50)
    coastal_radius_km: int = Field(default=5, ge=1, le=25)
    bgs_landslide_radius_km: int = Field(default=5, ge=1, le=25)
    air_quality_radius_km: int = Field(default=10, ge=1, le=50)
    ukcp18_scenarios: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_UKCP18_SCENARIOS),
        description="UKCP18 scenarios. Default RCP8.5; add 'rcp45' for comparison.",
    )
    ukcp18_epochs: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_UKCP18_EPOCHS),
        description="UKCP18 projection epochs (years).",
    )


async def _find_auction_properties(
    discover: bool = True,
    sources: list[str] | None = None,
    lot_type: str | None = None,
    auction_ids: list[str] | None = None,
    source: str | None = None,
    max_auctions: int = 10,
    available_only: bool = False,
) -> dict[str, Any]:
    """Pull upcoming auction lots via the hosted ``uk-auctions`` actor."""

    delegation = ApifyDelegation.resolve("uk-auctions")
    if delegation is None:
        raise DelegationError(
            "find_auction_properties requires a configured uk-auctions Apify "
            "delegation (set APIFY_API_TOKEN + APIFY_USERNAME or "
            "APIFY_ACTOR_UK_AUCTIONS)."
        )

    if auction_ids and not source:
        raise ValueError(
            "find_auction_properties requires 'source' when 'auction_ids' is set"
        )

    actor_input: dict[str, Any] = {
        "discover": discover and not auction_ids,
        "auctionIds": list(auction_ids or []),
        "sources": list(sources or []),
        "maxAuctions": max_auctions,
        "availableOnly": available_only,
        "concurrency": 4,
    }
    if source is not None:
        actor_input["source"] = source
    if lot_type is not None:
        actor_input["lotType"] = lot_type

    result = await delegation.call(actor_input)
    return _map_auctions_result(result.items, result.run_meta)


def _map_auctions_result(
    items: list[dict[str, Any]],
    run_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape uk-auctions actor rows into an agent-friendly response.

    Each row is an ``{auction_id, auction_source, auction_*,
    **AuctionLot.model_dump}`` envelope. We split the envelope metadata
    out into a ``lots`` entry with an ``auction`` sub-dict + a
    ``lot`` sub-dict (re-validated through :class:`AuctionLot`) so
    downstream LLM turns can reason about the lot independently of its
    catalogue position.
    """

    envelope_keys = {
        "auction_id",
        "auction_source",
        "auction_reference",
        "auction_name",
        "auction_date_day1",
        "auction_date_day2",
    }

    lots: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for row in items:
        if not isinstance(row, dict):
            parse_errors.append("uk-auctions row is not a dict")
            continue
        auction_meta = {k: row.get(k) for k in envelope_keys if k in row}
        lot_raw = {k: v for k, v in row.items() if k not in envelope_keys}
        try:
            lot = AuctionLot.model_validate(lot_raw)
        except ValidationError as exc:
            parse_errors.append(f"uk-auctions lot validation failed: {exc}")
            continue
        lots.append(
            {
                "auction": auction_meta,
                "lot": lot.model_dump(mode="json", exclude_none=True),
            }
        )

    return {
        "count": len(lots),
        "lots": lots,
        "run_meta": run_meta,
        "errors": parse_errors or None,
    }


async def _find_public_tenders(
    keyword: str | None = None,
    cpv_codes: list[str] | None = None,
    regions: list[str] | None = None,
    postcode: str | None = None,
    radius_km: int | None = None,
    statuses: list[str] | None = None,
    value_low: int | None = None,
    value_high: int | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    limit_per_source: int = 50,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Pull procurement notices via the hosted ``uk-tenders`` actor."""

    delegation = ApifyDelegation.resolve("uk-tenders")
    if delegation is None:
        raise DelegationError(
            "find_public_tenders requires a configured uk-tenders Apify "
            "delegation (set APIFY_API_TOKEN + APIFY_USERNAME or "
            "APIFY_ACTOR_UK_TENDERS)."
        )

    actor_input: dict[str, Any] = {
        "sources": list(sources) if sources else list(_DEFAULT_TENDER_SOURCES),
        "limitPerSource": limit_per_source,
    }
    if keyword:
        actor_input["keyword"] = keyword
    if cpv_codes:
        actor_input["cpvCodes"] = list(cpv_codes)
    if regions:
        actor_input["regions"] = list(regions)
    if postcode:
        actor_input["postcode"] = postcode
    if radius_km is not None:
        actor_input["radiusKm"] = radius_km
    if statuses:
        actor_input["statuses"] = list(statuses)
    if value_low is not None:
        actor_input["valueLow"] = value_low
    if value_high is not None:
        actor_input["valueHigh"] = value_high
    if published_from:
        actor_input["publishedFrom"] = published_from
    if published_to:
        actor_input["publishedTo"] = published_to

    result = await delegation.call(actor_input)
    return _map_tenders_result(result.items, result.run_meta)


def _map_tenders_result(
    items: list[dict[str, Any]],
    run_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape uk-tenders actor rows into a :class:`Tender` list.

    Each actor row is ``{"source": <source>, **Tender.model_dump}``
    where the outer ``source`` duplicates the inner ``Tender.source``
    (the ``**`` spread overwrites the literal with the model dump).
    We re-validate each row through :class:`Tender` so the agent sees
    canonical field names, and drop any rows that fail validation to
    an ``errors`` list for inspection.
    """

    tenders: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for row in items:
        if not isinstance(row, dict):
            parse_errors.append("uk-tenders row is not a dict")
            continue
        try:
            tender = Tender.model_validate(row)
        except ValidationError as exc:
            parse_errors.append(f"uk-tenders row validation failed: {exc}")
            continue
        tenders.append(tender.model_dump(mode="json", exclude_none=True))

    return {
        "count": len(tenders),
        "tenders": tenders,
        "run_meta": run_meta,
        "errors": parse_errors or None,
    }


async def _demographics_for_area(
    postcode: str,
    sources: list[str] | None = None,
    census_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Pull demographics via the hosted ``uk-demographics`` actor."""

    delegation = ApifyDelegation.resolve("uk-demographics")
    if delegation is None:
        raise DelegationError(
            "demographics_for_area requires a configured uk-demographics "
            "Apify delegation (set APIFY_API_TOKEN + APIFY_USERNAME or "
            "APIFY_ACTOR_UK_DEMOGRAPHICS)."
        )

    normalised = postcode.strip().upper()
    actor_input: dict[str, Any] = {
        "areas": [normalised],
        "sources": list(sources) if sources else list(_DEFAULT_DEMOGRAPHICS_SOURCES),
        "censusTables": list(census_tables)
        if census_tables
        else list(_DEFAULT_CENSUS_TABLES),
        "areaConcurrency": 1,
    }

    result = await delegation.call(actor_input)
    if not result.items:
        raise DelegationError(
            f"uk-demographics actor returned no rows for postcode {normalised!r}"
        )
    envelope = result.items[0]
    if not isinstance(envelope, dict):
        raise DelegationError("uk-demographics row is not a dict")
    return {
        **envelope,
        "run_meta": result.run_meta,
    }


async def _climate_risk_for_point(
    postcode: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    sources: list[str] | None = None,
    flood_radius_km: int = 10,
    coastal_radius_km: int = 5,
    bgs_landslide_radius_km: int = 5,
    air_quality_radius_km: int = 10,
    ukcp18_scenarios: list[str] | None = None,
    ukcp18_epochs: list[str] | None = None,
) -> dict[str, Any]:
    """Pull climate-risk assessment via the hosted ``uk-climate-risk`` actor."""

    delegation = ApifyDelegation.resolve("uk-climate-risk")
    if delegation is None:
        raise DelegationError(
            "climate_risk_for_point requires a configured uk-climate-risk "
            "Apify delegation (set APIFY_API_TOKEN + APIFY_USERNAME or "
            "APIFY_ACTOR_UK_CLIMATE_RISK)."
        )
    if postcode is None and (lat is None or lng is None):
        raise ValueError(
            "climate_risk_for_point requires either 'postcode' or both 'lat' and 'lng'"
        )

    point: dict[str, Any] = {}
    if postcode is not None:
        point["postcode"] = postcode.strip().upper()
    if lat is not None:
        point["lat"] = lat
    if lng is not None:
        point["lng"] = lng

    actor_input: dict[str, Any] = {
        "points": [point],
        "sources": list(sources) if sources else list(_DEFAULT_CLIMATE_SOURCES),
        "floodRadiusKm": flood_radius_km,
        "coastalRadiusKm": coastal_radius_km,
        "bgsLandslideRadiusKm": bgs_landslide_radius_km,
        "airQualityRadiusKm": air_quality_radius_km,
        "ukcp18Scenarios": list(ukcp18_scenarios)
        if ukcp18_scenarios
        else list(_DEFAULT_UKCP18_SCENARIOS),
        "ukcp18Epochs": list(ukcp18_epochs)
        if ukcp18_epochs
        else list(_DEFAULT_UKCP18_EPOCHS),
        "pointConcurrency": 1,
    }

    result = await delegation.call(actor_input)
    if not result.items:
        raise DelegationError(
            "uk-climate-risk actor returned no rows for the requested point"
        )
    envelope = result.items[0]
    if not isinstance(envelope, dict):
        raise DelegationError("uk-climate-risk row is not a dict")
    return {
        **envelope,
        "run_meta": result.run_meta,
    }


def build_apify_only_tools() -> list[StructuredTool]:
    """Return the actor-only tools whose hosting actor is resolvable.

    Each tool is appended to the returned list iff
    ``ApifyDelegation.resolve(<slug>)`` yields a client — i.e. iff the
    Apify token + username (or the actor-specific override) are
    configured for that specific actor. The check is cheap (a handful
    of ``os.getenv`` lookups) so we do it on every ``build_tools``
    call rather than caching; that lets scripts flip ``APIFY_API_TOKEN``
    at runtime and rebuild the tool list without restarting the
    process.
    """

    tools: list[StructuredTool] = []

    if ApifyDelegation.resolve("uk-auctions") is not None:
        tools.append(
            StructuredTool.from_function(
                name="find_auction_properties",
                description=(
                    "Find UK property auction lots across Allsop, Auction "
                    "House UK, Savills, and iamsold. Two modes: "
                    "'discover' (default) finds every upcoming auction "
                    "and enumerates lots, optionally filtered by "
                    "``sources`` or ``lot_type`` (residential / "
                    "commercial); or supply ``auction_ids`` + ``source`` "
                    "to pull one specific catalogue. Returns lots with "
                    "auction provenance, guide / sold price, address, "
                    "tenure, and full AuctionLot fields. Delegates to "
                    "the hosted uk-auctions Apify actor."
                ),
                args_schema=FindAuctionPropertiesArgs,
                coroutine=_find_auction_properties,
            )
        )

    if ApifyDelegation.resolve("uk-tenders") is not None:
        tools.append(
            StructuredTool.from_function(
                name="find_public_tenders",
                description=(
                    "Find UK public-sector procurement notices across "
                    "Contracts Finder (below-threshold) and Find a "
                    "Tender (above-threshold). Filter by CPV codes, "
                    "regions, postcode + radius (CF only), status, "
                    "value range, or publication date. Useful for "
                    "property-intel prefixes 45 (construction), 70 "
                    "(real estate services), 71 (architectural / "
                    "engineering), 77 (landscaping), 90 (waste). "
                    "Returns canonical Tender records. Delegates to "
                    "the hosted uk-tenders Apify actor."
                ),
                args_schema=FindPublicTendersArgs,
                coroutine=_find_public_tenders,
            )
        )

    if ApifyDelegation.resolve("uk-demographics") is not None:
        tools.append(
            StructuredTool.from_function(
                name="demographics_for_area",
                description=(
                    "Fetch a demographics pack for a UK postcode: ONS "
                    "Nomis labour market stats (unemployment, wages, "
                    "job density), Census 2021 bulk tables (population, "
                    "tenure, ethnicity, qualifications by default), IMD "
                    "2019 LSOA deprivation decile, and MHCLG household "
                    "projections + Housing Delivery Test. Returns a "
                    "per-source envelope with resolved LSOA / LA codes. "
                    "Delegates to the hosted uk-demographics Apify actor."
                ),
                args_schema=DemographicsForAreaArgs,
                coroutine=_demographics_for_area,
            )
        )

    if ApifyDelegation.resolve("uk-climate-risk") is not None:
        tools.append(
            StructuredTool.from_function(
                name="climate_risk_for_point",
                description=(
                    "Fetch a climate-risk pack for a UK point (postcode "
                    "or lat/lng): EA flood warnings + flood areas + "
                    "monitoring stations, NCERM 2024 coastal erosion + "
                    "SMP shoreline predictions, BGS GeoClimate "
                    "shrink-swell + landslide inventory, UKCP18 "
                    "regional climate projections, UKHSA radon band, "
                    "and DEFRA UK-AIR air-quality readings. Returns a "
                    "per-source envelope with resolved lat/lng. "
                    "Delegates to the hosted uk-climate-risk Apify actor."
                ),
                args_schema=ClimateRiskForPointArgs,
                coroutine=_climate_risk_for_point,
            )
        )

    return tools


__all__ = [
    "ClimateRiskForPointArgs",
    "DemographicsForAreaArgs",
    "FindAuctionPropertiesArgs",
    "FindPublicTendersArgs",
    "build_apify_only_tools",
]
