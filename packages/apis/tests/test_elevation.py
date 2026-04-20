"""Tests for :class:`ElevationClient` against the Open-Meteo elevation API.

The upstream service returns a JSON body of the shape
``{"elevation": [12.0, 50.5, ...]}`` — singular values are returned as
a one-element list, and out-of-coverage points are returned as ``null``.
These tests exercise single + batched calls plus the defensive parser.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis.elevation import (
    ElevationClient,
    ElevationPoint,
    elevation_at,
    elevations_at,
)

_ELEVATION_URL = re.compile(
    r"https://api\.open-meteo\.com/v1/elevation.*"
)


@pytest.mark.asyncio
@respx.mock
async def test_elevation_at_single_point_happy_path() -> None:
    route = respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": [12.0]})
    )

    async with ElevationClient() as client:
        point = await client.elevation_at(51.501009, -0.141588)

    assert route.called
    assert isinstance(point, ElevationPoint)
    assert point.lat == pytest.approx(51.501009)
    assert point.lng == pytest.approx(-0.141588)
    assert point.elevation_m == pytest.approx(12.0)
    assert point.source == "open-meteo"


@pytest.mark.asyncio
@respx.mock
async def test_elevation_at_null_value_surfaces_as_none() -> None:
    """Open-Meteo returns ``null`` for points outside DEM coverage."""
    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": [None]})
    )

    async with ElevationClient() as client:
        point = await client.elevation_at(90.0, 0.0)

    assert point.elevation_m is None
    assert point.source == "open-meteo"


@pytest.mark.asyncio
@respx.mock
async def test_elevation_at_scalar_elevation_is_accepted() -> None:
    """Some providers / proxies return a bare scalar instead of a list."""
    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": 12.0})
    )

    async with ElevationClient() as client:
        point = await client.elevation_at(51.5, -0.1)

    assert point.elevation_m == pytest.approx(12.0)


@pytest.mark.asyncio
@respx.mock
async def test_elevation_at_missing_field_is_none() -> None:
    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"generationtime_ms": 0.4})
    )

    async with ElevationClient() as client:
        point = await client.elevation_at(51.5, -0.1)

    assert point.elevation_m is None


@pytest.mark.asyncio
@respx.mock
async def test_elevations_at_batch_preserves_order() -> None:
    """Order must match input order — Open-Meteo returns them in request order."""
    route = respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(
            200, json={"elevation": [12.0, 210.5, None]}
        )
    )

    async with ElevationClient() as client:
        points = await client.elevations_at(
            [(51.501009, -0.141588), (54.999, -1.999), (90.0, 0.0)]
        )

    assert route.called
    assert len(points) == 3
    assert points[0].elevation_m == pytest.approx(12.0)
    assert points[1].elevation_m == pytest.approx(210.5)
    assert points[2].elevation_m is None
    assert all(p.source == "open-meteo" for p in points)


@pytest.mark.asyncio
@respx.mock
async def test_elevations_at_empty_input_no_request() -> None:
    route = respx.get(_ELEVATION_URL)

    async with ElevationClient() as client:
        points = await client.elevations_at([])

    assert points == []
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_elevations_at_short_response_pads_with_none() -> None:
    """If the upstream drops trailing elevations, pad to input length."""
    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": [12.0]})
    )

    async with ElevationClient() as client:
        points = await client.elevations_at(
            [(51.5, -0.1), (52.0, -1.0)]
        )

    assert len(points) == 2
    assert points[0].elevation_m == pytest.approx(12.0)
    assert points[1].elevation_m is None


@pytest.mark.asyncio
@respx.mock
async def test_elevations_at_custom_source_tag() -> None:
    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": [10.0]})
    )
    async with ElevationClient(source_tag="open-meteo:test") as client:
        point = await client.elevation_at(51.5, -0.1)
    assert point.source == "open-meteo:test"


@pytest.mark.asyncio
@respx.mock
async def test_module_level_convenience_helpers() -> None:
    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": [7.0]})
    )
    point = await elevation_at(51.5, -0.1)
    assert point.elevation_m == pytest.approx(7.0)

    respx.get(_ELEVATION_URL).mock(
        return_value=httpx.Response(200, json={"elevation": [1.0, 2.0]})
    )
    points = await elevations_at([(51.5, -0.1), (52.0, -1.0)])
    assert [p.elevation_m for p in points] == [1.0, 2.0]
