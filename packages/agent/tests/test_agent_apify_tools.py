"""Unit tests for :mod:`uk_property_agent.apify_tools`.

These tests exercise the four actor-only agent tools (auctions,
tenders, demographics, climate-risk) by stubbing the
``ApifyDelegation`` layer. They verify that

* ``build_apify_only_tools`` only registers a tool when its actor is
  resolvable,
* each tool passes its ``args_schema`` correctly to
  ``ApifyDelegation.call``,
* the ``_map_*_result`` helpers round-trip canonical model dumps back
  into the shape the agent expects (AuctionLot validated,
  Tender canonical keys, envelope pass-through for demographics and
  climate-risk).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from uk_property_apify_client import ApifyDelegation
from uk_property_apis import Tender
from uk_property_apis.tenders.models import TenderSource, TenderStatus
from uk_property_scrapers import AuctionLot
from uk_property_scrapers.schema import (
    Address,
    AuctionHouse,
    AuctionLotStatus,
    AuctionSaleMethod,
    PropertyType,
    Tenure,
)


@pytest.fixture
def apify_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the Apify delegation env is configured."""

    monkeypatch.setenv("APIFY_API_TOKEN", "dummy-token")
    monkeypatch.setenv("APIFY_USERNAME", "test-user")


def _sample_auction_lot() -> AuctionLot:
    return AuctionLot(
        auction_house=AuctionHouse.ALLSOP,
        source_id="lot-42",
        source_url="https://example.com/lot/42",
        lot_number="42",
        sale_method=AuctionSaleMethod.TRADITIONAL,
        status=AuctionLotStatus.AVAILABLE,
        property_type=PropertyType.FLAT,
        tenure=Tenure.LEASEHOLD,
        address=Address(
            raw="Flat 1, 10 Example St, London SW1A 1AA",
            postcode="SW1A 1AA",
        ),
        title="Sample lot",
    )


def _sample_tender() -> Tender:
    return Tender(
        source=TenderSource.CONTRACTS_FINDER,
        source_id="cf-1",
        title="Residential construction framework",
        status=TenderStatus.OPEN,
    )


class TestBuildApifyOnlyTools:
    def test_no_tools_without_env(self) -> None:
        from uk_property_agent.apify_tools import build_apify_only_tools

        assert build_apify_only_tools() == []

    def test_all_four_tools_with_env(self, apify_env: None) -> None:
        from uk_property_agent.apify_tools import build_apify_only_tools

        names = sorted(t.name for t in build_apify_only_tools())
        assert names == sorted(
            [
                "climate_risk_for_point",
                "demographics_for_area",
                "find_auction_properties",
                "find_public_tenders",
            ]
        )


class TestAuctionsDelegation:
    @pytest.mark.asyncio
    async def test_find_auction_properties_maps_rows_to_auction_lot(
        self, monkeypatch: pytest.MonkeyPatch, apify_env: None
    ) -> None:
        from uk_property_agent import apify_tools

        lot = _sample_auction_lot()
        actor_row = {
            "auction_id": "uuid-a",
            "auction_source": "allsop",
            "auction_reference": "R260430",
            "auction_name": "April 2026 Residential",
            "auction_date_day1": "2026-04-29",
            "auction_date_day2": None,
            **lot.model_dump(mode="json"),
        }

        fake_call = AsyncMock(
            return_value=type(
                "Result",
                (),
                {
                    "items": [actor_row],
                    "run_meta": {"totals": {"lots": 1}},
                },
            )()
        )
        fake_delegation = type(
            "Delegation",
            (),
            {"call": fake_call, "actor_id": type("Id", (), {"full_id": "x~uk-auctions"})()},
        )()
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda _cls, _slug: fake_delegation),
        )

        result = await apify_tools._find_auction_properties(discover=True)

        assert result["count"] == 1
        assert result["errors"] is None
        entry = result["lots"][0]
        assert entry["auction"]["auction_id"] == "uuid-a"
        assert entry["auction"]["auction_source"] == "allsop"
        assert entry["lot"]["source_id"] == "lot-42"
        assert entry["lot"]["auction_house"] == "allsop"
        assert result["run_meta"] == {"totals": {"lots": 1}}
        fake_call.assert_awaited_once()
        (payload,), _ = fake_call.call_args
        assert payload["discover"] is True
        assert payload["auctionIds"] == []
        assert payload["maxAuctions"] == 10

    @pytest.mark.asyncio
    async def test_find_auction_properties_requires_source_when_ids_given(
        self, monkeypatch: pytest.MonkeyPatch, apify_env: None
    ) -> None:
        from uk_property_agent import apify_tools

        fake_delegation = type(
            "Delegation",
            (),
            {
                "call": AsyncMock(),
                "actor_id": type("Id", (), {"full_id": "x~uk-auctions"})(),
            },
        )()
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda _cls, _slug: fake_delegation),
        )

        with pytest.raises(ValueError, match="requires 'source'"):
            await apify_tools._find_auction_properties(auction_ids=["x"])


