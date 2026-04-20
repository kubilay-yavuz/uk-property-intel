"""Tests for :class:`BGSClient` — GeoClimate + landslide inventory.

After BGS withdrew the A–E graded ``BGS_Hazards/GeoHazardsEngland``
service in 2024, the free, programmatically-queryable hazard surface
reshaped around three feeds:

* ``GeoIndex_Onshore/geoclimate_basic/MapServer/{0,1,2}`` — GeoClimate
  Basic shrink-swell at 2030 / 2050 / 2080 (averaged projection).
* ``GeoIndex_Onshore/geoclimate_basic/MapServer/{3,4}`` — GeoClimate
  UKCP18 shrink-swell at 2030 / 2070.
* ``GeoIndex_Onshore/hazards/MapServer/2`` — open landslide inventory.

These tests pin the request shape (ArcGIS query parameters,
``inSR=4326``, point-intersect vs point-buffer) and the response
mapping from ArcGIS JSON to the Pydantic models. Fixtures are real
captures from the BGS public endpoints checked into
``fixtures/bgs/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from uk_property_apis.bgs import (
    BGSClient,
    ClimateProjection,
    ShrinkSwellClass,
    ShrinkSwellHorizon,
)

_BASE_URL = "https://map.bgs.ac.uk/arcgis/rest/services"
_FIXTURES = Path(__file__).parent / "fixtures" / "bgs"

# BaseAPIClient pads the base URL with a trailing slash before concatenating
# the per-layer path, so the on-wire request carries a single
# ``/services/<layer>/query`` segment. Anchor the regex to that prefix and
# allow any query string — individual tests assert on specific params
# via ``call.request.url.params`` rather than trying to match via regex.
_URL_REGEX = r"https://map\.bgs\.ac\.uk/arcgis/rest/services/[A-Za-z_./0-9]+/query\?.*"


def _load(name: str) -> dict[str, Any]:
    """Load a recorded ArcGIS REST response from ``fixtures/bgs/``."""

    return json.loads((_FIXTURES / name).read_text())


def _route_for(layer_suffix: str, payload: dict[str, Any]) -> respx.Route:
    """Match ``<base>/<layer_suffix>/query?...`` and return ``payload``.

    The ``layer_suffix`` identifies which MapServer layer the test is
    stubbing (e.g. ``geoclimate_basic/MapServer/0``); we fold it into a
    strict regex so accidental cross-layer bleed from a misconfigured
    client surfaces as ``AllMockedAssertionError`` rather than as a
    fake "correct" response.
    """

    pattern = (
        r"https://map\.bgs\.ac\.uk/arcgis/rest/services/"
        + layer_suffix.replace("/", r"\/").replace(".", r"\.")
        + r"/query\?.*"
    )
    return respx.get(url__regex=pattern).mock(
        return_value=httpx.Response(200, json=payload)
    )


# --- Request-shape contract ------------------------------------------------


class TestRequestShape:
    """The ArcGIS REST dialect has a few gotchas — pin them here."""

    @pytest.mark.asyncio
    async def test_shrink_swell_is_a_point_intersect_query(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """``shrink_swell_at`` issues a point-intersect query at ``(lng, lat)``."""

        route = _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/1",
            _load("geoclimate_basic_2050_oxford.json"),
        )
        async with BGSClient() as client:
            await client.shrink_swell_at(
                51.752, -1.2577, horizon=ShrinkSwellHorizon.H_2050
            )
        assert route.called
        req = route.calls.last.request
        params = req.url.params
        # ArcGIS wants ``lng,lat`` for a WGS-84 point.
        assert params["geometry"] == "-1.2577,51.752"
        assert params["geometryType"] == "esriGeometryPoint"
        assert params["inSR"] == "4326"
        assert params["spatialRel"] == "esriSpatialRelIntersects"
        assert params["f"] == "json"
        # No radius for a pure intersect query.
        assert "distance" not in params

    @pytest.mark.asyncio
    async def test_landslide_query_is_a_buffered_point(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """``landslides_near`` converts km radius to metres with
        ``units=esriSRUnit_Meter`` and requests geometry back."""

        route = _route_for(
            "GeoIndex_Onshore/hazards/MapServer/2",
            _load("landslides_ventnor_iow_5km.json"),
        )
        async with BGSClient() as client:
            await client.landslides_near(50.5947, -1.2076, distance_km=5, limit=25)
        assert route.called
        params = route.calls.last.request.url.params
        assert params["distance"] == "5000"
        assert params["units"] == "esriSRUnit_Meter"
        # Need geometry back so we can Haversine-post-filter the distance_km.
        assert params["returnGeometry"] == "true"
        assert params["resultRecordCount"] == "25"

    @pytest.mark.asyncio
    async def test_shrink_swell_unknown_combo_rejects(self) -> None:
        """Only BGS-published ``(projection, horizon)`` combos are allowed."""

        async with BGSClient() as client:
            with pytest.raises(ValueError, match="No BGS GeoClimate layer"):
                await client.shrink_swell_at(
                    51.5,
                    -0.1,
                    horizon=ShrinkSwellHorizon.H_2080,
                    projection=ClimateProjection.UKCP18,
                )


# --- Shrink-swell mapping --------------------------------------------------


class TestShrinkSwell:
    @pytest.mark.asyncio
    async def test_shrink_swell_at_maps_class_and_keeps_metadata(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """Oxford @2030 basic is ``Improbable`` — legend + version preserved."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/0",
            _load("geoclimate_basic_2030_oxford.json"),
        )
        async with BGSClient() as client:
            assessment = await client.shrink_swell_at(
                51.752, -1.2577, horizon=ShrinkSwellHorizon.H_2030
            )
        assert assessment.projection == ClimateProjection.BASIC
        assert int(assessment.horizon_year) == 2030
        assert assessment.susceptibility == ShrinkSwellClass.IMPROBABLE
        assert assessment.legend is not None
        assert "clay shrink-swell" in assessment.legend
        assert assessment.version is not None
        assert assessment.version.startswith("GeoClimateGB_ShrinkSwell_2030")
        # Raw attrs preserved for callers who want the layer's OBJECTID /
        # shape_area for audit logs.
        assert assessment.properties["CLASS"] == "Improbable"

    @pytest.mark.asyncio
    async def test_shrink_swell_at_returns_possible_at_2050(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """Oxford escalates to ``Possible`` at the 2050 horizon."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/1",
            _load("geoclimate_basic_2050_oxford.json"),
        )
        async with BGSClient() as client:
            assessment = await client.shrink_swell_at(
                51.752, -1.2577, horizon=ShrinkSwellHorizon.H_2050
            )
        assert assessment.susceptibility == ShrinkSwellClass.POSSIBLE
        assert int(assessment.horizon_year) == 2050

    @pytest.mark.asyncio
    async def test_shrink_swell_at_handles_improbable_ukcp18(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """UKCP18 adds an ``Improbable`` class outside the Basic trichotomy."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/3",
            _load("geoclimate_ukcp18_2030_oxford.json"),
        )
        async with BGSClient() as client:
            assessment = await client.shrink_swell_at(
                51.752,
                -1.2577,
                horizon=ShrinkSwellHorizon.H_2030,
                projection=ClimateProjection.UKCP18,
            )
        assert assessment.projection == ClimateProjection.UKCP18
        assert assessment.susceptibility == ShrinkSwellClass.IMPROBABLE

    @pytest.mark.asyncio
    async def test_shrink_swell_at_maps_offshore_to_none(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """Offshore / out-of-coverage points return ``ShrinkSwellClass.NONE``."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/0",
            _load("geoclimate_basic_2030_offshore_empty.json"),
        )
        async with BGSClient() as client:
            assessment = await client.shrink_swell_at(
                49.5, -6.5, horizon=ShrinkSwellHorizon.H_2030
            )
        assert assessment.susceptibility == ShrinkSwellClass.NONE

    @pytest.mark.asyncio
    async def test_shrink_swell_trajectory_returns_all_basic_horizons(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """``shrink_swell_trajectory`` fans out across 2030 / 2050 / 2080."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/0",
            _load("geoclimate_basic_2030_oxford.json"),
        )
        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/1",
            _load("geoclimate_basic_2050_oxford.json"),
        )
        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/2",
            _load("geoclimate_basic_2080_oxford.json"),
        )
        async with BGSClient() as client:
            trajectory = await client.shrink_swell_trajectory(
                51.752, -1.2577, projection=ClimateProjection.BASIC
            )
        horizons = [int(a.horizon_year) for a in trajectory]
        # Oldest horizon first so consumers can scan for escalation.
        assert horizons == [2030, 2050, 2080]
        classes = [a.susceptibility for a in trajectory]
        # Oxford shows an ``Improbable → Possible → Probable`` escalation
        # across the three GeoClimate Basic horizons — this is the
        # canonical escalation pattern the field cares about.
        assert classes == [
            ShrinkSwellClass.IMPROBABLE,
            ShrinkSwellClass.POSSIBLE,
            ShrinkSwellClass.PROBABLE,
        ]


