"""Tests for OSRMClient."""
from __future__ import annotations

import re

import httpx
import pytest
import respx

from uk_property_geo.distance import Point
from uk_property_geo.osrm import NearestResult, OSRMClient, Route, TravelTimeMatrix

_BASE = "http://localhost:5000"


@pytest.mark.asyncio
@respx.mock
async def test_route_happy() -> None:
    respx.get(re.compile(r"http://localhost:5000/route/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "distance": 1234.5,
                        "duration": 180.0,
                        "geometry": "abc123",
                        "legs": [{"steps": []}],
                    }
                ]
            },
        )
    )
    async with OSRMClient(_BASE) as client:
        route = await client.route(Point(lat=51.5, lng=-0.1), Point(lat=51.6, lng=-0.2))
    assert isinstance(route, Route)
    assert route.distance_m == 1234.5
    assert route.duration_s == 180.0


@pytest.mark.asyncio
@respx.mock
async def test_route_distance_duration() -> None:
    respx.get(re.compile(r"http://localhost:5000/route/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200,
            json={"routes": [{"distance": 5000.0, "duration": 600.0, "legs": []}]},
        )
    )
    async with OSRMClient(_BASE) as client:
        route = await client.route(Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.11))
    assert route.distance_m == 5000.0
    assert route.duration_s == 600.0


@pytest.mark.asyncio
@respx.mock
async def test_route_steps_parsed() -> None:
    respx.get(re.compile(r"http://localhost:5000/route/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "routes": [{
                    "distance": 1000.0,
                    "duration": 120.0,
                    "legs": [{
                        "steps": [
                            {"name": "Oxford Street", "distance": 500.0, "duration": 60.0, "mode": "driving"},
                            {"name": "Regent Street", "distance": 500.0, "duration": 60.0, "mode": "driving"},
                        ]
                    }],
                }]
            },
        )
    )
    async with OSRMClient(_BASE) as client:
        route = await client.route(Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.12))
    assert len(route.steps) == 2
    assert route.steps[0].name == "Oxford Street"
    assert route.steps[1].name == "Regent Street"


@pytest.mark.asyncio
@respx.mock
async def test_table_happy() -> None:
    respx.get(re.compile(r"http://localhost:5000/table/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200,
            json={"durations": [[0.0, 120.0], [130.0, 0.0]], "distances": [[0.0, 1000.0], [1050.0, 0.0]]},
        )
    )
    origins = [Point(lat=51.5, lng=-0.1)]
    dests = [Point(lat=51.51, lng=-0.11)]
    async with OSRMClient(_BASE) as client:
        matrix = await client.table(origins, dests)
    assert isinstance(matrix, TravelTimeMatrix)
    assert matrix.durations[0][1] == 120.0


@pytest.mark.asyncio
@respx.mock
async def test_table_dimensions() -> None:
    respx.get(re.compile(r"http://localhost:5000/table/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200,
            json={"durations": [[0.0, 100.0, 200.0]], "distances": []},
        )
    )
    origins = [Point(lat=51.5, lng=-0.1)]
    dests = [Point(lat=51.51, lng=-0.11), Point(lat=51.52, lng=-0.12), Point(lat=51.53, lng=-0.13)]
    async with OSRMClient(_BASE) as client:
        matrix = await client.table(origins, dests)
    assert len(matrix.durations[0]) == 3


@pytest.mark.asyncio
@respx.mock
async def test_nearest_happy() -> None:
    respx.get(re.compile(r"http://localhost:5000/nearest/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200,
            json={"waypoints": [{"location": [-0.102, 51.503], "distance": 5.3, "name": "The Strand"}]},
        )
    )
    async with OSRMClient(_BASE) as client:
        results = await client.nearest(Point(lat=51.5, lng=-0.1))
    assert len(results) == 1
    assert isinstance(results[0], NearestResult)
    assert results[0].name == "The Strand"
    assert results[0].distance_m == 5.3


@pytest.mark.asyncio
@respx.mock
async def test_nearest_multiple() -> None:
    waypoints = [
        {"location": [-0.10, 51.50], "distance": 5.0, "name": "Street A"},
        {"location": [-0.11, 51.51], "distance": 15.0, "name": "Street B"},
        {"location": [-0.12, 51.52], "distance": 25.0, "name": "Street C"},
    ]
    respx.get(re.compile(r"http://localhost:5000/nearest/v1/driving/.*")).mock(
        return_value=httpx.Response(200, json={"waypoints": waypoints})
    )
    async with OSRMClient(_BASE) as client:
        results = await client.nearest(Point(lat=51.5, lng=-0.1), number=3)
    assert len(results) == 3


@pytest.mark.asyncio
@respx.mock
async def test_route_walking_profile() -> None:
    route_mock = respx.get(re.compile(r"http://localhost:5000/route/v1/walking/.*")).mock(
        return_value=httpx.Response(
            200, json={"routes": [{"distance": 800.0, "duration": 600.0, "legs": []}]}
        )
    )
    async with OSRMClient(_BASE) as client:
        await client.route(Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.11), profile="walking")
    assert route_mock.called


@pytest.mark.asyncio
@respx.mock
async def test_route_cycling_profile() -> None:
    respx.get(re.compile(r"http://localhost:5000/route/v1/cycling/.*")).mock(
        return_value=httpx.Response(
            200, json={"routes": [{"distance": 900.0, "duration": 300.0, "legs": []}]}
        )
    )
    async with OSRMClient(_BASE) as client:
        route = await client.route(
            Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.11), profile="cycling"
        )
    assert route.distance_m == 900.0


@pytest.mark.asyncio
@respx.mock
async def test_osrm_context_manager() -> None:
    respx.get(re.compile(r"http://localhost:5000/nearest/v1/driving/.*")).mock(
        return_value=httpx.Response(200, json={"waypoints": []})
    )
    async with OSRMClient(_BASE) as client:
        results = await client.nearest(Point(lat=51.5, lng=-0.1))
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_route_http_error() -> None:
    respx.get(re.compile(r"http://localhost:5000/route/v1/driving/.*")).mock(
        return_value=httpx.Response(400, json={"message": "Bad request"})
    )
    async with OSRMClient(_BASE) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.route(Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.11))


@pytest.mark.asyncio
@respx.mock
async def test_table_empty_matrix() -> None:
    respx.get(re.compile(r"http://localhost:5000/table/v1/driving/.*")).mock(
        return_value=httpx.Response(200, json={"durations": [], "distances": []})
    )
    async with OSRMClient(_BASE) as client:
        matrix = await client.table([], [])
    assert matrix.durations == []


@pytest.mark.asyncio
@respx.mock
async def test_nearest_empty_waypoints() -> None:
    respx.get(re.compile(r"http://localhost:5000/nearest/v1/driving/.*")).mock(
        return_value=httpx.Response(200, json={"waypoints": []})
    )
    async with OSRMClient(_BASE) as client:
        results = await client.nearest(Point(lat=51.5, lng=-0.1))
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_route_geometry_optional() -> None:
    respx.get(re.compile(r"http://localhost:5000/route/v1/driving/.*")).mock(
        return_value=httpx.Response(
            200, json={"routes": [{"distance": 500.0, "duration": 60.0, "legs": []}]}
        )
    )
    async with OSRMClient(_BASE) as client:
        route = await client.route(Point(lat=51.5, lng=-0.1), Point(lat=51.51, lng=-0.11))
    assert route.geometry is None
