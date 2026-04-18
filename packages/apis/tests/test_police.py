"""Tests for ``PoliceClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import AuthError
from uk_property_apis.police import PoliceClient


@pytest.mark.asyncio
@respx.mock
async def test_street_crimes_happy() -> None:
    respx.get(re.compile(r"https://data\.police\.uk/api/crimes-street/all-crime\?.*")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "category": "burglary",
                    "location_type": "Force",
                    "location": {
                        "latitude": "51.0",
                        "longitude": "-0.1",
                        "street": {"id": 1, "name": "On or near High Street"},
                    },
                    "month": "2024-01",
                },
            ],
        ),
    )
    async with PoliceClient() as client:
        crimes = await client.street_crimes(51.0, -0.1, month="2024-01")
    assert len(crimes) == 1
    assert crimes[0].category == "burglary"
    assert crimes[0].location is not None
    assert crimes[0].location.street is not None
    assert crimes[0].location.street.name == "On or near High Street"


@pytest.mark.asyncio
@respx.mock
async def test_street_crimes_401() -> None:
    respx.get(re.compile(r"https://data\.police\.uk/api/.*")).mock(
        return_value=httpx.Response(401, json={}),
    )
    async with PoliceClient() as client:
        with pytest.raises(AuthError):
            await client.street_crimes(51.0, -0.1, month="2024-01")


@pytest.mark.asyncio
@respx.mock
async def test_locate_neighbourhood() -> None:
    respx.get(re.compile(r"https://data\.police\.uk/api/locate-neighbourhood\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={"neighbourhood": "city", "force": "metropolitan"},
        ),
    )
    async with PoliceClient() as client:
        n = await client.locate_neighbourhood(51.5, -0.12)
    assert n.force == "metropolitan"


@pytest.mark.asyncio
@respx.mock
async def test_crimes_no_location() -> None:
    respx.get(re.compile(r"https://data\.police\.uk/api/crimes-no-location\?.*")).mock(
        return_value=httpx.Response(200, json=[]),
    )
    async with PoliceClient() as client:
        rows = await client.crimes_no_location(category="burglary", force="met", month="2024-01")
    assert rows == []


@pytest.mark.asyncio
@respx.mock
async def test_forces() -> None:
    respx.get("https://data.police.uk/api/forces").mock(
        return_value=httpx.Response(200, json=[{"id": "met", "name": "Metropolitan Police"}]),
    )
    async with PoliceClient() as client:
        forces = await client.forces()
    assert forces[0].id == "met"


@pytest.mark.asyncio
@respx.mock
async def test_crime_categories() -> None:
    respx.get(re.compile(r"https://data\.police\.uk/api/crime-categories\?.*")).mock(
        return_value=httpx.Response(200, json=[{"name": "Anti-social behaviour", "url": "-"}]),
    )
    async with PoliceClient() as client:
        cats = await client.crime_categories(month="2024-01")
    assert cats[0].name == "Anti-social behaviour"


@pytest.mark.asyncio
@respx.mock
async def test_crimes_at_location() -> None:
    respx.get(re.compile(r"https://data\.police\.uk/api/crimes-at-location\?.*")).mock(
        return_value=httpx.Response(200, json=[]),
    )
    async with PoliceClient() as client:
        rows = await client.crimes_at_location(month="2024-01", location_id=1)
    assert rows == []


@pytest.mark.asyncio
@respx.mock
async def test_crime_stats_near_compound() -> None:
    def _crime_json(cat: str) -> list[dict]:
        return [
            {
                "category": cat,
                "location_type": "Force",
                "location": {"latitude": "51", "longitude": "-0.1"},
                "month": "2024-01",
            },
        ]

    route = respx.get(re.compile(r"https://data\.police\.uk/api/crimes-street/all-crime\?.*"))

    route.mock(
        side_effect=[
            httpx.Response(200, json=_crime_json("theft")),
            httpx.Response(200, json=_crime_json("theft")),
        ],
    )

    async with PoliceClient() as client:
        stats = await client.crime_stats_near(51.0, -0.1, months_back=2)

    assert stats.by_category["theft"] == 2
    assert len(stats.by_month_category) == 2
