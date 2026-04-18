"""Tests for ``LandRegistryClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.land_registry import LandRegistryClient
from uk_property_apis.land_registry._parse import (
    extract_transaction_id,
    parse_transaction_date,
    pref_label,
)


def test_pref_label_string() -> None:
    assert pref_label("Freehold") == "Freehold"


def test_pref_label_nested() -> None:
    node = {"prefLabel": [{"_value": "Leasehold", "_datatype": "langString", "_lang": "en"}]}
    assert pref_label(node) == "Leasehold"


def test_parse_transaction_date() -> None:
    assert parse_transaction_date("Fri, 17 May 1996") == "1996-05-17"


def test_extract_transaction_id() -> None:
    assert extract_transaction_id({"transactionId": "abc-def"}) == "abc-def"
    assert (
        extract_transaction_id(
            {"_about": "http://landregistry.data.gov.uk/data/ppi/transaction/ABC-123"},
        )
        == "ABC-123"
    )


@pytest.mark.asyncio
@respx.mock
async def test_fetch_transactions_page() -> None:
    respx.get(re.compile(r"https://landregistry\.data\.gov\.uk/data/ppi/transaction\.json\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "items": [{"transactionId": "T1", "_about": "http://x/transaction/T1"}],
                    "page": 0,
                    "itemsPerPage": 100,
                },
            },
        ),
    )
    async with LandRegistryClient() as client:
        page = await client.fetch_transactions_page(postcode="B1 1AA", page_size=100)
    assert page.items
    assert extract_transaction_id(page.items[0]) == "T1"


@pytest.mark.asyncio
@respx.mock
async def test_get_transaction_current() -> None:
    respx.get(re.compile(r"https://landregistry\.data\.gov\.uk/data/ppi/transaction/T1/current\.json")).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "primaryTopic": {
                        "transactionId": "T1",
                        "pricePaid": 250000,
                        "transactionDate": "Mon, 01 Jan 2018",
                        "newBuild": False,
                        "estateType": {"prefLabel": [{"_value": "Freehold", "_datatype": "langString", "_lang": "en"}]},
                        "propertyType": {"prefLabel": [{"_value": "Detached", "_datatype": "langString", "_lang": "en"}]},
                        "propertyAddress": {
                            "paon": "10",
                            "saon": None,
                            "street": "High Road",
                            "locality": None,
                            "town": "Town",
                            "district": "District",
                            "county": "County",
                            "postcode": "AB1 2CD",
                        },
                    },
                },
            },
        ),
    )
    async with LandRegistryClient() as client:
        rec = await client.get_transaction_current("T1")
    assert rec.transaction_id == "T1"
    assert rec.price == 250000
    assert rec.postcode == "AB1 2CD"
    assert rec.tenure == "Freehold"


@pytest.mark.asyncio
@respx.mock
async def test_search_by_postcode_expands() -> None:
    """``expand=True`` hits ``transaction-record.json`` which returns expanded rows
    directly - no N+1 follow-up calls needed."""

    respx.get(
        re.compile(r"https://landregistry\.data\.gov\.uk/data/ppi/transaction-record\.json\?.*")
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "result": {
                    "items": [
                        {
                            "transactionId": "T1",
                            "pricePaid": 1,
                            "transactionDate": "Mon, 01 Jan 2018",
                            "newBuild": False,
                            "estateType": {
                                "prefLabel": [
                                    {
                                        "_value": "Freehold",
                                        "_datatype": "langString",
                                        "_lang": "en",
                                    }
                                ]
                            },
                            "propertyType": {
                                "prefLabel": [
                                    {
                                        "_value": "Terraced",
                                        "_datatype": "langString",
                                        "_lang": "en",
                                    }
                                ]
                            },
                            "propertyAddress": {
                                "paon": "1",
                                "street": "St",
                                "town": "T",
                                "district": "D",
                                "county": "C",
                                "postcode": "AB1 2CD",
                            },
                        }
                    ],
                    "page": 0,
                    "itemsPerPage": 10,
                },
            },
        ),
    )
    async with LandRegistryClient() as client:
        rows = await client.search_by_postcode("AB1 2CD", page_size=10, expand=True)
    assert len(rows) == 1
    assert rows[0].price == 1
    assert rows[0].postcode == "AB1 2CD"
    assert rows[0].tenure == "Freehold"
    assert rows[0].property_type == "Terraced"


@pytest.mark.asyncio
@respx.mock
async def test_search_by_postcode_no_expand() -> None:
    respx.get(re.compile(r"https://landregistry\.data\.gov\.uk/data/ppi/transaction\.json\?.*")).mock(
        return_value=httpx.Response(200, json={"result": {"items": [{"transactionId": "T9"}]}}),
    )
    async with LandRegistryClient() as client:
        rows = await client.search_by_postcode("X", expand=False)
    assert rows[0].transaction_id == "T9"
    assert rows[0].price == 0


@pytest.mark.asyncio
@respx.mock
async def test_transaction_current_404() -> None:
    respx.get(re.compile(r"https://landregistry\.data\.gov\.uk/data/ppi/transaction/missing/current\.json")).mock(
        return_value=httpx.Response(404, json={}),
    )
    async with LandRegistryClient() as client:
        with pytest.raises(NotFoundError):
            await client.get_transaction_current("missing")
