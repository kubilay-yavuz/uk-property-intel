"""Tests for ``FloodClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.flood import FloodClient


@pytest.mark.asyncio
@respx.mock
async def test_active_floods_near_happy() -> None:
    respx.get(re.compile(r"https://environment\.data\.gov\.uk/flood-monitoring/id/floods\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "@context": "http://environment.data.gov.uk/flood-monitoring/meta/context.jsonld",
                "meta": {},
                "items": [
                    {
                        "@id": "http://example/flood/1",
                        "description": "River rising",
                        "severity": "Flood Alert",
                        "riverOrSea": "Thames",
                    },
                ],
            },
        ),
    )
    async with FloodClient() as client:
        rows = await client.active_floods_near(51.5, -0.1, distance_km=5)
    assert rows[0].description == "River rising"


@pytest.mark.asyncio
@respx.mock
async def test_active_floods_near_404() -> None:
    respx.get(re.compile(r"https://environment\.data\.gov\.uk/flood-monitoring/id/floods\?.*")).mock(
        return_value=httpx.Response(404, json={}),
    )
    async with FloodClient() as client:
        with pytest.raises(NotFoundError):
            await client.active_floods_near(51.5, -0.1)


@pytest.mark.asyncio
@respx.mock
async def test_flood_areas_near() -> None:
    respx.get(re.compile(r"https://environment\.data\.gov\.uk/flood-monitoring/id/floodAreas\?.*")).mock(
        return_value=httpx.Response(200, json={"items": [{"@id": "x", "label": "Area A"}]}),
    )
    async with FloodClient() as client:
        areas = await client.flood_areas_near(51.0, 0.0, distance_km=2)
    assert areas[0].label == "Area A"


@pytest.mark.asyncio
@respx.mock
async def test_stations_near() -> None:
    respx.get(re.compile(r"https://environment\.data\.gov\.uk/flood-monitoring/id/stations\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"@id": "s1", "label": "Gauge", "lat": 51.0, "long": -0.2}]},
        ),
    )
    async with FloodClient() as client:
        sts = await client.stations_near(51.0, -0.2, distance_km=1)
    assert sts[0].label == "Gauge"