class TestTendersDelegation:
    @pytest.mark.asyncio
    async def test_find_public_tenders_validates_rows(
        self, monkeypatch: pytest.MonkeyPatch, apify_env: None
    ) -> None:
        from uk_property_agent import apify_tools

        tender = _sample_tender()
        actor_row = {
            "source": "contracts-finder",
            **tender.model_dump(mode="json"),
        }

        fake_call = AsyncMock(
            return_value=type(
                "Result",
                (),
                {
                    "items": [actor_row, {"malformed": True}],
                    "run_meta": {"totals": {"tenders": 1}},
                },
            )()
        )
        fake_delegation = type(
            "Delegation",
            (),
            {"call": fake_call, "actor_id": type("Id", (), {"full_id": "x~uk-tenders"})()},
        )()
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda _cls, _slug: fake_delegation),
        )

        result = await apify_tools._find_public_tenders(keyword="construction")

        assert result["count"] == 1
        assert result["tenders"][0]["source"] == "contracts-finder"
        assert result["tenders"][0]["source_id"] == "cf-1"
        assert result["tenders"][0]["title"] == "Residential construction framework"
        assert result["errors"] is not None
        assert len(result["errors"]) == 1
        (payload,), _ = fake_call.call_args
        assert payload["keyword"] == "construction"
        assert payload["sources"] == ["contracts-finder", "find-a-tender"]


class TestDemographicsDelegation:
    @pytest.mark.asyncio
    async def test_demographics_passes_postcode_and_defaults(
        self, monkeypatch: pytest.MonkeyPatch, apify_env: None
    ) -> None:
        from uk_property_agent import apify_tools

        envelope: dict[str, Any] = {
            "area": {"postcode": "SW1A 1AA", "lsoa": "E01004735"},
            "nomis": {"unemployment_rate": 3.4},
        }

        fake_call = AsyncMock(
            return_value=type(
                "Result",
                (),
                {
                    "items": [envelope],
                    "run_meta": {"totals": {"areas": 1}},
                },
            )()
        )
        fake_delegation = type(
            "Delegation",
            (),
            {
                "call": fake_call,
                "actor_id": type("Id", (), {"full_id": "x~uk-demographics"})(),
            },
        )()
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda _cls, _slug: fake_delegation),
        )

        result = await apify_tools._demographics_for_area(postcode=" sw1a 1aa ")

        assert result["area"] == {"postcode": "SW1A 1AA", "lsoa": "E01004735"}
        assert result["nomis"] == {"unemployment_rate": 3.4}
        assert result["run_meta"] == {"totals": {"areas": 1}}
        (payload,), _ = fake_call.call_args
        assert payload["areas"] == ["SW1A 1AA"]
        assert payload["sources"] == ["nomis", "census", "imd", "mhclg"]
        assert payload["censusTables"] == ["TS001", "TS044", "TS021", "TS067"]


class TestClimateRiskDelegation:
    @pytest.mark.asyncio
    async def test_climate_risk_requires_postcode_or_coords(
        self, monkeypatch: pytest.MonkeyPatch, apify_env: None
    ) -> None:
        from uk_property_agent import apify_tools

        fake_delegation = type(
            "Delegation",
            (),
            {
                "call": AsyncMock(),
                "actor_id": type("Id", (), {"full_id": "x~uk-climate-risk"})(),
            },
        )()
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda _cls, _slug: fake_delegation),
        )

        with pytest.raises(ValueError, match="postcode"):
            await apify_tools._climate_risk_for_point()

    @pytest.mark.asyncio
    async def test_climate_risk_passes_coordinates(
        self, monkeypatch: pytest.MonkeyPatch, apify_env: None
    ) -> None:
        from uk_property_agent import apify_tools

        envelope: dict[str, Any] = {
            "point": {"lat": 51.5, "lng": -0.1},
            "flood": {"warnings": []},
        }

        fake_call = AsyncMock(
            return_value=type(
                "Result",
                (),
                {"items": [envelope], "run_meta": {"totals": {"points": 1}}},
            )()
        )
        fake_delegation = type(
            "Delegation",
            (),
            {
                "call": fake_call,
                "actor_id": type("Id", (), {"full_id": "x~uk-climate-risk"})(),
            },
        )()
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda _cls, _slug: fake_delegation),
        )

        result = await apify_tools._climate_risk_for_point(lat=51.5, lng=-0.1)

        assert result["point"] == {"lat": 51.5, "lng": -0.1}
        assert result["flood"] == {"warnings": []}
        (payload,), _ = fake_call.call_args
        assert payload["points"] == [{"lat": 51.5, "lng": -0.1}]
        assert payload["floodRadiusKm"] == 10
