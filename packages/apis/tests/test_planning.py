"""Tests for ``PlanningClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import AuthError
from uk_property_apis.planning import PlanningClient, wkt_point


@pytest.mark.asyncio
@respx.mock
async def test_fetch_entities_happy() -> None:
    respx.get(re.compile(r"https://www\.planning\.data\.gov\.uk/entity\.json\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={"entities": [{"entity": 1, "name": "Listed A"}], "count": 1},
        ),
    )
    async with PlanningClient() as client:
        page = await client.fetch_entities("listed-building", geometry="POINT (-0.1 51.5)", geometry_relation="intersects")
    assert page.entities[0].name == "Listed A"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_entities_401() -> None:
    respx.get(re.compile(r"https://www\.planning\.data\.gov\.uk/entity\.json\?.*")).mock(
        return_value=httpx.Response(401, json={}),
    )
    async with PlanningClient() as client:
        with pytest.raises(AuthError):
            await client.fetch_entities("green-belt")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_listed_buildings_near_wkt_polygon() -> None:
    captured: dict[str, str] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs, urlparse

        url = str(request.url)
        captured["url"] = url
        q = urlparse(url).query
        parsed = parse_qs(q)
        captured["geometry"] = parsed["geometry"][0]
        return httpx.Response(200, json={"entities": [], "count": 0})

    respx.get(re.compile(r"https://www\.planning\.data\.gov\.uk/entity\.json\?.*")).mock(side_effect=_record)
    async with PlanningClient() as client:
        await client.fetch_listed_buildings_near(51.5, -0.12, radius_m=500)
    assert "POLYGON" in captured["geometry"]
    assert "geometry_relation=intersects" in captured["url"]


def test_wkt_point_helper() -> None:
    assert wkt_point(51.5, -0.12) == "POINT (-0.12 51.5)"


@pytest.mark.asyncio
@respx.mock
async def test_conservation_area_wrapper() -> None:
    respx.get(re.compile(r"https://www\.planning\.data\.gov\.uk/entity\.json\?.*")).mock(
        return_value=httpx.Response(200, json={"entities": [], "count": 0}),
    )
    async with PlanningClient() as client:
        page = await client.fetch_conservation_areas(limit=5)
    assert page.count == 0
