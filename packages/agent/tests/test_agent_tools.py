"""Unit tests for the agent tool wrappers.

These exercise the :class:`ToolContext` dependency-injection path and confirm
each tool function produces a JSON-serialisable dict. Network calls are
mocked with respx.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from uk_property_agent.tools import ToolContext, build_tools
from uk_property_apis import CompaniesHouseClient
from uk_property_listings import SimpleCrawler

FIXTURES = Path(__file__).parents[3] / "packages/scrapers/tests/fixtures"


def _read(relpath: str) -> str:
    return (FIXTURES / relpath).read_text()


@asynccontextmanager
async def _fast_crawler_factory():
    async with SimpleCrawler(request_timeout_s=5.0) as crawler:
        yield crawler


def _test_ctx() -> ToolContext:
    return ToolContext(crawler_factory=_fast_crawler_factory)


def _tool_by_name(name: str, ctx: ToolContext | None = None):
    tools = build_tools(ctx or _test_ctx())
    for t in tools:
        if t.name == name:
            return t
    raise KeyError(name)


class TestBuildTools:
    def test_default_tool_set(self) -> None:
        tools = build_tools(_test_ctx())
        names = {t.name for t in tools}
        assert names >= {
            "search_zoopla",
            "search_rightmove",
            "search_onthemarket",
            "get_listing_by_url",
            "lookup_postcode",
            "find_postcodes_for_place",
            "sold_prices_for_postcode",
            "crime_stats_near",
            "listed_buildings_near",
            "flood_warnings_near",
        }

    def test_epc_only_when_factory_provided(self) -> None:
        ctx = _test_ctx()
        assert "epc_certificates_for_postcode" not in {t.name for t in build_tools(ctx)}

    def test_companies_house_only_when_factory_provided(self) -> None:
        ctx = _test_ctx()
        assert "company_profile" not in {t.name for t in build_tools(ctx)}


class TestSearchTools:
    @respx.mock
    async def test_zoopla_search(self) -> None:
        html = _read("zoopla/search_cambridgeshire_2026-04.html")
        respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        tool = _tool_by_name("search_zoopla")
        out = await tool.ainvoke({"location": "Cambridge", "transaction": "sale", "max_pages": 1})
        assert out["source"] == "zoopla"
        assert out["pages_fetched"] == 1
        assert len(out["listings"]) >= 10
        assert all(row["source"] == "zoopla" for row in out["listings"])

    @respx.mock
    async def test_rightmove_search(self) -> None:
        html = _read("rightmove/search_cambridge_2026-04.html")
        respx.get(url__regex=r"https://www\.rightmove\.co\.uk/property-for-sale/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        tool = _tool_by_name("search_rightmove")
        out = await tool.ainvoke({"location": "Cambridge", "transaction": "sale", "max_pages": 1})
        assert out["source"] == "rightmove"
        assert len(out["listings"]) >= 5

    @respx.mock
    async def test_search_invalid_transaction_coerces_to_sale(self) -> None:
        html = _read("zoopla/search_cambridgeshire_2026-04.html")
        respx.get(url__regex=r"https://www\.zoopla\.co\.uk/for-sale/property/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        tool = _tool_by_name("search_zoopla")
        out = await tool.ainvoke(
            {"location": "Cambridge", "transaction": "buy-to-let", "max_pages": 1}
        )
        assert out["query"]["transaction"] == "sale"


class TestGetListingByUrl:
    """`get_listing_by_url` dispatches on host → correct scraper parser."""

    @respx.mock
    async def test_rightmove_detail_url(self) -> None:
        html = _read("rightmove/detail_173261858_2026-04.html")
        url = "https://www.rightmove.co.uk/properties/173261858"
        respx.get(url).mock(return_value=httpx.Response(200, html=html))
        tool = _tool_by_name("get_listing_by_url")
        out = await tool.ainvoke({"url": url})
        assert out["source"] == "rightmove"
        assert out["url"].endswith("173261858")
        assert out["listing"] is not None
        assert out["listing"]["source"] == "rightmove"
        assert out["listing"]["source_id"] == "173261858"

    @respx.mock
    async def test_zoopla_detail_url(self) -> None:
        html = _read("zoopla/detail_72228361_2026-04.html")
        url = "https://www.zoopla.co.uk/for-sale/details/72228361/"
        respx.get(url).mock(return_value=httpx.Response(200, html=html))
        tool = _tool_by_name("get_listing_by_url")
        out = await tool.ainvoke({"url": url})
        assert out["source"] == "zoopla"
        assert out["listing"] is not None
        assert out["listing"]["source"] == "zoopla"
        assert out["listing"]["source_id"] == "72228361"

    @respx.mock
    async def test_onthemarket_detail_url(self) -> None:
        html = _read("onthemarket/detail_18999957_2026-04.html")
        url = "https://www.onthemarket.com/details/18999957/"
        respx.get(url).mock(return_value=httpx.Response(200, html=html))
        tool = _tool_by_name("get_listing_by_url")
        out = await tool.ainvoke({"url": url})
        assert out["source"] == "onthemarket"
        assert out["listing"] is not None
        assert out["listing"]["source"] == "onthemarket"
        assert out["listing"]["source_id"] == "18999957"

    async def test_unsupported_host_rejected_before_network(self) -> None:
        # No respx.mock decorator — any accidental HTTP call here would
        # raise ConnectError, which would mask the ValueError we want to
        # see. The tool must reject the URL purely from its hostname.
        tool = _tool_by_name("get_listing_by_url")
        with pytest.raises(ValueError, match="Unsupported portal"):
            await tool.ainvoke({"url": "https://www.primelocation.com/for-sale/details/123/"})


class TestPostcodeTool:
    @respx.mock
    async def test_lookup_postcode(self) -> None:
        respx.get("https://api.postcodes.io/postcodes/CB11BS").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 200,
                    "result": {
                        "postcode": "CB1 1BS",
                        "longitude": 0.129,
                        "latitude": 52.201,
                        "country": "England",
                        "region": "East of England",
                        "admin_district": "Cambridge",
                        "admin_ward": "Market",
                        "outcode": "CB1",
                        "incode": "1BS",
                    },
                },
            )
        )
        tool = _tool_by_name("lookup_postcode")
        out = await tool.ainvoke({"postcode": "CB1 1BS"})
        assert out["postcode"] == "CB1 1BS"
        assert out["admin_district"] == "Cambridge"
        assert out["latitude"] == pytest.approx(52.201)


class TestUkPostcodeValidator:
    """Fail-fast regex so ``lookup_postcode(Cambridge)`` never hits the network."""

    def test_accepts_all_standard_uk_postcode_shapes(self) -> None:
        from uk_property_agent.tools import _validate_uk_postcode

        # Shapes: AA9A 9AA / A9A 9AA / A9 9AA / A99 9AA / AA9 9AA / AA99 9AA
        for pc in [
            "CB1 2JW",
            "SW1A 1AA",
            "M1 1AE",
            "B33 8TH",
            "CR2 6XH",
            "DN55 1PT",
            "EC1A 1BB",
            "W1A 0AX",
            # Lenient on whitespace + case — postcodes.io itself is too.
            "cb1 2jw",
            "SW1A1AA",
            "  M1 1AE  ",
        ]:
            assert _validate_uk_postcode(pc) == pc

    def test_rejects_place_name_with_recovery_hint(self) -> None:
        from uk_property_agent.tools import _validate_uk_postcode

        for bad in ["Cambridge", "London", "Canary Wharf", "Hackney", ""]:
            with pytest.raises(ValueError) as exc_info:
                _validate_uk_postcode(bad)
            msg = str(exc_info.value)
            assert "not a UK postcode" in msg
            # Every error message should tell the LLM how to recover.
            assert "find_postcodes_for_place" in msg

    def test_rejects_partial_postcodes(self) -> None:
        from uk_property_agent.tools import _validate_uk_postcode

        for bad in ["CB1", "SW1A", "12345", "B 33 8TH not quite"]:
            with pytest.raises(ValueError):
                _validate_uk_postcode(bad)


class TestPostcodeValidationAtToolBoundary:
    """End-to-end check: pydantic validation fires before any HTTP call."""

    async def test_lookup_postcode_rejects_place_name_before_network(self) -> None:
        tool = _tool_by_name("lookup_postcode")
        # No respx mocks — if the validator fails open and attempts an
        # HTTP call, respx would raise on the unroutable request. By
        # catching the pydantic ValidationError first we prove no
        # network traffic is emitted for place-name inputs.
        with pytest.raises(Exception, match="not a UK postcode"):
            await tool.ainvoke({"postcode": "Cambridge"})


class TestFindPostcodesForPlaceTool:
    @respx.mock
    async def test_resolves_place_with_nearby_postcodes(self) -> None:
        respx.get(url__regex=r"https://api\.postcodes\.io/places(\?.*)?$").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 200,
                    "result": [
                        {
                            "name_1": "Cambridge",
                            "local_type": "City",
                            "county_unitary": "Cambridgeshire",
                            "region": "East of England",
                            "country": "England",
                            "latitude": 52.205,
                            "longitude": 0.116,
                        },
                        {
                            "name_1": "Cambridge",
                            "local_type": "Village",
                            "county_unitary": "Gloucestershire",
                            "region": "South West",
                            "country": "England",
                            "latitude": 51.740,
                            "longitude": -2.346,
                        },
                    ],
                },
            )
        )
        respx.get(url__regex=r"https://api\.postcodes\.io/postcodes\?.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 200,
                    "result": [
                        {"postcode": "CB2 3QQ", "longitude": 0.117, "latitude": 52.200},
                        {"postcode": "CB2 1TN", "longitude": 0.118, "latitude": 52.202},
                        {"postcode": "CB2 1QA", "longitude": 0.119, "latitude": 52.203},
                    ],
                },
            )
        )
        tool = _tool_by_name("find_postcodes_for_place")
        out = await tool.ainvoke({"query": "Cambridge", "limit": 5})

        assert out["query"] == "Cambridge"
        assert out["count"] == 2
        top, alt = out["matches"]
        assert top["name"] == "Cambridge"
        assert top["local_type"] == "City"
        assert top["county_unitary"] == "Cambridgeshire"
        # Top match carries nearby postcodes resolved via reverse geocode.
        assert "CB2 3QQ" in top["nearby_postcodes"]
        assert "CB2 1TN" in top["nearby_postcodes"]
        # Alternative also gets nearby postcodes (same mock, same answers) —
        # the contract is "every match has a nearby_postcodes list".
        assert alt["county_unitary"] == "Gloucestershire"
        assert isinstance(alt["nearby_postcodes"], list)

    @respx.mock
    async def test_no_matches_returns_empty_shape(self) -> None:
        respx.get(url__regex=r"https://api\.postcodes\.io/places(\?.*)?$").mock(
            return_value=httpx.Response(200, json={"status": 200, "result": []})
        )
        tool = _tool_by_name("find_postcodes_for_place")
        out = await tool.ainvoke({"query": "Atlantis"})
        assert out == {"query": "Atlantis", "count": 0, "matches": []}

    @respx.mock
    async def test_survives_reverse_geocode_failure(self) -> None:
        # A 500 on /postcodes must not kill the whole tool — the match
        # should still come back, just with an empty nearby_postcodes.
        respx.get(url__regex=r"https://api\.postcodes\.io/places(\?.*)?$").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 200,
                    "result": [
                        {
                            "name_1": "Cambridge",
                            "local_type": "City",
                            "latitude": 52.205,
                            "longitude": 0.116,
                        }
                    ],
                },
            )
        )
        respx.get(url__regex=r"https://api\.postcodes\.io/postcodes\?.*").mock(
            return_value=httpx.Response(500, json={"status": 500})
        )
        tool = _tool_by_name("find_postcodes_for_place")
        out = await tool.ainvoke({"query": "Cambridge"})
        assert out["count"] == 1
        assert out["matches"][0]["nearby_postcodes"] == []


class TestSoldPricesTool:
    @respx.mock
    async def test_empty_postcode(self) -> None:
        respx.get(
            url__regex=r"https://landregistry\.data\.gov\.uk/data/ppi/transaction-record\.json.*"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"items": [], "itemsPerPage": 100, "page": 0}},
            )
        )
        tool = _tool_by_name("sold_prices_for_postcode")
        out = await tool.ainvoke({"postcode": "CB1 1BS"})
        assert out["count"] == 0
        assert out["records"] == []


def _mock_postcode(postcode: str, lat: float, lng: float) -> None:
    normalised = postcode.replace(" ", "").upper()
    respx.get(f"https://api.postcodes.io/postcodes/{normalised}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": 200,
                "result": {
                    "postcode": postcode,
                    "longitude": lng,
                    "latitude": lat,
                    "country": "England",
                    "region": "East of England",
                },
            },
        )
    )


class TestDistanceBetweenPostcodesTool:
    @respx.mock
    async def test_computes_distance(self) -> None:
        _mock_postcode("CB1 1BS", 52.201, 0.129)
        _mock_postcode("NR2 1AB", 52.628, 1.298)
        tool = _tool_by_name("distance_between_postcodes")
        out = await tool.ainvoke({"from_postcode": "CB1 1BS", "to_postcode": "NR2 1AB"})
        assert out["from_postcode"] == "CB1 1BS"
        assert out["to_postcode"] == "NR2 1AB"
        # Cambridge -> Norwich straight line is around 90 km.
        assert 85_000 < out["distance_m"] < 100_000
        # km / miles are rounded for readability, allow a small rounding slack.
        assert out["distance_km"] == pytest.approx(out["distance_m"] / 1000.0, abs=0.01)
        assert out["distance_miles"] == pytest.approx(out["distance_m"] / 1609.344, abs=0.01)
        assert out["from_lat_lng"] == {"lat": 52.201, "lng": 0.129}

    @respx.mock
    async def test_rejects_missing_coordinates(self) -> None:
        respx.get("https://api.postcodes.io/postcodes/CB11BS").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": 200,
                    "result": {
                        "postcode": "CB1 1BS",
                        "latitude": None,
                        "longitude": None,
                    },
                },
            )
        )
        _mock_postcode("NR2 1AB", 52.628, 1.298)
        tool = _tool_by_name("distance_between_postcodes")
        with pytest.raises(ValueError, match="no coordinates"):
            await tool.ainvoke({"from_postcode": "CB1 1BS", "to_postcode": "NR2 1AB"})


class TestAmenitiesNearPostcodeTool:
    @respx.mock
    async def test_returns_sorted_amenities(self) -> None:
        _mock_postcode("CB1 1BS", 52.201, 0.129)
        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 52.2100,
                            "lon": 0.1290,
                            "tags": {"railway": "station", "name": "Far Station"},
                        },
                        {
                            "type": "node",
                            "id": 2,
                            "lat": 52.2015,
                            "lon": 0.1291,
                            "tags": {"railway": "station", "name": "Near Station"},
                        },
                    ],
                },
            )
        )
        tool = _tool_by_name("amenities_near_postcode")
        out = await tool.ainvoke(
            {
                "postcode": "CB1 1BS",
                "categories": ["rail_station"],
                "radius_m": 1000,
                "limit": 5,
            }
        )
        assert out["postcode"] == "CB1 1BS"
        assert out["count"] == 2
        names = [item["name"] for item in out["items"]]
        assert names == ["Near Station", "Far Station"]

    @respx.mock
    async def test_unknown_categories_fallback_to_rail_station(self) -> None:
        _mock_postcode("CB1 1BS", 52.201, 0.129)

        captured: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured.append(request.content.decode())
            return httpx.Response(200, json={"elements": []})

        respx.post("https://overpass-api.de/api/interpreter").mock(side_effect=capture)

        tool = _tool_by_name("amenities_near_postcode")
        out = await tool.ainvoke(
            {
                "postcode": "CB1 1BS",
                "categories": ["nonsense"],
                "radius_m": 500,
            }
        )
        assert out["categories"] == ["rail_station"]
        assert captured and 'railway"="station' in captured[0]

    @respx.mock
    async def test_truncation_flag(self) -> None:
        _mock_postcode("CB1 1BS", 52.201, 0.129)
        elements = [
            {
                "type": "node",
                "id": idx,
                "lat": 52.2010 + idx * 0.0005,
                "lon": 0.1290,
                "tags": {"amenity": "cafe", "name": f"Cafe {idx}"},
            }
            for idx in range(1, 6)
        ]
        respx.post("https://overpass-api.de/api/interpreter").mock(
            return_value=httpx.Response(200, json={"elements": elements})
        )
        tool = _tool_by_name("amenities_near_postcode")
        out = await tool.ainvoke(
            {
                "postcode": "CB1 1BS",
                "categories": ["cafe"],
                "radius_m": 1500,
                "limit": 2,
            }
        )
        assert out["count"] == 5
        assert len(out["items"]) == 2
        assert out["truncated"] is True


def _ch_ctx() -> ToolContext:
    """Context with a Companies House factory wired to a fake API key.

    The key is only used for Basic-auth headers; respx intercepts the
    requests, so no real network hit happens.
    """

    def factory() -> CompaniesHouseClient:
        return CompaniesHouseClient(api_key="test-key")

    return ToolContext(crawler_factory=_fast_crawler_factory, companies_house_factory=factory)


class TestCompaniesHouseTools:
    def test_tools_wired_when_factory_present(self) -> None:
        tools = build_tools(_ch_ctx())
        names = {t.name for t in tools}
        assert names >= {
            "search_companies",
            "company_profile",
            "company_officers",
            "company_psc",
            "company_filings",
            "company_charges",
        }

    @respx.mock
    async def test_search_companies(self) -> None:
        respx.get("https://api.company-information.service.gov.uk/search/companies").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "company_number": "12345678",
                            "title": "ACME PROPERTIES LTD",
                            "company_status": "active",
                            "company_type": "ltd",
                            "address_snippet": "1 High St, London",
                            "date_of_creation": "2015-02-11",
                        }
                    ],
                    "total_results": 1,
                    "items_per_page": 20,
                    "start_index": 0,
                },
            )
        )
        tool = _tool_by_name("search_companies", _ch_ctx())
        out = await tool.ainvoke({"query": "acme properties"})
        assert out["items"][0]["company_number"] == "12345678"
        assert out["items"][0]["title"] == "ACME PROPERTIES LTD"

    @respx.mock
    async def test_company_profile_uses_get_company(self) -> None:
        """Regression: the tool previously called a non-existent
        `CompaniesHouseClient.company_profile` method. It must use
        `get_company`, which maps to the ``/company/{number}`` endpoint.
        """

        route = respx.get("https://api.company-information.service.gov.uk/company/12345678").mock(
            return_value=httpx.Response(
                200,
                json={
                    "company_name": "ACME PROPERTIES LTD",
                    "company_number": "12345678",
                    "company_status": "active",
                    "type": "ltd",
                    "date_of_creation": "2015-02-11",
                    "sic_codes": ["68100"],
                },
            )
        )
        tool = _tool_by_name("company_profile", _ch_ctx())
        out = await tool.ainvoke({"company_number": "12345678"})
        assert route.called
        assert out["company_name"] == "ACME PROPERTIES LTD"
        assert out["company_number"] == "12345678"
        assert out["sic_codes"] == ["68100"]

    @respx.mock
    async def test_company_officers(self) -> None:
        respx.get("https://api.company-information.service.gov.uk/company/12345678/officers").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "name": "SMITH, John",
                            "officer_role": "director",
                            "appointed_on": "2015-02-11",
                            "nationality": "British",
                            "occupation": "Director",
                        }
                    ],
                    "total_results": 1,
                    "items_per_page": 35,
                    "start_index": 0,
                },
            )
        )
        tool = _tool_by_name("company_officers", _ch_ctx())
        out = await tool.ainvoke({"company_number": "12345678"})
        assert out["items"][0]["name"] == "SMITH, John"
        assert out["items"][0]["officer_role"] == "director"

    @respx.mock
    async def test_company_psc(self) -> None:
        respx.get(
            "https://api.company-information.service.gov.uk/"
            "company/12345678/persons-with-significant-control"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "kind": "individual-person-with-significant-control",
                            "name": "Jane Doe",
                            "notified_on": "2016-06-30",
                            "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                        }
                    ],
                    "total_results": 1,
                    "items_per_page": 35,
                    "start_index": 0,
                },
            )
        )
        tool = _tool_by_name("company_psc", _ch_ctx())
        out = await tool.ainvoke({"company_number": "12345678"})
        assert out["items"][0]["name"] == "Jane Doe"

    @respx.mock
    async def test_company_filings(self) -> None:
        respx.get(
            "https://api.company-information.service.gov.uk/company/12345678/filing-history"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "transaction_id": "tx-1",
                            "type": "CS01",
                            "date": "2025-02-11",
                            "description": "confirmation-statement",
                            "category": "confirmation-statement",
                        }
                    ],
                    "total_count": 1,
                    "items_per_page": 25,
                    "start_index": 0,
                },
            )
        )
        tool = _tool_by_name("company_filings", _ch_ctx())
        out = await tool.ainvoke({"company_number": "12345678"})
        assert out["items"][0]["type"] == "CS01"

    @respx.mock
    async def test_company_charges(self) -> None:
        respx.get("https://api.company-information.service.gov.uk/company/12345678/charges").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "charge_code": "1234567",
                            "charge_number": 1,
                            "status": "outstanding",
                            "delivered_on": "2018-05-14",
                        }
                    ],
                    "total_count": 1,
                    "items_per_page": 25,
                    "start_index": 0,
                },
            )
        )
        tool = _tool_by_name("company_charges", _ch_ctx())
        out = await tool.ainvoke({"company_number": "12345678"})
        assert out["items"][0]["charge_code"] == "1234567"
        assert out["items"][0]["status"] == "outstanding"


def _ppd_item(
    *,
    transaction_id: str,
    price: int,
    date_str: str,
    property_type: str = "Terraced",
    postcode: str = "CB1 1BS",
) -> dict[str, object]:
    """Build one ``transaction-record.json`` item in the shape the client expects."""

    return {
        "transactionId": transaction_id,
        "pricePaid": price,
        "transactionDate": date_str,
        "newBuild": False,
        "estateType": {
            "prefLabel": [{"_value": "Freehold", "_datatype": "langString", "_lang": "en"}]
        },
        "propertyType": {
            "prefLabel": [{"_value": property_type, "_datatype": "langString", "_lang": "en"}]
        },
        "propertyAddress": {
            "paon": "1",
            "street": "High St",
            "town": "Cambridge",
            "district": "Cambridge",
            "county": "Cambridgeshire",
            "postcode": postcode,
        },
    }


class TestEstimatePropertyValueTool:
    @respx.mock
    async def test_builds_estimate_from_ppd(self) -> None:
        now_year = 2026
        items = [
            _ppd_item(
                transaction_id=f"t-{i}",
                price=450_000 + i * 10_000,
                date_str=f"Mon, 01 Jun {now_year - 1}",
            )
            for i in range(6)
        ]
        respx.get(
            url__regex=r"https://landregistry\.data\.gov\.uk/data/ppi/transaction-record\.json.*"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"items": items, "itemsPerPage": 100, "page": 0}},
            )
        )

        tool = _tool_by_name("estimate_property_value")
        out = await tool.ainvoke({"postcode": "CB1 1BS"})

        assert out["postcode"] == "CB1 1BS"
        assert out["ppd_rows_fetched"] == 6
        assert out["comparables_considered"] == 6
        est = out["estimate"]
        assert est["postcode"] == "CB1 1BS"
        assert est["basis"] == "postcode_area"
        assert est["comparables_used"] == 6
        assert est["low_gbp"] <= est["estimate_gbp"] <= est["high_gbp"]
        assert 450_000 <= est["estimate_gbp"] <= 510_000

    @respx.mock
    async def test_insufficient_data_when_empty(self) -> None:
        respx.get(
            url__regex=r"https://landregistry\.data\.gov\.uk/data/ppi/transaction-record\.json.*"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"items": [], "itemsPerPage": 100, "page": 0}},
            )
        )
        tool = _tool_by_name("estimate_property_value")
        out = await tool.ainvoke({"postcode": "CB1 1BS"})
        assert out["ppd_rows_fetched"] == 0
        assert out["comparables_considered"] == 0
        assert out["estimate"]["basis"] == "insufficient_data"
        assert out["estimate"]["confidence"] == "low"
        assert out["estimate"]["estimate_gbp"] == 0

    @respx.mock
    async def test_years_back_sets_cutoff(self) -> None:
        """A 2-year window drops sales that are older than that.

        We load 3 rows: one within window, two outside. Only the fresh one
        should feed the estimate, so ``comparables_considered`` is 1 and the
        AVM falls back to ``insufficient_data``.
        """

        items = [
            _ppd_item(
                transaction_id="fresh",
                price=500_000,
                date_str="Mon, 01 Jun 2025",
            ),
            _ppd_item(
                transaction_id="old-1",
                price=400_000,
                date_str="Mon, 01 Jun 2010",
            ),
            _ppd_item(
                transaction_id="old-2",
                price=420_000,
                date_str="Mon, 01 Jun 2012",
            ),
        ]
        respx.get(
            url__regex=r"https://landregistry\.data\.gov\.uk/data/ppi/transaction-record\.json.*"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"result": {"items": items, "itemsPerPage": 100, "page": 0}},
            )
        )
        tool = _tool_by_name("estimate_property_value")
        out = await tool.ainvoke({"postcode": "CB1 1BS", "years_back": 2})
        assert out["ppd_rows_fetched"] == 3
        assert out["comparables_considered"] == 1
        assert out["estimate"]["basis"] == "insufficient_data"


# ── Planning tools (Idox ArcGIS + HTML fallback) ────────────────────────────


_ARCGIS_QUERY_URL_RE = (
    r"https://planning\.lambeth\.gov\.uk/server/rest/services/PALIVE/"
    r"LIVEUniformPA_Planning/FeatureServer/\d+/query"
)

_WESTMINSTER_SEARCH_BASE = "https://idoxpa.westminster.gov.uk/online-applications"


def _arcgis_feature(
    *,
    key_val: str,
    reference: str,
    address: str,
    description: str = "",
    lon: float = -0.12,
    lat: float = 51.49,
    date_modified_ms: int = 1_700_000_000_000,
) -> dict[str, Any]:
    return {
        "attributes": {
            "REFVAL": reference,
            "KEYVAL": key_val,
            "ADDRESS": address,
            "DESCRIPTION": description,
            "DATEMODIFIED": date_modified_ms,
            "ISPAVISIBLE": 1,
        },
        "geometry": {"x": lon, "y": lat},
    }


class TestPlanningCouncilsTool:
    async def test_list_planning_councils_reports_transport_support(self) -> None:
        tool = _tool_by_name("list_planning_councils")
        out = await tool.ainvoke({})
        # Public library ships reference configs for one ArcGIS council
        # (Lambeth) and one HTML-only council (Westminster); the full
        # curated set of supported councils lives behind the hosted A5
        # actor and is not exposed through this tool.
        assert out["count"] >= 2
        by_slug = {row["slug"]: row for row in out["councils"]}
        # Lambeth is ArcGIS-backed (supports 'recent'); Westminster is HTML-only.
        assert by_slug["lambeth"]["supports_arcgis"] is True
        assert by_slug["lambeth"]["supports_recent"] is True
        assert by_slug["westminster"]["supports_arcgis"] is False
        assert by_slug["westminster"]["supports_recent"] is False
        # Every council exposes the Public Access origin for HTML search.
        assert all(row["public_access_base_url"].startswith("https://") for row in out["councils"])


class TestSearchPlanningApplicationsTool:
    @respx.mock
    async def test_recent_mode_uses_arcgis(self) -> None:
        payload = {
            "objectIdFieldName": "OBJECTID",
            "exceededTransferLimit": False,
            "features": [
                _arcgis_feature(
                    key_val="KEY1",
                    reference="26/00001/FUL",
                    address="1 Acre Lane, London",
                ),
                _arcgis_feature(
                    key_val="KEY2",
                    reference="26/00002/FUL",
                    address="2 Acre Lane, London",
                ),
            ],
        }
        route = respx.get(url__regex=rf"{_ARCGIS_QUERY_URL_RE}.*").mock(
            return_value=httpx.Response(200, json=payload)
        )
        tool = _tool_by_name("search_planning_applications")
        out = await tool.ainvoke({"council": "lambeth", "mode": "recent", "since_days": 7})
        assert route.called
        assert out["council"] == "lambeth"
        assert out["mode"] == "recent"
        assert out["transport"] == "arcgis"
        assert out["count"] == 2
        assert {row["reference"] for row in out["applications"]} == {
            "26/00001/FUL",
            "26/00002/FUL",
        }

    async def test_recent_mode_rejects_html_only_council(self) -> None:
        tool = _tool_by_name("search_planning_applications")
        with pytest.raises(ValueError, match="does not publish an ArcGIS"):
            await tool.ainvoke({"council": "westminster", "mode": "recent"})

    @respx.mock
    async def test_search_mode_uses_arcgis_when_available(self) -> None:
        route = respx.get(url__regex=rf"{_ARCGIS_QUERY_URL_RE}.*").mock(
            return_value=httpx.Response(
                200,
                json={
                    "objectIdFieldName": "OBJECTID",
                    "exceededTransferLimit": False,
                    "features": [
                        _arcgis_feature(
                            key_val="KEY3",
                            reference="26/00003/FUL",
                            address="3 Acre Lane, London",
                        )
                    ],
                },
            )
        )
        tool = _tool_by_name("search_planning_applications")
        out = await tool.ainvoke(
            {
                "council": "lambeth",
                "mode": "search",
                "query": "Acre Lane",
                "max_results": 5,
            }
        )
        assert route.called
        assert out["transport"] == "arcgis"
        assert out["query"] == "Acre Lane"
        assert out["count"] == 1
        request_url = str(route.calls.last.request.url)
        assert "ADDRESS+LIKE+%27%25Acre+Lane%25%27" in request_url

    @respx.mock
    async def test_search_mode_falls_back_to_html_for_html_only_council(self) -> None:
        search_form = """
        <html><head></head><body>
        <form>
          <input type='hidden' name='_csrf' value='tok-123' />
        </form>
        </body></html>
        """
        results_html = """
        <html><body>
        <ul id="searchresults">
          <li class="searchresult">
            <a href="/online-applications/applicationDetails.do?activeTab=summary&keyVal=ABC123">
              1 Victoria Street, Westminster
            </a>
            <p class="address">1 Victoria Street, Westminster, SW1</p>
            <p class="metaInfo">
              Ref. No: 26/00010/FUL
              <span class="divider">|</span>
              Received: Wed 14 Jan 2026
              <span class="divider">|</span>
              Validated: Thu 15 Jan 2026
              <span class="divider">|</span>
              Status: Awaiting decision
            </p>
          </li>
        </ul>
        </body></html>
        """
        form_route = respx.get(f"{_WESTMINSTER_SEARCH_BASE}/search.do").mock(
            return_value=httpx.Response(200, text=search_form)
        )
        search_route = respx.post(f"{_WESTMINSTER_SEARCH_BASE}/simpleSearchResults.do").mock(
            return_value=httpx.Response(200, text=results_html)
        )
        tool = _tool_by_name("search_planning_applications")
        out = await tool.ainvoke(
            {
                "council": "westminster",
                "mode": "search",
                "query": "Victoria Street",
            }
        )
        assert form_route.called
        assert search_route.called
        assert out["transport"] == "html"
        assert out["count"] == 1
        assert out["applications"][0]["reference"] == "26/00010/FUL"
        assert out["applications"][0]["key_val"] == "ABC123"

    async def test_search_mode_requires_query(self) -> None:
        tool = _tool_by_name("search_planning_applications")
        with pytest.raises(ValueError, match="non-empty query"):
            await tool.ainvoke({"council": "lambeth", "mode": "search"})

    async def test_unknown_council_raises(self) -> None:
        tool = _tool_by_name("search_planning_applications")
        with pytest.raises(ValueError, match="Unknown council"):
            await tool.ainvoke({"council": "narnia", "mode": "recent"})


class TestLookupPlanningApplicationTool:
    @respx.mock
    async def test_lookup_by_key_val_via_html(self) -> None:
        detail_html = """
        <html><head><title>Application Summary</title></head><body>
        <table id="simpleDetailsTable">
          <tr><th scope="row">Reference</th><td>26/00042/FUL</td></tr>
          <tr><th scope="row">Address</th><td>10 Example Road, London</td></tr>
          <tr><th scope="row">Proposal</th><td>Single-storey rear extension.</td></tr>
          <tr><th scope="row">Status</th><td>Awaiting decision</td></tr>
          <tr><th scope="row">Case Officer</th><td>A. Smith</td></tr>
          <tr><th scope="row">Ward</th><td>Somewhere</td></tr>
        </table>
        </body></html>
        """
        route = respx.get(f"{_WESTMINSTER_SEARCH_BASE}/applicationDetails.do").mock(
            return_value=httpx.Response(200, text=detail_html)
        )
        tool = _tool_by_name("lookup_planning_application")
        out = await tool.ainvoke({"council": "westminster", "key_val": "KEY42"})
        assert route.called
        assert out["found"] is True
        assert out["transport"] == "html"
        app = out["application"]
        assert app["reference"] == "26/00042/FUL"
        assert app["key_val"] == "KEY42"
        assert app["case_officer"] == "A. Smith"

    async def test_requires_at_least_one_identifier(self) -> None:
        tool = _tool_by_name("lookup_planning_application")
        with pytest.raises(ValueError, match="at least one"):
            await tool.ainvoke({"council": "westminster"})


# ── Landlord-graph tool (Companies House) ───────────────────────────────────


def _mock_ch(pattern: str, payload: dict[str, Any]) -> None:
    respx.get(url__regex=pattern).mock(return_value=httpx.Response(200, json=payload))


class TestLandlordNetworkTool:
    def test_only_wired_when_ch_factory_present(self) -> None:
        tools = build_tools(_test_ctx())
        assert "landlord_network_for_company" not in {t.name for t in tools}

    @respx.mock
    async def test_returns_structured_graph_at_depth_1(self) -> None:
        base = "https://api.company-information.service.gov.uk"
        _mock_ch(
            rf"{base}/company/12345678$",
            {
                "company_name": "SPV LTD",
                "company_number": "12345678",
                "company_status": "active",
                "type": "ltd",
                "date_of_creation": "2018-01-01",
                "sic_codes": ["68100"],
            },
        )
        _mock_ch(
            rf"{base}/company/12345678/officers\b.*",
            {
                "items": [
                    {
                        "name": "SMITH, John",
                        "officer_role": "director",
                        "appointed_on": "2018-01-01",
                        "links": {"officer": {"appointments": "/officers/off1/appointments"}},
                    }
                ],
                "total_results": 1,
            },
        )
        _mock_ch(
            rf"{base}/company/12345678/persons-with-significant-control\b.*",
            {"items": [], "total_results": 0},
        )
        tool = _tool_by_name("landlord_network_for_company", _ch_ctx())
        out = await tool.ainvoke({"company_number": "12345678", "depth": 1})

        assert out["seed_company_number"] == "12345678"
        assert out["depth"] == 1
        kinds = {node["kind"] for node in out["nodes"]}
        assert kinds == {"company", "officer"}
        assert any(edge["relation"] == "officer_of" for edge in out["edges"])
