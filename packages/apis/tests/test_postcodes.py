"""Tests for ``PostcodesClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.postcodes import PostcodesClient

_EC1A_PAYLOAD = {
    "status": 200,
    "result": {
        "postcode": "EC1A 1BB",
        "quality": 1,
        "eastings": 531073,
        "northings": 182317,
        "country": "England",
        "longitude": -0.112017,
        "latitude": 51.524567,
        "region": "London",
        "lsoa": "Islington 022F",
        "msoa": "Islington 022",
        "incode": "1BB",
        "outcode": "EC1A",
        "parliamentary_constituency": "Islington South and Finsbury",
        "admin_district": "Islington",
        "parish": "Islington, unparished area",
        "admin_county": None,
        "admin_ward": "Clerkenwell",
        "ccg": "NHS North Central London",
        "nuts": "Islington",
        "codes": {"admin_district": "E09000019"},
    },
}


@pytest.mark.asyncio
@respx.mock
async def test_lookup_postcode_happy() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/postcodes/EC1A1BB$")).mock(
        return_value=httpx.Response(200, json=_EC1A_PAYLOAD),
    )
    async with PostcodesClient() as client:
        r = await client.lookup_postcode("EC1A 1BB")
    assert r.postcode == "EC1A 1BB"
    assert r.latitude == pytest.approx(51.524567)
    assert r.admin_district == "Islington"


@pytest.mark.asyncio
@respx.mock
async def test_lookup_postcode_404() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/postcodes/.*")).mock(
        return_value=httpx.Response(404, json={"status": 404, "error": "Not found"}),
    )
    async with PostcodesClient() as client:
        with pytest.raises(NotFoundError):
            await client.lookup_postcode("ZZ99 9ZZ")


@pytest.mark.asyncio
@respx.mock
async def test_validate_postcode() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/postcodes/.*/validate$")).mock(
        return_value=httpx.Response(200, json={"status": 200, "result": True}),
    )
    async with PostcodesClient() as client:
        assert await client.validate_postcode("EC1A1BB") is True


@pytest.mark.asyncio
@respx.mock
async def test_bulk_lookup() -> None:
    respx.post(re.compile(r"https://api\.postcodes\.io/postcodes$")).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "result": [
                    {"query": "EC1A1BB", "result": _EC1A_PAYLOAD["result"]},
                ],
            },
        ),
    )
    async with PostcodesClient() as client:
        page = await client.bulk_lookup(["EC1A1BB"])
    assert page.result[0].query == "EC1A1BB"
    assert page.result[0].result is not None
    assert page.result[0].result.postcode == "EC1A 1BB"


@pytest.mark.asyncio
@respx.mock
async def test_reverse_geocode() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/postcodes\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "result": [
                    {
                        "postcode": "EC1A 1BB",
                        "latitude": 51.52,
                        "longitude": -0.11,
                        "distance": 12.3,
                    },
                ],
            },
        ),
    )
    async with PostcodesClient() as client:
        r = await client.reverse_geocode(51.52, -0.11, radius_m=200)
    assert r.result and r.result[0].distance == pytest.approx(12.3)


@pytest.mark.asyncio
@respx.mock
async def test_lookup_outcode() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/outcodes/EC1A$")).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "result": {
                    "outcode": "EC1A",
                    "longitude": -0.1,
                    "latitude": 51.5,
                    "northings": 1,
                    "eastings": 2,
                    "admin_district": ["Islington"],
                    "parish": [],
                    "admin_county": [],
                    "admin_ward": [],
                    "country": ["England"],
                },
            },
        ),
    )
    async with PostcodesClient() as client:
        o = await client.lookup_outcode("EC1A")
    assert o.outcode == "EC1A"


@pytest.mark.asyncio
async def test_bulk_lookup_rejects_over_100() -> None:
    async with PostcodesClient() as client:
        with pytest.raises(ValueError):
            await client.bulk_lookup(["X"] * 101)


@pytest.mark.asyncio
@respx.mock
async def test_search_places_returns_matches() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/places(\?.*)?$")).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "result": [
                    {
                        "code": "osgb4000000074534990",
                        "name_1": "Cambridge",
                        "local_type": "City",
                        "county_unitary": "Cambridgeshire",
                        "region": "East of England",
                        "country": "England",
                        "longitude": 0.115989,
                        "latitude": 52.205337,
                        "eastings": 545000,
                        "northings": 258500,
                    },
                    {
                        "code": "osgb4000000074542345",
                        "name_1": "Cambridge",
                        "local_type": "Village",
                        "county_unitary": "Gloucestershire",
                        "region": "South West",
                        "country": "England",
                        "longitude": -2.346,
                        "latitude": 51.740,
                    },
                ],
            },
        ),
    )
    async with PostcodesClient() as client:
        results = await client.search_places("Cambridge", limit=3)
    assert len(results) == 2
    assert results[0].name_1 == "Cambridge"
    assert results[0].local_type == "City"
    assert results[0].county_unitary == "Cambridgeshire"
    assert results[0].latitude == pytest.approx(52.205337)
    assert results[1].county_unitary == "Gloucestershire"


@pytest.mark.asyncio
@respx.mock
async def test_search_places_empty_result_returns_list() -> None:
    respx.get(re.compile(r"https://api\.postcodes\.io/places(\?.*)?$")).mock(
        return_value=httpx.Response(200, json={"status": 200, "result": []}),
    )
    async with PostcodesClient() as client:
        results = await client.search_places("Atlantis")
    assert results == []


@pytest.mark.asyncio
async def test_search_places_rejects_empty_query() -> None:
    async with PostcodesClient() as client:
        with pytest.raises(ValueError, match="non-empty"):
            await client.search_places("   ")
