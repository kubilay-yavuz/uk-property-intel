"""Tests for :class:`CoastalErosionClient` against the NCERM 2024 WFS.

The EA retired the original NCERM WFS in early 2025 and moved the data
to ``/spatialdata/ncern-national-2024/wfs`` with a completely new layer
surface. See :mod:`uk_property_apis.coastal.client` for the full rationale.

These tests pin:

* The BBOX filter contract (axis order, CRS URN, output CRS).
* How each GeoJSON feature maps into :class:`ShorelinePrediction` /
  :class:`ErosionZone`.
* The Haversine post-filter (inland points shouldn't pull coastal
  frontages even when the BBOX is wide).
* That an empty FeatureCollection round-trips to an empty list (no
  exceptions, no surprise ``None`` blocks).

All live-endpoint calls are mocked via ``respx``. The fixtures under
``fixtures/coastal/`` are real, unedited NCERM 2024 responses captured
by the ``curl`` probes recorded in the client module docstring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from uk_property_apis.coastal import (
    ClimateUplift,
    CoastalErosionClient,
    HorizonYear,
    ManagementScenario,
    SMPPolicy,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "coastal"
_BASE_URL = "https://environment.data.gov.uk/spatialdata/ncern-national-2024/wfs"
# ``BaseAPIClient`` appends an empty path to the base URL as
# ``<base>/?<qs>``, so our mock matcher has to accept the trailing slash.
_URL_REGEX = (
    r"https://environment\.data\.gov\.uk/spatialdata/"
    r"ncern-national-2024/wfs/?\?.*"
)


def _load(name: str) -> dict[str, Any]:
    with (_FIXTURES / name).open() as fh:
        return json.load(fh)


# Happisburgh, Norfolk — the textbook English coastal-erosion hotspot.
_HAPPISBURGH_LAT = 52.8219
_HAPPISBURGH_LNG = 1.5333

# Cambridge city centre — 50+ km from the nearest coastline.
_CAMBRIDGE_LAT = 52.2053
_CAMBRIDGE_LNG = 0.1218


def _respx_route(
    type_name: str, response: dict[str, Any]
) -> respx.Route:
    """BBOX query routes share a base URL — a regex matcher on the
    NCERM WFS host lets one stub serve every ``typeName`` in a single
    test while still reading ``route.calls.last.request`` for per-call
    assertions."""
    del type_name
    return respx.get(url__regex=_URL_REGEX).mock(
        return_value=httpx.Response(200, json=response)
    )


class TestBBoxContract:
    """The WFS is fussy about CRS URN + axis order — pin the query shape."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_query_uses_bbox_and_epsg4326(self) -> None:
        route = respx.get(url__regex=_URL_REGEX).mock(
            return_value=httpx.Response(
                200, json={"type": "FeatureCollection", "features": []}
            )
        )
        async with CoastalErosionClient() as client:
            await client.erosion_risk_near(_HAPPISBURGH_LAT, _HAPPISBURGH_LNG, distance_km=5.0)
        assert route.called
        params = dict(route.calls.last.request.url.params)
        assert params["service"] == "WFS"
        assert params["version"] == "2.0.0"
        assert params["request"] == "GetFeature"
        assert params["outputFormat"] == "json"
        assert params["srsName"] == "urn:ogc:def:crs:EPSG::4326"
        assert params["typeName"] == "NCERM_Ground_Instability_Zone"
        bbox = params["bbox"]
        assert bbox.endswith(",urn:ogc:def:crs:EPSG::4326")
        min_lat, min_lon, max_lat, max_lon, _ = bbox.split(",")
        # axis order is (minLat, minLon, maxLat, maxLon)
        assert float(min_lat) < _HAPPISBURGH_LAT < float(max_lat)
        assert float(min_lon) < _HAPPISBURGH_LNG < float(max_lon)

    @respx.mock
    @pytest.mark.asyncio
    async def test_smp_layer_selection_honours_horizon_and_uplift(self) -> None:
        """``horizon=2105``, ``uplift=70`` must resolve to the
        ``NCERM_SMP_2105_70CC`` layer — wrong layer = wrong distance
        numbers, so this is load-bearing."""
        route = respx.get(url__regex=_URL_REGEX).mock(
            return_value=httpx.Response(
                200, json={"type": "FeatureCollection", "features": []}
            )
        )
        async with CoastalErosionClient() as client:
            await client.shoreline_predictions_near(
                _HAPPISBURGH_LAT,
                _HAPPISBURGH_LNG,
                horizon_year=HorizonYear.LONG_TERM,
                climate_uplift=ClimateUplift.CENTRAL,
            )
        params = dict(route.calls.last.request.url.params)
        assert params["typeName"] == "NCERM_SMP_2105_70CC"

    @respx.mock
    @pytest.mark.asyncio
    async def test_nfi_scenario_picks_nfi_layer(self) -> None:
        route = respx.get(url__regex=_URL_REGEX).mock(
            return_value=httpx.Response(
                200, json={"type": "FeatureCollection", "features": []}
            )
        )
        async with CoastalErosionClient() as client:
            await client.shoreline_predictions_near(
                _HAPPISBURGH_LAT,
                _HAPPISBURGH_LNG,
                scenario=ManagementScenario.NFI,
                horizon_year=2055,
                climate_uplift=95,
            )
        params = dict(route.calls.last.request.url.params)
        assert params["typeName"] == "NCERM_NFI_2055_95CC"


