"""Tests for :class:`ContractsFinderClient`.

All HTTP is respx-mocked — we assert the outgoing request body shape and
confirm the normaliser is wired through end-to-end.
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

import httpx
import pytest
import respx
from uk_property_apis.tenders import ContractsFinderClient, TenderQuery, TenderStatus

_SEARCH_URL = "https://www.contractsfinder.service.gov.uk/api/rest/2/search_notices/json"
_NOTICE_URL_PREFIX = (
    "https://www.contractsfinder.service.gov.uk/api/rest/2/get_published_notice/json/"
)


def _search_response(notices: list[dict[str, object]]) -> dict[str, object]:
    return {
        "HitCount": len(notices),
        "NoticeList": notices,
        "MaxHits": 0,
        "ByRegion": [],
        "ByType": [],
    }


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_happy() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                [
                    {
                        "Id": "cf-1",
                        "Title": "Parks maintenance",
                        "NoticeStatus": "Open",
                        "PublishedDate": "2026-03-01T09:00:00Z",
                        "CpvCodes": ["77310000"],
                    },
                ]
            ),
        ),
    )
    async with ContractsFinderClient() as client:
        results = await client.search_tenders(
            TenderQuery(keyword="parks", cpv_codes=["77310000"], limit=50)
        )
    assert route.called
    sent = json.loads(route.calls.last.request.content)
    assert sent["size"] == 50
    assert sent["searchCriteria"]["keyword"] == "parks"
    assert sent["searchCriteria"]["cpvCodes"] == ["77310000"]
    assert len(results) == 1
    assert results[0].source_id == "cf-1"
    assert results[0].status is TenderStatus.OPEN
    assert results[0].cpv_prefix_matches("77") is True


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_size_override() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response([])),
    )
    async with ContractsFinderClient() as client:
        await client.search_tenders(TenderQuery(limit=100), size=250)
    sent = json.loads(route.calls.last.request.content)
    assert sent["size"] == 250


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_handles_missing_notice_list() -> None:
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"HitCount": 0}),
    )
    async with ContractsFinderClient() as client:
        results = await client.search_tenders()
    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_search_criteria_omits_unset_fields() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response([])),
    )
    async with ContractsFinderClient() as client:
        await client.search_tenders()
    sent = json.loads(route.calls.last.request.content)
    assert sent["searchCriteria"] == {}
    assert sent["size"] == 100


@pytest.mark.asyncio
@respx.mock
async def test_search_criteria_renders_all_fields() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response([])),
    )
    async with ContractsFinderClient() as client:
        await client.search_tenders(
            TenderQuery(
                keyword="housing",
                cpv_codes=["45211000", "70000000"],
                regions=["London"],
                postcode="SW1A 2AA",
                radius_km=15,
                notice_types=["Contract"],
                statuses=["Open"],
                value_low=100_000,
                value_high=5_000_000,
                published_from=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                published_to=datetime.fromisoformat("2026-12-31T23:59:59+00:00"),
                limit=200,
            )
        )
    sent = json.loads(route.calls.last.request.content)
    criteria = sent["searchCriteria"]
    assert criteria["keyword"] == "housing"
    assert criteria["cpvCodes"] == ["45211000", "70000000"]
    assert criteria["regions"] == ["London"]
    assert criteria["postcode"] == "SW1A 2AA"
    assert criteria["radius"] == 15
    assert criteria["types"] == ["Contract"]
    assert criteria["statuses"] == ["Open"]
    assert criteria["valueLow"] == 100_000
    assert criteria["valueHigh"] == 5_000_000
    assert criteria["publishedFrom"] == "2026-01-01T00:00:00+00:00"
    assert criteria["publishedTo"] == "2026-12-31T23:59:59+00:00"
    assert sent["size"] == 200


@pytest.mark.asyncio
@respx.mock
async def test_get_notice_normalises_full_notice() -> None:
    respx.get(f"{_NOTICE_URL_PREFIX}cf-xyz").mock(
        return_value=httpx.Response(
            200,
            json={
                "Id": "cf-xyz",
                "Notice": {
                    "Title": "Regeneration masterplan consultancy",
                    "NoticeStatus": "Awarded",
                    "NoticeType": "Award",
                    "AwardedValue": 425000,
                    "CpvCodes": ["71410000"],
                },
            },
        ),
    )
    async with ContractsFinderClient() as client:
        tender = await client.get_notice("cf-xyz")
    assert tender.source_id == "cf-xyz"
    assert tender.title == "Regeneration masterplan consultancy"
    assert tender.status is TenderStatus.AWARDED
    assert tender.notice_type == "Award"
    assert tender.value is not None
    assert tender.value.amount == 425_000.0


@pytest.mark.asyncio
@respx.mock
async def test_search_notices_raw_returns_envelope() -> None:
    respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "HitCount": 42,
                "NoticeList": [{"Id": "a"}, {"Id": "b"}],
                "ByRegion": [{"region": "London", "count": 30}],
            },
        ),
    )
    async with ContractsFinderClient() as client:
        payload = await client.search_notices_raw()
    assert payload["HitCount"] == 42
    assert payload["ByRegion"][0]["region"] == "London"


@pytest.mark.asyncio
@respx.mock
async def test_search_request_uses_correct_base_and_path() -> None:
    route = respx.post(_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_search_response([])),
    )
    async with ContractsFinderClient() as client:
        await client.search_tenders()
    assert route.called
    parsed = urlparse(str(route.calls.last.request.url))
    assert parsed.netloc == "www.contractsfinder.service.gov.uk"
    assert parsed.path == "/api/rest/2/search_notices/json"
    assert route.calls.last.request.method == "POST"
