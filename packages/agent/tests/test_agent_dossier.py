"""Tests for :func:`build_property_dossier`.

We exercise every branch of the builder with stub clients wired through
:class:`ToolContext`. Each stub is an ``async with``-aware object that
returns the exact pydantic models the real clients emit, so we also
catch schema drift if one of the upstream models changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from uk_property_agent import (
    DossierOptions,
    PropertyDossier,
    ToolContext,
    build_property_dossier,
)
from uk_property_apis import ApplicationDetail
from uk_property_apis.idox import KNOWN_COUNCILS
from uk_property_apis.land_registry.models import PricePaidRecord
from uk_property_apis.police.models import CrimeStatsSummary
from uk_property_apis.postcodes.models import PostcodeResult
from uk_property_geo import AmenityCategory
from uk_property_geo.overpass import AmenityHit


@dataclass
class _StubPostcodes:
    """Fake ``PostcodesClient`` that returns a baked ``PostcodeResult``.

    ``admin_district`` defaults to "Lambeth" so the planning branch fires
    against the reference ArcGIS council; tests that want to skip planning
    pass ``admin_district="Elsewhere"`` or set ``include_planning=False``.
    """

    postcode: str = "SE11 6QF"
    latitude: float = 51.49
    longitude: float = -0.11
    admin_district: str = "Lambeth"
    calls: list[str] = field(default_factory=list)

    async def __aenter__(self) -> _StubPostcodes:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def lookup_postcode(self, postcode: str) -> PostcodeResult:
        self.calls.append(postcode)
        return PostcodeResult(
            postcode=self.postcode,
            latitude=self.latitude,
            longitude=self.longitude,
            country="England",
            region="London",
            admin_district=self.admin_district,
            admin_ward="Prince's",
            outcode=self.postcode.split()[0],
        )


@dataclass
class _StubLandRegistry:
    rows: list[PricePaidRecord] = field(default_factory=list)
    raise_exc: Exception | None = None

    async def __aenter__(self) -> _StubLandRegistry:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def search_by_postcode(
        self, postcode: str, *, expand: bool = False
    ) -> list[PricePaidRecord]:
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.rows)


@dataclass
class _StubOverpass:
    hits: list[AmenityHit] = field(default_factory=list)

    async def __aenter__(self) -> _StubOverpass:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def amenities_near(
        self,
        lat: float,
        lng: float,
        *,
        radius_m: float,
        categories: list[AmenityCategory],
    ) -> list[AmenityHit]:
        return [h for h in self.hits if h.category in categories]


class _FakeEntityPage:
    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self.entities = entities


@dataclass
class _StubPlanning:
    listed_count: int = 0

    async def __aenter__(self) -> _StubPlanning:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def fetch_listed_buildings_near(
        self, lat: float, lng: float, *, radius_m: float
    ) -> _FakeEntityPage:
        return _FakeEntityPage([{"name": f"listed-{i}"} for i in range(self.listed_count)])


@dataclass
class _StubPolice:
    summary: CrimeStatsSummary | None = None
    raise_exc: Exception | None = None

    async def __aenter__(self) -> _StubPolice:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def crime_stats_near(
        self, lat: float, lng: float, *, months_back: int
    ) -> CrimeStatsSummary:
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.summary or CrimeStatsSummary(
            months=["2026-01"], by_category={}, by_month_category=[]
        )


class _FakeFloodWarning:
    def __init__(self, severity_level: int) -> None:
        self.severity_level = severity_level


@dataclass
class _StubFlood:
    warnings: list[_FakeFloodWarning] = field(default_factory=list)

    async def __aenter__(self) -> _StubFlood:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def active_floods_near(self, lat: float, lng: float) -> list[_FakeFloodWarning]:
        return list(self.warnings)


class _FakeEPCRow:
    def __init__(self, rating: str) -> None:
        self.current_energy_rating = rating


class _FakeEPCPage:
    def __init__(self, rows: list[_FakeEPCRow]) -> None:
        self.rows = rows
        self.next_search_after = None


@dataclass
class _StubEPC:
    rows: list[_FakeEPCRow] = field(default_factory=list)

    async def __aenter__(self) -> _StubEPC:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def search_domestic(
        self, *, postcode: str, size: int = 100
    ) -> _FakeEPCPage:
        return _FakeEPCPage(self.rows)


@dataclass
class _StubArcGISPlanning:
    apps: list[ApplicationDetail] = field(default_factory=list)

    def __call__(self, config: Any) -> _StubArcGISPlanning:
        return self

    async def __aenter__(self) -> _StubArcGISPlanning:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def recent_applications(
        self, *, since: Any, max_results: int
    ) -> list[ApplicationDetail]:
        return list(self.apps)


def _ctx_with_all_stubs(
    *,
    postcodes: _StubPostcodes | None = None,
    lr_rows: list[PricePaidRecord] | None = None,
    overpass_hits: list[AmenityHit] | None = None,
    listed_count: int = 0,
    crime_summary: CrimeStatsSummary | None = None,
    floods: list[_FakeFloodWarning] | None = None,
    epc_rows: list[_FakeEPCRow] | None = None,
    planning_apps: list[ApplicationDetail] | None = None,
    police_exc: Exception | None = None,
    lr_exc: Exception | None = None,
) -> ToolContext:
    pc = postcodes or _StubPostcodes()
    lr = _StubLandRegistry(rows=lr_rows or [], raise_exc=lr_exc)
    op = _StubOverpass(hits=overpass_hits or [])
    pl = _StubPlanning(listed_count=listed_count)
    police = _StubPolice(summary=crime_summary, raise_exc=police_exc)
    flood = _StubFlood(warnings=floods or [])
    epc = _StubEPC(rows=epc_rows or [])
    arcgis = _StubArcGISPlanning(apps=planning_apps or [])
    ctx = ToolContext()
    ctx.postcodes_factory = lambda: pc
    ctx.land_registry_factory = lambda: lr
    ctx.overpass_factory = lambda: op
    ctx.planning_factory = lambda: pl
    ctx.police_factory = lambda: police
    ctx.flood_factory = lambda: flood
    ctx.epc_factory = lambda: epc if epc_rows is not None else None
    ctx.arcgis_planning_factory = arcgis
    return ctx


@pytest.mark.asyncio
async def test_dossier_all_sources_populate() -> None:
    ppd_rows = [
        PricePaidRecord(
            transaction_id="T1",
            price=650_000,
            transfer_date="2024-06-01",
            property_type="F",
            postcode="SE11 6QF",
            paon="10",
            street="Oval Way",
        ),
        PricePaidRecord(
            transaction_id="T2",
            price=700_000,
            transfer_date="2024-04-20",
            property_type="F",
            postcode="SE11 6QF",
            paon="12",
            street="Oval Way",
        ),
        PricePaidRecord(
            transaction_id="T3",
            price=800_000,
            transfer_date="2024-02-15",
            property_type="F",
            postcode="SE11 6QF",
            paon="14",
            street="Oval Way",
        ),
    ]
    hits = [
        AmenityHit(
            id=1,
            osm_type="node",
            category=AmenityCategory.RAIL_STATION,
            name="Vauxhall",
            lat=51.49,
            lng=-0.12,
            distance_m=520.0,
        ),
        AmenityHit(
            id=2,
            osm_type="node",
            category=AmenityCategory.BUS_STOP,
            name="Kennington Lane",
            lat=51.49,
            lng=-0.11,
            distance_m=80.0,
        ),
    ]
    crime = CrimeStatsSummary(
        months=["2026-01", "2026-02", "2026-03"],
        by_category={"burglary": 3, "violent-crime": 7, "anti-social-behaviour": 12},
        by_month_category=[],
    )
    floods = [_FakeFloodWarning(severity_level=2)]
    epc_rows = [_FakeEPCRow("B"), _FakeEPCRow("C"), _FakeEPCRow("B")]
    ctx = _ctx_with_all_stubs(
        lr_rows=ppd_rows,
        overpass_hits=hits,
        listed_count=4,
        crime_summary=crime,
        floods=floods,
        epc_rows=epc_rows,
    )

    dossier = await build_property_dossier(
        postcode="SE11 6QF",
        ctx=ctx,
        options=DossierOptions(property_type="F", years_back=3),
    )

    assert isinstance(dossier, PropertyDossier)
    assert dossier.errors == []
    assert dossier.location is not None
    assert dossier.location.admin_district == "Lambeth"

    assert dossier.avm is not None
    assert dossier.avm.estimate_gbp is not None
    assert dossier.avm.comparables_used == 3
    assert dossier.avm.ppd_rows_fetched == 3
    assert dossier.avm.property_type == "F"

    assert dossier.ppd is not None
    assert dossier.ppd.rows_fetched == 3
    assert dossier.ppd.median_price_gbp == 700_000
    assert dossier.ppd.min_price_gbp == 650_000
    assert dossier.ppd.max_price_gbp == 800_000
    assert dossier.ppd.recent_sales[0].transfer_date == "2024-06-01"

    assert dossier.neighbourhood is not None
    assert dossier.neighbourhood.radius_m == 800.0
    bus_stop = next(
        c for c in dossier.neighbourhood.categories if c.category == "bus_stop"
    )
    assert bus_stop.count == 1
    assert bus_stop.nearest_name == "Kennington Lane"
    assert dossier.neighbourhood.listed_buildings_within_radius == 4

    assert dossier.crime is not None
    assert dossier.crime.total_incidents == 22
    assert [c["category"] for c in dossier.crime.top_categories[:2]] == [
        "anti-social-behaviour",
        "violent-crime",
    ]

    assert dossier.flood is not None
    assert dossier.flood.active_warnings == 1
    assert dossier.flood.worst_severity_level == 2

    assert dossier.epc is not None
    assert dossier.epc.count == 3
    assert dossier.epc.top_ratings == {"B": 2, "C": 1}

    assert dossier.planning is not None
    assert dossier.planning.council_slug == "lambeth"
    assert dossier.planning.council_name == KNOWN_COUNCILS["lambeth"].name
    assert dossier.planning.transport == "arcgis"


@pytest.mark.asyncio
async def test_postcode_failure_still_produces_dossier() -> None:
    class _FailingPostcodes(_StubPostcodes):
        async def lookup_postcode(self, postcode: str) -> PostcodeResult:
            raise RuntimeError("postcodes.io down")

    ctx = _ctx_with_all_stubs(postcodes=_FailingPostcodes())
    dossier = await build_property_dossier(postcode="SW1A 1AA", ctx=ctx)

    assert dossier.postcode == "SW1A 1AA"
    assert dossier.location is None
    assert dossier.neighbourhood is None
    assert dossier.crime is None
    assert dossier.flood is None
    assert dossier.planning is None
    assert any(e.source == "postcodes" for e in dossier.errors)
    assert dossier.avm is None or dossier.ppd is not None


@pytest.mark.asyncio
async def test_land_registry_failure_is_captured() -> None:
    ctx = _ctx_with_all_stubs(lr_exc=RuntimeError("Land Registry 500"))
    dossier = await build_property_dossier(postcode="SE11 6QF", ctx=ctx)

    assert dossier.avm is None
    assert dossier.ppd is None
    assert any(e.source == "land-registry" for e in dossier.errors)


@pytest.mark.asyncio
async def test_crime_exception_captured_but_other_blocks_survive() -> None:
    ctx = _ctx_with_all_stubs(police_exc=TimeoutError("police API slow"))
    dossier = await build_property_dossier(postcode="SE11 6QF", ctx=ctx)

    assert dossier.crime is None
    assert dossier.neighbourhood is not None
    assert dossier.flood is not None
    assert any(
        e.source == "police" and e.error_type == "TimeoutError"
        for e in dossier.errors
    )


@pytest.mark.asyncio
async def test_epc_block_skipped_when_factory_missing() -> None:
    ctx = _ctx_with_all_stubs()
    ctx.epc_factory = None
    dossier = await build_property_dossier(postcode="SE11 6QF", ctx=ctx)

    assert dossier.epc is None


@pytest.mark.asyncio
async def test_planning_skipped_for_unknown_district() -> None:
    ctx = _ctx_with_all_stubs(postcodes=_StubPostcodes(admin_district="Elsewhere"))
    dossier = await build_property_dossier(postcode="SE11 6QF", ctx=ctx)

    assert dossier.planning is None
    assert dossier.location is not None
    assert dossier.location.admin_district == "Elsewhere"


@pytest.mark.asyncio
async def test_include_planning_false_skips_planning() -> None:
    ctx = _ctx_with_all_stubs()
    dossier = await build_property_dossier(
        postcode="SE11 6QF",
        ctx=ctx,
        options=DossierOptions(include_planning=False),
    )
    assert dossier.planning is None


@pytest.mark.asyncio
async def test_empty_ppd_returns_zero_rows_block() -> None:
    ctx = _ctx_with_all_stubs(lr_rows=[])
    dossier = await build_property_dossier(postcode="SE11 6QF", ctx=ctx)

    assert dossier.ppd is not None
    assert dossier.ppd.rows_fetched == 0
    assert dossier.ppd.median_price_gbp is None
    assert dossier.ppd.recent_sales == []
    assert dossier.avm is None or dossier.avm.comparables_used == 0


@pytest.mark.asyncio
async def test_worst_severity_is_min_severity_level() -> None:
    floods = [
        _FakeFloodWarning(severity_level=3),
        _FakeFloodWarning(severity_level=1),
        _FakeFloodWarning(severity_level=4),
    ]
    ctx = _ctx_with_all_stubs(floods=floods)
    dossier = await build_property_dossier(postcode="SE11 6QF", ctx=ctx)

    assert dossier.flood is not None
    assert dossier.flood.active_warnings == 3
    assert dossier.flood.worst_severity_level == 1
