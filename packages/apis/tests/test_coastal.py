"""Tests for ``CoastalErosionClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from uk_property_apis._core.exceptions import ServerError
from uk_property_apis.coastal import CoastalErosionClient

_BASE_RE = re.compile(r"https://environment\.data\.gov\.uk/spatialdata/.*")

_EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _erosion_feature(
    zone_id: str = "EZ001",
    management_policy: str = "Hold the line",
    erosion_rate: float = 0.3,
    smp_ref: str = "SMP2-1",
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "ZONE_ID": zone_id,
            "MANAGEMENT_POLICY": management_policy,
            "EROSION_RATE": erosion_rate,
            "SMP_REF": smp_ref,
        },
    }


def _smp_feature(
    prediction_20: float = 5.0,
    prediction_50: float = 12.0,
    prediction_100: float = 28.0,
    policy_20: str = "Hold",
    policy_50: str = "Managed retreat",
    policy_100: str = "No active intervention",
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "PREDICTION_20": prediction_20,
            "PREDICTION_50": prediction_50,
            "PREDICTION_100": prediction_100,
            "POLICY_20": policy_20,
            "POLICY_50": policy_50,
            "POLICY_100": policy_100,
        },
    }


def _fc(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


# ---------------------------------------------------------------------------
# erosion_risk_near
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_erosion_risk_near_with_results() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_fc(_erosion_feature())))
    async with CoastalErosionClient() as client:
        zones = await client.erosion_risk_near(50.7, 1.6)
    assert len(zones) == 1
    assert zones[0].zone_id == "EZ001"
    assert zones[0].management_policy == "Hold the line"
    assert zones[0].erosion_rate_m_per_yr == 0.3
    assert zones[0].smp_reference == "SMP2-1"


@pytest.mark.asyncio
@respx.mock
async def test_erosion_risk_near_inland_empty() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with CoastalErosionClient() as client:
        zones = await client.erosion_risk_near(52.0, -1.5)
    assert zones == []


@pytest.mark.asyncio
@respx.mock
async def test_erosion_risk_near_default_distance() -> None:
    """Default distance_km=5 encodes 5000 metres in the CQL_FILTER."""
    captured_url: list[str] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured_url.append(str(request.url))
        return httpx.Response(200, json=_EMPTY_FC)

    respx.get(_BASE_RE).mock(side_effect=_capture)
    async with CoastalErosionClient() as client:
        await client.erosion_risk_near(50.7, 1.6)
    assert "5000" in captured_url[0]


# ---------------------------------------------------------------------------
# shoreline_prediction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_shoreline_prediction_50_year() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_fc(_smp_feature())))
    async with CoastalErosionClient() as client:
        pred = await client.shoreline_prediction(50.7, 1.6, epoch=50)
    assert pred is not None
    assert pred.epoch_years == 50
    assert pred.predicted_distance_m == 12.0
    assert pred.risk_category == "Managed retreat"


@pytest.mark.asyncio
@respx.mock
async def test_shoreline_prediction_20_year() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_fc(_smp_feature())))
    async with CoastalErosionClient() as client:
        pred = await client.shoreline_prediction(50.7, 1.6, epoch=20)
    assert pred is not None
    assert pred.epoch_years == 20
    assert pred.predicted_distance_m == 5.0
    assert pred.risk_category == "Hold"


@pytest.mark.asyncio
@respx.mock
async def test_shoreline_prediction_100_year() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_fc(_smp_feature())))
    async with CoastalErosionClient() as client:
        pred = await client.shoreline_prediction(50.7, 1.6, epoch=100)
    assert pred is not None
    assert pred.epoch_years == 100
    assert pred.predicted_distance_m == 28.0
    assert pred.risk_category == "No active intervention"


@pytest.mark.asyncio
@respx.mock
async def test_shoreline_prediction_no_polygon_returns_none() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with CoastalErosionClient() as client:
        pred = await client.shoreline_prediction(52.0, -1.5)
    assert pred is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_erosion_risk_server_error() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(500, json={}))
    async with CoastalErosionClient() as client:
        with pytest.raises(ServerError):
            await client.erosion_risk_near(50.7, 1.6)


@pytest.mark.asyncio
@respx.mock
async def test_shoreline_prediction_server_error() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(500, json={}))
    async with CoastalErosionClient() as client:
        with pytest.raises(ServerError):
            await client.shoreline_prediction(50.7, 1.6)