class TestShorelinePredictionsNear:
    """Mapping GeoJSON -> ShorelinePrediction + radius post-filter."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_happisburgh_populated(self) -> None:
        _respx_route(
            "NCERM_SMP_2055_0CC", _load("ncerm_smp_2055_0cc_happisburgh.json")
        )
        async with CoastalErosionClient() as client:
            results = await client.shoreline_predictions_near(
                _HAPPISBURGH_LAT,
                _HAPPISBURGH_LNG,
                distance_km=5.0,
            )
        assert results, "Happisburgh fixture should yield at least one frontage"
        first = results[0]
        assert first.management_scenario is ManagementScenario.SMP
        assert first.horizon_year is HorizonYear.NEAR_TERM
        assert first.climate_uplift is ClimateUplift.NONE
        assert first.smp_name == "Kelling Hard to Lowestoft"
        # All Kelling-to-Lowestoft frontages should carry one of the
        # documented SMP policy values.
        for row in results:
            assert row.medium_term_policy in SMPPolicy
            assert row.long_term_policy in SMPPolicy
            assert isinstance(row.predicted_erosion_distance_m, (float, type(None)))

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_feature_collection_returns_empty_list(self) -> None:
        _respx_route(
            "NCERM_SMP_2055_0CC",
            _load("ncerm_smp_2055_0cc_cambridge_empty.json"),
        )
        async with CoastalErosionClient() as client:
            results = await client.shoreline_predictions_near(
                _CAMBRIDGE_LAT,
                _CAMBRIDGE_LNG,
                distance_km=5.0,
            )
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_radius_post_filter_discards_far_frontages(self) -> None:
        """A 100 m radius around Happisburgh should keep maybe one polygon
        (or none) — the BBOX approximation is coarser than that, so the
        haversine post-filter has real work to do."""
        _respx_route(
            "NCERM_SMP_2055_0CC", _load("ncerm_smp_2055_0cc_happisburgh.json")
        )
        async with CoastalErosionClient() as client:
            wide = await client.shoreline_predictions_near(
                _HAPPISBURGH_LAT, _HAPPISBURGH_LNG, distance_km=5.0
            )
            tight = await client.shoreline_predictions_near(
                _HAPPISBURGH_LAT, _HAPPISBURGH_LNG, distance_km=0.1
            )
        assert len(tight) < len(wide)


class TestShorelinePredictionClosest:
    """``shoreline_prediction`` returns the closest frontage or ``None``."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_closest_frontage(self) -> None:
        _respx_route(
            "NCERM_SMP_2105_70CC",
            _load("ncerm_smp_2105_70cc_happisburgh.json"),
        )
        async with CoastalErosionClient() as client:
            closest = await client.shoreline_prediction(
                _HAPPISBURGH_LAT,
                _HAPPISBURGH_LNG,
                horizon_year=2105,
                climate_uplift=70,
                max_radius_km=5.0,
            )
        assert closest is not None
        assert closest.horizon_year is HorizonYear.LONG_TERM
        assert closest.climate_uplift is ClimateUplift.CENTRAL
        assert closest.smp_name == "Kelling Hard to Lowestoft"

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_none_for_inland_point(self) -> None:
        _respx_route(
            "NCERM_SMP_2055_0CC",
            _load("ncerm_smp_2055_0cc_cambridge_empty.json"),
        )
        async with CoastalErosionClient() as client:
            closest = await client.shoreline_prediction(
                _CAMBRIDGE_LAT,
                _CAMBRIDGE_LNG,
                max_radius_km=5.0,
            )
        assert closest is None


class TestErosionRiskNear:
    """``erosion_risk_near`` queries the instability-zone layer."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_scarborough_populated(self) -> None:
        _respx_route(
            "NCERM_Ground_Instability_Zone",
            _load("ncerm_instability_zone_scarborough.json"),
        )
        async with CoastalErosionClient() as client:
            zones = await client.erosion_risk_near(
                54.27,
                -0.40,
                distance_km=30.0,
            )
        assert zones, "Scarborough fixture should return non-empty zones"
        # The fixture carries zones from the Tyne-to-Flamborough-Head
        # SMP; every returned zone should reference that SMP.
        for zone in zones:
            assert zone.smp_name == "The Tyne to Flamborough Head"
            assert zone.local_authority == "Scarborough"
            # Zone IDs in the fixture are sequential 2-digit strings
            # starting at '07' — zero-padded is load-bearing, so assert
            # string-shape rather than a specific enum.
            assert zone.zone_id is not None
            assert zone.zone_id.isdigit() and len(zone.zone_id) == 2
            # Every fixture row has at least one policy-unit reference.
            assert zone.policy_units

    @respx.mock
    @pytest.mark.asyncio
    async def test_invalid_radius_is_rejected(self) -> None:
        async with CoastalErosionClient() as client:
            with pytest.raises(ValueError, match="distance_km must be > 0"):
                await client.erosion_risk_near(
                    _HAPPISBURGH_LAT, _HAPPISBURGH_LNG, distance_km=0
                )
