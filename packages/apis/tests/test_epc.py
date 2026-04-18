"""Tests for ``EPCClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import AuthError
from uk_property_apis.epc import EPCClient


@pytest.mark.asyncio
@respx.mock
async def test_search_domestic_happy() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/domestic/search\?.*")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lmk-key": "abc",
                    "address": "1 High Street",
                    "postcode": "CB1 1AA",
                    "current-energy-rating": "C",
                    "current-energy-efficiency": "72",
                },
            ],
            headers={"X-Next-Search-After": "token123"},
        ),
    )
    auth = httpx.BasicAuth("user@example.com", "secret-token")
    async with EPCClient(auth=auth) as client:
        page = await client.search_domestic(postcode="CB1 1AA", size=10)
    assert page.rows[0].lmk_key == "abc"
    assert page.rows[0].current_energy_rating == "C"
    assert page.next_search_after == "token123"


@pytest.mark.asyncio
@respx.mock
async def test_search_domestic_dict_rows() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/domestic/search\?.*")).mock(
        return_value=httpx.Response(200, json={"rows": [{"lmk-key": "x", "address": "A"}]}),
    )
    async with EPCClient(auth=httpx.BasicAuth("a", "b")) as client:
        page = await client.search_domestic()
    assert page.rows[0].lmk_key == "x"


@pytest.mark.asyncio
@respx.mock
async def test_search_domestic_401() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/domestic/search\?.*")).mock(
        return_value=httpx.Response(401, json={}),
    )
    async with EPCClient(auth=httpx.BasicAuth("a", "b")) as client:
        with pytest.raises(AuthError):
            await client.search_domestic()


@pytest.mark.asyncio
@respx.mock
async def test_get_domestic_certificate_object() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/domestic/certificate/abc$")).mock(
        return_value=httpx.Response(
            200,
            json={
                "lmk-key": "abc",
                "total-floor-area": "120",
                "property-type": "House",
            },
        ),
    )
    async with EPCClient(auth=httpx.BasicAuth("a", "b")) as client:
        row = await client.get_domestic_certificate("abc")
    assert row.total_floor_area == "120"


@pytest.mark.asyncio
@respx.mock
async def test_get_domestic_certificate_list() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/domestic/certificate/abc$")).mock(
        return_value=httpx.Response(200, json=[{"lmk-key": "abc", "address": "A"}]),
    )
    async with EPCClient(auth=httpx.BasicAuth("a", "b")) as client:
        row = await client.get_domestic_certificate("abc")
    assert row.address == "A"


@pytest.mark.asyncio
@respx.mock
async def test_non_domestic_search() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/non-domestic/search\?.*")).mock(
        return_value=httpx.Response(200, json=[]),
    )
    async with EPCClient(auth=httpx.BasicAuth("a", "b")) as client:
        page = await client.search_non_domestic(postcode="M1")
    assert page.rows == []


@pytest.mark.asyncio
@respx.mock
async def test_display_search() -> None:
    respx.get(re.compile(r"https://epc\.opendatacommunities\.org/api/v1/display/search\?.*")).mock(
        return_value=httpx.Response(200, json=[]),
    )
    async with EPCClient(auth=httpx.BasicAuth("a", "b")) as client:
        page = await client.search_display()
    assert page.rows == []


def test_epc_requires_credentials() -> None:
    with pytest.raises(ValueError):
        EPCClient()