# --- Landslide inventory ---------------------------------------------------


class TestLandslides:
    @pytest.mark.asyncio
    async def test_landslides_near_parses_and_sorts_by_distance(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """Ventnor (IoW) fixture has 29 events; each gets a Haversine km."""

        _route_for(
            "GeoIndex_Onshore/hazards/MapServer/2",
            _load("landslides_ventnor_iow_5km.json"),
        )
        query_lat, query_lng = 50.5947, -1.2076
        async with BGSClient() as client:
            events = await client.landslides_near(
                query_lat, query_lng, distance_km=5, limit=30
            )
        assert events, "expected the Ventnor fixture to be populated"
        # Strictly non-decreasing distance after client-side sort.
        distances = [e.distance_km for e in events if e.distance_km is not None]
        assert distances == sorted(distances)
        first = events[0]
        # Each event carries a non-negative km distance tagged onto it.
        assert first.distance_km is not None and first.distance_km >= 0
        assert first.distance_km <= 5.0
        assert first.landslide_id is not None
        assert first.locality and "Isle of Wight" in first.locality
        assert first.latitude is not None and first.longitude is not None
        # ``UNKNOWN`` is scrubbed from the date fields so downstream
        # consumers don't treat the literal string as a real year —
        # real events keep their year, empty ones become ``None``.
        for event in events:
            assert event.first_known_year != "UNKNOWN"
            assert event.last_known_year != "UNKNOWN"
        # The known-fixture "Ventnor" canonical landslide record is in the set.
        names = {e.name for e in events}
        assert "Ventnor" in names

    @pytest.mark.asyncio
    async def test_landslides_near_empty_returns_empty_list(
        self, respx_mock: respx.MockRouter
    ) -> None:
        _route_for(
            "GeoIndex_Onshore/hazards/MapServer/2",
            _load("landslides_ely_empty.json"),
        )
        async with BGSClient() as client:
            events = await client.landslides_near(52.398, 0.2625, distance_km=10)
        assert events == []

    @pytest.mark.parametrize("radius_km", [0, -1.0])
    @pytest.mark.asyncio
    async def test_landslides_invalid_radius_rejects(self, radius_km: float) -> None:
        async with BGSClient() as client:
            with pytest.raises(ValueError, match="distance_km"):
                await client.landslides_near(50.5947, -1.2076, distance_km=radius_km)


# --- Aggregate -------------------------------------------------------------


class TestGeohazardsAt:
    @pytest.mark.asyncio
    async def test_geohazards_at_combines_trajectory_and_landslides(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """``geohazards_at`` fans out shrink-swell + landslides for one point."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/0",
            _load("geoclimate_basic_2030_oxford.json"),
        )
        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/1",
            _load("geoclimate_basic_2050_oxford.json"),
        )
        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/2",
            _load("geoclimate_basic_2080_oxford.json"),
        )
        _route_for(
            "GeoIndex_Onshore/hazards/MapServer/2",
            _load("landslides_ventnor_iow_5km.json"),
        )
        async with BGSClient() as client:
            assessment = await client.geohazards_at(
                51.752, -1.2577, landslide_radius_km=5, landslide_limit=10
            )
        assert len(assessment.shrink_swell_trajectory) == 3
        assert assessment.has_shrink_swell_risk is True
        # 10 landslides preserved in the slice (fixture has 29; resultRecordCount was client-side).
        assert len(assessment.landslides_nearby) > 0
        assert assessment.has_landslide_risk is True
        assert assessment.landslide_search_radius_km == 5

    @pytest.mark.asyncio
    async def test_geohazards_at_inland_has_no_landslide_risk(
        self, respx_mock: respx.MockRouter
    ) -> None:
        """Inland fenland: shrink-swell may exist, landslide list is empty."""

        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/0",
            _load("geoclimate_basic_2030_inverness_improbable.json"),
        )
        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/1",
            _load("geoclimate_basic_2030_inverness_improbable.json"),
        )
        _route_for(
            "GeoIndex_Onshore/geoclimate_basic/MapServer/2",
            _load("geoclimate_basic_2030_inverness_improbable.json"),
        )
        _route_for(
            "GeoIndex_Onshore/hazards/MapServer/2",
            _load("landslides_ely_empty.json"),
        )
        async with BGSClient() as client:
            assessment = await client.geohazards_at(
                52.398, 0.2625, landslide_radius_km=10
            )
        assert assessment.has_shrink_swell_risk is False
        assert assessment.has_landslide_risk is False
        assert assessment.landslides_nearby == []
