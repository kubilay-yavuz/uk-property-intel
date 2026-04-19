"""Tests for OTPClient."""
from __future__ import annotations

import re
from datetime import datetime

import httpx
import pytest
import respx

from uk_property_geo.distance import Point
from uk_property_geo.otp import Isochrone, Itinerary, OTPClient

_BASE = "http://localhost:8080"

_JOURNEY_RESPONSE = {
    "plan": {
        "itineraries": [
            {
                "duration": 1800,
                "walkDistance": 500.0,
                "transitTime": 1200,
                "transfers": 1,
                "legs": [
                    {
                        "mode": "WALK",
                        "from": {"name": "Home"},
                        "to": {"name": "Bus Stop"},
                        "duration": 300,
                        "agencyName": None,
                    },
                    {
                        "mode": "BUS",
                        "from": {"name": "Bus Stop"},
                        "to": {"name": "Victoria"},
                        "duration": 1200,
                        "routeShortName": "11",
                        "agencyName": "TfL",
                    },
                ],
            }
        ]
    }
}

_ISOCHRONE_RESPONSE = {
    "features": [
        {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
        {"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]]}},
    ]
}


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_happy() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json=_JOURNEY_RESPONSE)
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12)
        )
    assert len(itineraries) == 1
    assert isinstance(itineraries[0], Itinerary)
    assert itineraries[0].duration_s == 1800


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_transit_walk() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json=_JOURNEY_RESPONSE)
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1),
            Point(lat=51.51, lng=-0.12),
            mode="TRANSIT,WALK",
        )
    assert itineraries[0].transfers == 1


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_walk_only() -> None:
    response = {"plan": {"itineraries": [{"duration": 900, "walkDistance": 1200.0, "transitTime": 0, "transfers": 0, "legs": []}]}}
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json=response)
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1),
            Point(lat=51.51, lng=-0.12),
            mode="WALK",
        )
    assert itineraries[0].transit_time_s == 0


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_no_itineraries() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json={"plan": {"itineraries": []}})
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12)
        )
    assert itineraries == []


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_with_depart_at() -> None:
    mock = respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json={"plan": {"itineraries": []}})
    )
    depart = datetime(2026, 4, 19, 9, 0, 0)
    async with OTPClient(_BASE) as client:
        await client.plan_journey(
            Point(lat=51.5, lng=-0.1),
            Point(lat=51.51, lng=-0.12),
            depart_at=depart,
        )
    assert mock.called
    called_url = str(mock.calls[0].request.url)
    assert "date=2026-04-19" in called_url or "date=" in called_url


@pytest.mark.asyncio
@respx.mock
async def test_isochrone_happy() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/isochrone.*")).mock(
        return_value=httpx.Response(200, json=_ISOCHRONE_RESPONSE)
    )
    async with OTPClient(_BASE) as client:
        isos = await client.isochrone(Point(lat=51.5, lng=-0.1), cutoff_minutes=[15, 30])
    assert len(isos) == 2
    assert all(isinstance(i, Isochrone) for i in isos)


@pytest.mark.asyncio
@respx.mock
async def test_isochrone_cutoffs() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/isochrone.*")).mock(
        return_value=httpx.Response(200, json=_ISOCHRONE_RESPONSE)
    )
    async with OTPClient(_BASE) as client:
        isos = await client.isochrone(Point(lat=51.5, lng=-0.1), cutoff_minutes=[15, 30])
    assert isos[0].cutoff_minutes == 15
    assert isos[1].cutoff_minutes == 30


@pytest.mark.asyncio
@respx.mock
async def test_isochrone_custom_cutoffs() -> None:
    single_feature = {"features": [{"geometry": {"type": "Polygon", "coordinates": []}}]}
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/isochrone.*")).mock(
        return_value=httpx.Response(200, json=single_feature)
    )
    async with OTPClient(_BASE) as client:
        isos = await client.isochrone(Point(lat=51.5, lng=-0.1), cutoff_minutes=[45])
    assert isos[0].cutoff_minutes == 45


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_legs_parsed() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json=_JOURNEY_RESPONSE)
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12)
        )
    legs = itineraries[0].legs
    assert len(legs) == 2


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_leg_mode() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json=_JOURNEY_RESPONSE)
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12)
        )
    assert itineraries[0].legs[0].mode == "WALK"
    assert itineraries[0].legs[1].mode == "BUS"
    assert itineraries[0].legs[1].agency == "TfL"


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_http_error() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(500, json={})
    )
    async with OTPClient(_BASE) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.plan_journey(Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12))


@pytest.mark.asyncio
@respx.mock
async def test_otp_context_manager() -> None:
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/isochrone.*")).mock(
        return_value=httpx.Response(200, json={"features": []})
    )
    async with OTPClient(_BASE) as client:
        isos = await client.isochrone(Point(lat=51.5, lng=-0.1))
    assert isos == []


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_multiple_itineraries() -> None:
    response = {
        "plan": {
            "itineraries": [
                {"duration": 1800, "walkDistance": 500.0, "transitTime": 1200, "transfers": 1, "legs": []},
                {"duration": 2100, "walkDistance": 300.0, "transitTime": 1500, "transfers": 0, "legs": []},
            ]
        }
    }
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json=response)
    )
    async with OTPClient(_BASE) as client:
        itineraries = await client.plan_journey(
            Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12)
        )
    assert len(itineraries) == 2


@pytest.mark.asyncio
@respx.mock
async def test_isochrone_geometry_stored() -> None:
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    respx.get(re.compile(r"http://localhost:8080/otp/routers/default/isochrone.*")).mock(
        return_value=httpx.Response(200, json={"features": [{"geometry": poly}]})
    )
    async with OTPClient(_BASE) as client:
        isos = await client.isochrone(Point(lat=51.5, lng=-0.1), cutoff_minutes=[15])
    assert isos[0].geometry == poly


@pytest.mark.asyncio
@respx.mock
async def test_plan_journey_bus_only_mode() -> None:
    mock = respx.get(re.compile(r"http://localhost:8080/otp/routers/default/plan.*")).mock(
        return_value=httpx.Response(200, json={"plan": {"itineraries": []}})
    )
    async with OTPClient(_BASE) as client:
        await client.plan_journey(
            Point(lat=51.5, lng=-0.1),
            Point(lat=51.51, lng=-0.12),
            mode="BUS,WALK",
        )
    assert mock.called
