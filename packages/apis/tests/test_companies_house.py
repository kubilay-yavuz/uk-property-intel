"""Tests for ``CompaniesHouseClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import AuthError
from uk_property_apis.companies_house import CompaniesHouseClient


@pytest.mark.asyncio
@respx.mock
async def test_search_companies_happy() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/search/companies\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [{"company_number": "00000001", "title": "Test Ltd"}],
                "total_count": 1,
                "items_per_page": 20,
                "start_index": 0,
            },
        ),
    )
    async with CompaniesHouseClient(api_key="dummy") as client:
        res = await client.search_companies("Test", items_per_page=5)
    assert res.items[0].company_number == "00000001"


@pytest.mark.asyncio
@respx.mock
async def test_search_companies_401() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/search/companies\?.*")).mock(
        return_value=httpx.Response(401, json={}),
    )
    async with CompaniesHouseClient(api_key="bad") as client:
        with pytest.raises(AuthError):
            await client.search_companies("x")


@pytest.mark.asyncio
@respx.mock
async def test_get_company() -> None:
    respx.get("https://api.company-information.service.gov.uk/company/00000001").mock(
        return_value=httpx.Response(
            200,
            json={
                "company_name": "TEST LTD",
                "company_number": "00000001",
                "company_status": "active",
                "sic_codes": ["62012"],
            },
        ),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        c = await client.get_company("00000001")
    assert c.company_name == "TEST LTD"
    assert c.sic_codes == ["62012"]


@pytest.mark.asyncio
@respx.mock
async def test_get_officers() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/company/1/officers\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"name": "SMITH, John", "officer_role": "director"}], "total_results": 1},
        ),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        o = await client.get_officers("1")
    assert o.items[0].name == "SMITH, John"


@pytest.mark.asyncio
@respx.mock
async def test_get_psc() -> None:
    respx.get(
        re.compile(
            r"https://api\.company-information\.service\.gov\.uk/company/1/persons-with-significant-control\?.*",
        ),
    ).mock(return_value=httpx.Response(200, json={"items": [{"kind": "individual", "name": "A"}], "total_results": 1}))
    async with CompaniesHouseClient(api_key="k") as client:
        p = await client.get_psc("1")
    assert p.items[0].kind == "individual"


@pytest.mark.asyncio
@respx.mock
async def test_get_filing_history() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/company/1/filing-history\?.*")).mock(
        return_value=httpx.Response(200, json={"items": [{"transaction_id": "x", "type": "AA"}], "total_count": 1}),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        f = await client.get_filing_history("1")
    assert f.items[0].type == "AA"


@pytest.mark.asyncio
@respx.mock
async def test_get_charges() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/company/1/charges\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"charge_number": 1, "status": "outstanding"}], "total_count": 1},
        ),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        ch = await client.get_charges("1")
    assert ch.items[0].charge_number == 1


def test_companies_house_requires_key() -> None:
    with pytest.raises(ValueError):
        CompaniesHouseClient()


@pytest.mark.asyncio
@respx.mock
async def test_search_officers() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/search/officers\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "SMITH, John",
                        "description": "Born April 1980",
                        "links": {"self": "/officers/off1/appointments"},
                    }
                ],
                "total_results": 1,
                "items_per_page": 20,
                "start_index": 0,
            },
        ),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        res = await client.search_officers("John Smith")
    assert res.items[0].officer_id == "off1"
    assert res.items[0].title == "SMITH, John"


@pytest.mark.asyncio
@respx.mock
async def test_get_officer_appointments() -> None:
    respx.get(re.compile(r"https://api\.company-information\.service\.gov\.uk/officers/off1/appointments\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "appointed_to": {
                            "company_number": "12345678",
                            "company_name": "TEST LTD",
                            "company_status": "active",
                        },
                        "officer_role": "director",
                        "appointed_on": "2018-01-01",
                        "links": {"company": "/company/12345678"},
                    }
                ],
                "total_results": 1,
                "items_per_page": 35,
                "start_index": 0,
                "name": "SMITH, John",
            },
        ),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        res = await client.get_officer_appointments("off1")
    assert res.name == "SMITH, John"
    assert res.items[0].company_number == "12345678"
