"""Unit tests for ``uk_property_scrapers.auctions.allsop``.

Uses real JSON payloads captured from the live Allsop site (see
``tests/fixtures/auctions/allsop/`` for provenance). The goal is to pin
the parser behaviour against production-shape data so future feed changes
blow up loudly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from uk_property_scrapers import (
    AuctionHouse,
    AuctionLot,
    AuctionLotStatus,
    AuctionSaleMethod,
    PriceQualifier,
    PropertyType,
    Tenure,
)
from uk_property_scrapers.auctions import allsop

# ── parse_search_results ────────────────────────────────────────────────────


class TestParseSearchResults:
    def test_returns_lots_in_feed_order(
        self, allsop_search_payload: dict[str, Any]
    ) -> None:
        lots = allsop.parse_search_results(allsop_search_payload)
        assert len(lots) == 5
        assert [lot.lot_number for lot in lots] == ["1", "2", "3", "4", "5"]

    def test_every_lot_validates(self, allsop_search_payload: dict[str, Any]) -> None:
        lots = allsop.parse_search_results(allsop_search_payload)
        for lot in lots:
            assert isinstance(lot, AuctionLot)
            assert lot.auction_house is AuctionHouse.ALLSOP

    def test_handles_missing_results_section_gracefully(self) -> None:
        assert allsop.parse_search_results({}) == []
        assert allsop.parse_search_results({"data": {}}) == []
        assert allsop.parse_search_results({"data": {"results": "garbage"}}) == []

    def test_skips_lots_without_reference_or_id(self) -> None:
        payload: dict[str, Any] = {
            "data": {
                "results": [
                    {"allsop_lotid": None, "reference": "R260430 001"},
                    {"allsop_lotid": "abc", "reference": None},
                    {"allsop_lotid": "abc", "reference": "", "location": {"lat": 0, "lon": 0}},
                ]
            }
        }
        assert allsop.parse_search_results(payload) == []

    def test_prefers_auction_meta_name_for_catalogue_id(
        self,
        allsop_search_payload: dict[str, Any],
        allsop_auction_payload: dict[str, Any],
    ) -> None:
        meta = allsop.parse_auction_metadata(allsop_auction_payload)
        lots = allsop.parse_search_results(allsop_search_payload, auction_meta=meta)
        # The upstream name retains a trailing space ('Residential - April- 2026 ').
        # We pass it through verbatim rather than normalising so downstream
        # callers can pin to Allsop's exact catalogue label if they want to.
        assert all(lot.catalogue_id == "Residential - April- 2026 " for lot in lots)

    def test_falls_back_to_reference_when_meta_absent(
        self, allsop_search_payload: dict[str, Any]
    ) -> None:
        lots = allsop.parse_search_results(allsop_search_payload)
        assert all(lot.catalogue_id == "R260430" for lot in lots)


# ── Field-level parsers (per lot) ───────────────────────────────────────────


class TestLotFields:
    @pytest.fixture(scope="class")
    def lots(self, allsop_search_payload: dict[str, Any]) -> list[AuctionLot]:
        return allsop.parse_search_results(allsop_search_payload)

    def test_lot_one_end_to_end(self, lots: list[AuctionLot]) -> None:
        lot = lots[0]
        assert lot.source_id == "9ac2cf22-1e22-11f1-80bb-0242ac110002"
        assert (
            str(lot.source_url)
            == "https://www.allsop.co.uk/lot-overview/"
            "vacant-freehold-link-semi-detached-house/r260430-098"
        )
        assert lot.lot_number == "1"
        # Day-1 lot — Wednesday 29 April 2026 (Europe/London). Catalogue
        # reference encodes the final sale day (R260430 = 30 Apr) which
        # is why ``reference`` and ``auction_date`` disagree.
        assert lot.auction_date == date(2026, 4, 29)
        assert lot.title == "VACANT - Freehold Link Semi Detached House"
        assert lot.tenure is Tenure.FREEHOLD
        assert lot.property_type is PropertyType.SEMI_DETACHED
        assert lot.property_type_raw == "House"
        assert lot.is_vacant_possession is True
        assert lot.status is AuctionLotStatus.AVAILABLE
        assert lot.sale_method is AuctionSaleMethod.TRADITIONAL

    def test_address_parsed_from_property_fields(
        self, lots: list[AuctionLot]
    ) -> None:
        lot = lots[0]
        assert lot.address.raw == "Bexley Gardens, Chadwell Heath, Romford, Essex"
        assert lot.address.postcode == "RM6 4FD"
        assert lot.address.postcode_outcode == "RM6"

    def test_coords_decoded_from_location_dict(
        self, lots: list[AuctionLot]
    ) -> None:
        lot = lots[0]
        assert lot.coords is not None
        assert lot.coords.lat == pytest.approx(51.577, rel=1e-3)
        assert lot.coords.lng == pytest.approx(0.1155, rel=1e-2)

    def test_guide_price_single_value_carries_in_excess_qualifier(
        self, lots: list[AuctionLot]
    ) -> None:
        guide = lots[0].guide_price
        assert guide is not None
        assert guide.raw == "£200,000+"
        assert guide.low_pence == 200_000 * 100
        assert guide.high_pence is None  # trailing-plus collapses
        assert guide.qualifier is PriceQualifier.OFFERS_IN_EXCESS_OF

    def test_lead_image_url_built_from_cdn_file_id(
        self, lots: list[AuctionLot]
    ) -> None:
        lot = lots[0]
        assert len(lot.image_urls) == 1
        url = str(lot.image_urls[0].url)
        assert url.startswith(
            "https://as-prod-bau-object-storage.s3.eu-west-2.amazonaws.com/image_cache/"
        )
        assert "80b61b0a-212b-11f1-96dc-0242ac110002" in url

    def test_description_formatted_as_bullet_list(
        self, lots: list[AuctionLot]
    ) -> None:
        lot = lots[0]
        assert lot.description is not None
        assert lot.description.splitlines()[0].startswith("- Ground Floor")

    def test_investment_lot_flagged_as_not_vacant(
        self, lots: list[AuctionLot]
    ) -> None:
        # Lot 3 in the fixture carries 'Part Let' tenancy. The feed
        # populates ``property_tenancy`` while leaving
        # ``allsop_propertytenancy`` null, so only the former lands in
        # raw_site_fields.
        lot = lots[2]
        assert lot.is_vacant_possession is False
        assert lot.raw_site_fields.get("property_tenancy") == "Part Let"

    def test_raw_passthrough_preserves_reference(
        self, lots: list[AuctionLot]
    ) -> None:
        assert lots[0].raw_site_fields["reference"] == "R260430 098"
        assert lots[0].raw_site_fields["lot_type"] == "residential"


# ── Range guide prices ──────────────────────────────────────────────────────


class TestRangeGuidePrice:
    def test_range_parsed_with_both_endpoints(
        self, allsop_range_payload: dict[str, Any]
    ) -> None:
        lots = allsop.parse_search_results(allsop_range_payload)
        assert lots, "expected at least one range lot"
        guide = lots[0].guide_price
        assert guide is not None
        assert " - " in guide.raw
        assert guide.low_pence is not None
        assert guide.high_pence is not None
        assert guide.high_pence > guide.low_pence
        assert guide.qualifier is PriceQualifier.GUIDE_PRICE


# ── Auction metadata ────────────────────────────────────────────────────────


class TestAuctionMetadata:
    def test_extracts_the_interesting_subset(
        self, allsop_auction_payload: dict[str, Any]
    ) -> None:
        meta = allsop.parse_auction_metadata(allsop_auction_payload)
        assert meta == {
            "auction_id": "16fe8330-8a60-11f0-a081-0242ac110002",
            "reference": "R260430",
            "name": "Residential - April- 2026 ",  # trailing space is upstream
            "venue": "Live Stream",
            "auction_type": "residential",
            "date_day1": date(2026, 4, 29),
            "date_day2": date(2026, 4, 30),
            "next_auction_date": "27th and 28th May 2026",
            "lots_sold": 6,
            "lots_unsold": 0,
            "value_sold_gbp": 1_837_000,
        }

    def test_returns_empty_dict_when_envelope_missing(self) -> None:
        assert allsop.parse_auction_metadata({}) == {}
        assert allsop.parse_auction_metadata({"auctionData": None}) == {}


# ── Helpers ────────────────────────────────────────────────────────────────


class TestInferAuctionDateFromReference:
    @pytest.mark.parametrize(
        ("reference", "expected"),
        [
            ("R260430 098", date(2026, 4, 30)),
            ("R250115 001", date(2025, 1, 15)),
            ("r261220 200", date(2026, 12, 20)),
        ],
    )
    def test_known_references(
        self, reference: str, expected: date
    ) -> None:
        assert allsop.infer_auction_date_from_reference(reference) == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "260430 098",
            "R2604 098",
            "R26043",
            "RABCDE",
            None,
        ],
    )
    def test_rejects_bad_input(self, bad: str | None) -> None:
        assert allsop.infer_auction_date_from_reference(bad or "") is None
