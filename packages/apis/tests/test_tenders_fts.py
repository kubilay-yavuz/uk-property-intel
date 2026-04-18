"""Tests for :class:`FTSClient`.

Exercises authentication wiring, OCDS cursor pagination, and the
in-memory post-filter that compensates for FTS's minimal native filters.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from uk_property_apis.tenders import FTSClient, TenderQuery, TenderStatus
from uk_property_apis.tenders.fts_client import _extract_cursor, _matches_query
from uk_property_apis.tenders.models import (
    Tender,
    TenderClassification,
    TenderLocation,
    TenderOrg,
    TenderSource,
    TenderValue,
)

_PACKAGES_URL = (
    "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
)


def _make_release(
    *,
    tender_id: str = "TN-2026-04-001",
    ocid: str = "ocds-abcdef-0001",
    title: str = "Housing retrofit framework",
    description: str = "Fabric-first retrofit across council stock.",
    status: str = "active",
    cpv_codes: list[str] | None = None,
    region: str = "London",
    amount: float | None = 12_500_000.0,
) -> dict[str, Any]:
    return {
        "ocid": ocid,
        "id": f"rel-{tender_id}",
        "date": "2026-04-10T12:00:00Z",
        "tag": ["tender"],
        "buyer": {"id": "GB-COH-02916292", "name": "Westminster City Council"},
        "parties": [
            {
                "id": "GB-COH-02916292",
                "name": "Westminster City Council",
                "identifier": {"scheme": "GB-COH", "id": "02916292"},
                "address": {"region": region, "countryCode": "GB"},
            }
        ],
        "tender": {
            "id": tender_id,
            "title": title,
            "description": description,
            "status": status,
            "classification": {
                "scheme": "CPV",
                "id": (cpv_codes or ["45210000"])[0],
            },
            "additionalClassifications": [
                {"scheme": "CPV", "id": c} for c in (cpv_codes or [])[1:]
            ],
            "value": {"amount": amount, "currency": "GBP"}
            if amount is not None
            else None,
            "tenderPeriod": {"endDate": "2026-05-30T17:00:00Z"},
            "items": [
                {
                    "id": "item-1",
                    "deliveryAddresses": [
                        {"region": region, "countryCode": "GB"}
                    ],
                }
            ],
        },
    }


def _package(releases: list[dict[str, Any]], next_cursor: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"releases": releases}
    if next_cursor is not None:
        body["links"] = {
            "next": (
                f"https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
                f"?cursor={next_cursor}&limit=100"
            )
        }
    else:
        body["links"] = {}
    return body


class TestCursorExtraction:
    def test_extract_from_full_url(self) -> None:
        link = "https://api.example.com/foo?cursor=abc123&limit=10"
        assert _extract_cursor(link) == "abc123"

    def test_extract_from_relative_url(self) -> None:
        assert _extract_cursor("/ocdsReleasePackages?cursor=xyz") == "xyz"

    def test_no_cursor_returns_none(self) -> None:
        assert _extract_cursor("https://api.example.com/foo") is None

    def test_empty_cursor_returns_none(self) -> None:
        assert _extract_cursor("https://api.example.com/foo?cursor=") is None

    def test_non_string_returns_none(self) -> None:
        assert _extract_cursor(None) is None
        assert _extract_cursor(12) is None
        assert _extract_cursor({}) is None


class TestConstructor:
    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FTS_API_KEY", raising=False)
        monkeypatch.delenv("CDP_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key required"):
            FTSClient()

    def test_reads_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_API_KEY", "env-key-123")
        client = FTSClient()
        assert client._default_headers is not None
        assert client._default_headers.get("CDP-Api-Key") == "env-key-123"

    def test_cdp_api_key_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FTS_API_KEY", raising=False)
        monkeypatch.setenv("CDP_API_KEY", "cdp-key-fallback")
        client = FTSClient()
        assert client._default_headers is not None
        assert client._default_headers.get("CDP-Api-Key") == "cdp-key-fallback"

    def test_explicit_key_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_API_KEY", "env-key")
        client = FTSClient(api_key="explicit-key")
        assert client._default_headers is not None
        assert client._default_headers.get("CDP-Api-Key") == "explicit-key"

    def test_custom_headers_merged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FTS_API_KEY", "env-key")
        client = FTSClient(headers={"User-Agent": "uk-property-agent/1.0"})
        assert client._default_headers is not None
        assert client._default_headers.get("CDP-Api-Key") == "env-key"
        assert client._default_headers.get("User-Agent") == "uk-property-agent/1.0"


@pytest.mark.asyncio
@respx.mock
async def test_get_release_packages_raw_sends_params() -> None:
    route = respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json=_package([], next_cursor=None)),
    )
    async with FTSClient(api_key="test-key") as client:
        await client.get_release_packages_raw(
            updated_from=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
            updated_to=datetime.fromisoformat("2026-04-01T00:00:00+00:00"),
            cursor="page-2",
            limit=50,
        )
    assert route.called
    req = route.calls.last.request
    assert req.headers["CDP-Api-Key"] == "test-key"
    qs = parse_qs(urlsplit(str(req.url)).query)
    assert qs["cursor"] == ["page-2"]
    assert qs["limit"] == ["50"]
    assert qs["updatedFrom"] == ["2026-01-01T00:00:00Z"]
    assert qs["updatedTo"] == ["2026-04-01T00:00:00Z"]


@pytest.mark.asyncio
@respx.mock
async def test_get_release_packages_raw_clamps_limit_to_100() -> None:
    route = respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json=_package([], next_cursor=None)),
    )
    async with FTSClient(api_key="k") as client:
        await client.get_release_packages_raw(limit=999)
    qs = parse_qs(urlsplit(str(route.calls.last.request.url)).query)
    assert qs["limit"] == ["100"]


@pytest.mark.asyncio
@respx.mock
async def test_iter_release_packages_follows_cursor_chain() -> None:
    page_1 = _package([_make_release(tender_id="a"), _make_release(tender_id="b")], "cur-2")
    page_2 = _package([_make_release(tender_id="c")], "cur-3")
    page_3 = _package([], None)

    respx.get(_PACKAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json=page_1),
            httpx.Response(200, json=page_2),
            httpx.Response(200, json=page_3),
        ]
    )

    async with FTSClient(api_key="k") as client:
        envelopes: list[dict[str, Any]] = []
        async for pkg in client.iter_release_packages(page_size=10):
            envelopes.append(pkg)

    assert len(envelopes) == 3
    tender_ids = [
        rel["tender"]["id"]
        for env in envelopes
        for rel in env.get("releases", [])
    ]
    assert tender_ids == ["a", "b", "c"]


@pytest.mark.asyncio
@respx.mock
async def test_iter_release_packages_stops_on_identical_cursor() -> None:
    """Defensive: same cursor twice means loop, bail out."""

    page = _package([_make_release(tender_id="x")], "same-cursor")
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json=page),
    )

    calls = 0
    async with FTSClient(api_key="k") as client:
        async for _ in client.iter_release_packages(page_size=10):
            calls += 1
    assert calls == 2


@pytest.mark.asyncio
@respx.mock
async def test_iter_release_packages_respects_max_pages() -> None:
    page = _package([_make_release(tender_id="x")], "next")
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json=page),
    )

    pages = 0
    async with FTSClient(api_key="k") as client:
        async for _ in client.iter_release_packages(page_size=10, max_pages=2):
            pages += 1
    assert pages == 2


@pytest.mark.asyncio
@respx.mock
async def test_iter_releases_flattens_packages() -> None:
    release_a = _make_release(tender_id="a")
    release_b = _make_release(tender_id="b")
    release_c = _make_release(tender_id="c")
    respx.get(_PACKAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json=_package([release_a, release_b], "cur-2")),
            httpx.Response(200, json=_package([release_c], None)),
        ]
    )

    ids: list[str] = []
    async with FTSClient(api_key="k") as client:
        async for release in client.iter_releases(page_size=10):
            ids.append(release["tender"]["id"])

    assert ids == ["a", "b", "c"]


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_applies_limit_budget() -> None:
    releases = [_make_release(tender_id=f"t-{i}") for i in range(5)]
    respx.get(_PACKAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json=_package(releases, None)),
        ]
    )

    async with FTSClient(api_key="k") as client:
        results = await client.search_tenders(TenderQuery(limit=3))
    assert len(results) == 3
    assert all(t.source is TenderSource.FIND_A_TENDER for t in results)


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_applies_client_side_keyword_filter() -> None:
    matching = _make_release(
        tender_id="m", title="Social housing construction programme"
    )
    skip = _make_release(
        tender_id="s",
        title="IT support services",
        description="Helpdesk managed services.",
    )
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json=_package([matching, skip], None)),
    )

    async with FTSClient(api_key="k") as client:
        results = await client.search_tenders(TenderQuery(keyword="housing"))

    assert len(results) == 1
    assert results[0].source_id == "m"


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_cpv_prefix_match() -> None:
    construction = _make_release(tender_id="c", cpv_codes=["45211000"])
    real_estate = _make_release(tender_id="r", cpv_codes=["70310000"])
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(
            200, json=_package([construction, real_estate], None)
        ),
    )

    async with FTSClient(api_key="k") as client:
        results = await client.search_tenders(TenderQuery(cpv_codes=["45"]))

    assert len(results) == 1
    assert results[0].source_id == "c"


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_value_range_filter() -> None:
    cheap = _make_release(tender_id="cheap", amount=50_000)
    expensive = _make_release(tender_id="lux", amount=50_000_000)
    just_right = _make_release(tender_id="mid", amount=2_500_000)
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(
            200, json=_package([cheap, expensive, just_right], None)
        ),
    )

    async with FTSClient(api_key="k") as client:
        results = await client.search_tenders(
            TenderQuery(value_low=1_000_000, value_high=10_000_000)
        )

    assert len(results) == 1
    assert results[0].source_id == "mid"


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_region_filter_buyer_or_location() -> None:
    london = _make_release(tender_id="london", region="London")
    manchester = _make_release(tender_id="manchester", region="North West")
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(
            200, json=_package([london, manchester], None)
        ),
    )

    async with FTSClient(api_key="k") as client:
        results = await client.search_tenders(TenderQuery(regions=["London"]))

    assert len(results) == 1
    assert results[0].source_id == "london"


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_status_and_notice_type_filter() -> None:
    active = _make_release(tender_id="active", status="active")
    cancelled = copy.deepcopy(active)
    cancelled["tender"]["id"] = "cancelled"
    cancelled["tender"]["status"] = "cancelled"
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(
            200, json=_package([active, cancelled], None)
        ),
    )

    async with FTSClient(api_key="k") as client:
        results_open = await client.search_tenders(
            TenderQuery(statuses=["open"])
        )
        results_tender = await client.search_tenders(
            TenderQuery(notice_types=["tender"])
        )

    assert [r.source_id for r in results_open] == ["active"]
    assert {r.source_id for r in results_tender} == {"active", "cancelled"}


@pytest.mark.asyncio
@respx.mock
async def test_search_tenders_published_date_bounds() -> None:
    old = _make_release(tender_id="old")
    old["date"] = "2025-01-01T00:00:00Z"
    new = _make_release(tender_id="new")
    new["date"] = "2026-05-05T12:00:00Z"
    respx.get(_PACKAGES_URL).mock(
        return_value=httpx.Response(200, json=_package([old, new], None)),
    )

    async with FTSClient(api_key="k") as client:
        results = await client.search_tenders(
            TenderQuery(
                published_from=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                published_to=datetime.fromisoformat("2026-12-31T23:59:59+00:00"),
            )
        )

    assert [r.source_id for r in results] == ["new"]


def _fake_tender(
    *,
    title: str = "x",
    description: str = "",
    status: TenderStatus = TenderStatus.OPEN,
    notice_type: str | None = "tender",
    cpv: list[str] | None = None,
    region: str | None = None,
    value: float | None = None,
    published: datetime | None = None,
) -> Tender:
    classifications = [
        TenderClassification(scheme="CPV", code=c) for c in cpv or []
    ]
    return Tender(
        source=TenderSource.FIND_A_TENDER,
        source_id=title,
        title=title,
        description=description or None,
        status=status,
        notice_type=notice_type,
        value=TenderValue(amount=value, currency="GBP") if value is not None else None,
        classifications=classifications,
        location=TenderLocation(region=region) if region else None,
        buyer=TenderOrg(name="X") if not region else None,
        published_date=published,
    )


class TestMatchesQuery:
    def test_keyword_matches_description(self) -> None:
        tender = _fake_tender(title="Unrelated", description="Construction of houses.")
        assert _matches_query(tender, TenderQuery(keyword="houses")) is True

    def test_keyword_case_insensitive(self) -> None:
        tender = _fake_tender(title="HOUSING WORK")
        assert _matches_query(tender, TenderQuery(keyword="housing")) is True

    def test_keyword_miss(self) -> None:
        tender = _fake_tender(title="IT services")
        assert _matches_query(tender, TenderQuery(keyword="housing")) is False

    def test_cpv_exact_match(self) -> None:
        tender = _fake_tender(title="x", cpv=["45211000"])
        assert _matches_query(tender, TenderQuery(cpv_codes=["45211000"])) is True

    def test_cpv_prefix_match(self) -> None:
        tender = _fake_tender(title="x", cpv=["45211000"])
        assert _matches_query(tender, TenderQuery(cpv_codes=["45"])) is True

    def test_cpv_miss(self) -> None:
        tender = _fake_tender(title="x", cpv=["70310000"])
        assert _matches_query(tender, TenderQuery(cpv_codes=["45"])) is False

    def test_value_low_excludes_unknown_value(self) -> None:
        tender = _fake_tender(title="x", value=None)
        assert _matches_query(tender, TenderQuery(value_low=10_000)) is False

    def test_value_high_accepts_under(self) -> None:
        tender = _fake_tender(title="x", value=5_000)
        assert _matches_query(tender, TenderQuery(value_high=10_000)) is True

    def test_empty_query_accepts_anything(self) -> None:
        assert _matches_query(_fake_tender(title="x"), TenderQuery()) is True
