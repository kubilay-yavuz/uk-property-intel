"""Tests for the PPD + EPC join pipeline.

Tests are fixture-driven: we hand in hand-rolled PPD and EPC row objects
(duck-typed, not the real Pydantic models) so the tests stay isolated from
upstream schema drift in ``uk_property_apis``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from uk_property_avm import (
    EnrichedComparable,
    JoinConfig,
    enrich_comparables,
    normalise_address_key,
    normalise_postcode,
    parse_floor_area,
)


@dataclass
class _PPDRow:
    transaction_id: str
    price: int
    transfer_date: str
    property_type: str | None = None
    tenure: str | None = None
    paon: str | None = None
    street: str | None = None
    postcode: str | None = None


@dataclass
class _EPCRow:
    lmk_key: str
    postcode: str | None
    address: str | None
    total_floor_area: str | None = None
    current_energy_rating: str | None = None
    current_energy_efficiency: str | None = None
    built_form: str | None = None
    construction_age_band: str | None = None
    property_type: str | None = None
    inspection_date: str | None = None


class TestPostcodeNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("sw2 5tn", "SW2 5TN"),
            ("SW25TN", "SW2 5TN"),
            ("  ec1v3ap  ", "EC1V 3AP"),
            ("W1A 1AA", "W1A 1AA"),
        ],
    )
    def test_valid_postcodes_canonicalise(self, raw: str, expected: str) -> None:
        assert normalise_postcode(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "SW2", "notapostcode", None])
    def test_invalid_postcodes_return_none(self, raw: str | None) -> None:
        assert normalise_postcode(raw) is None


class TestAddressKeyNormalisation:
    def test_expands_abbreviations_and_uppercases(self) -> None:
        key = normalise_address_key(
            postcode="sw2 5tn",
            paon="12a",
            street="Acacia rd",
        )
        assert key == "SW2 5TN|12A|ACACIA ROAD"

    def test_collapses_whitespace_in_street(self) -> None:
        key = normalise_address_key(
            postcode="E1 1AA",
            paon="5",
            street="  Cable   Street  ",
        )
        assert key == "E1 1AA|5|CABLE STREET"

    @pytest.mark.parametrize(
        ("pc", "paon", "st"),
        [
            (None, "12", "ACACIA ROAD"),
            ("SW2 5TN", None, "ACACIA ROAD"),
            ("SW2 5TN", "12", None),
            ("bad-pc", "12", "ACACIA ROAD"),
        ],
    )
    def test_returns_none_when_any_part_missing(
        self, pc: str | None, paon: str | None, st: str | None
    ) -> None:
        assert normalise_address_key(postcode=pc, paon=paon, street=st) is None


class TestParseFloorArea:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("82", 82.0),
            ("82.5", 82.5),
            ("1,234.5", 1234.5),
            ("82 m2", 82.0),
            ("82 sqm", 82.0),
            ("82 square metres", 82.0),
            (82, 82.0),
            (82.5, 82.5),
        ],
    )
    def test_valid_inputs_parse(self, raw: object, expected: float) -> None:
        assert parse_floor_area(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [None, "", "  ", "not a number", 0, -5, 10_001, True])
    def test_invalid_inputs_return_none(self, raw: object) -> None:
        assert parse_floor_area(raw) is None


class TestEnrichExactMatch:
    def test_single_exact_match_populates_all_fields(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                property_type="T",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="EPC-1",
                postcode="SW2 5TN",
                address="12 Acacia Road, London",
                total_floor_area="85",
                current_energy_rating="C",
                current_energy_efficiency="72",
                built_form="Mid-Terrace",
                construction_age_band="England and Wales: 1900-1929",
                property_type="House",
                inspection_date="2023-06-15",
            )
        ]

        [result] = enrich_comparables(ppd, epc)

        assert isinstance(result, EnrichedComparable)
        assert result.transaction_id == "T1"
        assert result.price == 500_000
        assert result.match_quality == "exact_address"
        assert result.floor_area_sqm == 85.0
        assert result.energy_rating == "C"
        assert result.energy_efficiency == 72
        assert result.built_form == "Mid-Terrace"
        assert result.epc_property_type == "House"
        assert result.construction_age_band == "England and Wales: 1900-1929"
        assert result.epc_lmk_key == "EPC-1"
        assert result.epc_inspection_date == "2023-06-15"

    def test_picks_closest_in_time_epc(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="OLD",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="70",
                inspection_date="2015-01-01",
            ),
            _EPCRow(
                lmk_key="CLOSE",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="85",
                inspection_date="2023-12-01",
            ),
        ]

        [result] = enrich_comparables(ppd, epc)

        assert result.epc_lmk_key == "CLOSE"
        assert result.floor_area_sqm == 85.0

    def test_prefers_pre_sale_epc_when_configured(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="BEFORE",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="85",
                inspection_date="2023-09-01",
            ),
            _EPCRow(
                lmk_key="AFTER",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="95",
                inspection_date="2024-04-15",
            ),
        ]

        [result] = enrich_comparables(ppd, epc)

        assert result.epc_lmk_key == "BEFORE"
        assert result.floor_area_sqm == 85.0

    def test_takes_nearest_after_sale_when_prefer_before_disabled(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="BEFORE_FAR",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="85",
                inspection_date="2022-01-01",
            ),
            _EPCRow(
                lmk_key="AFTER_CLOSE",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="95",
                inspection_date="2024-04-01",
            ),
        ]

        [result] = enrich_comparables(
            ppd, epc, config=JoinConfig(prefer_before_sale=False)
        )

        assert result.epc_lmk_key == "AFTER_CLOSE"


class TestEnrichNoMatch:
    def test_unmatched_row_returned_with_none_fields(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc: list[_EPCRow] = []

        [result] = enrich_comparables(ppd, epc)

        assert result.match_quality == "none"
        assert result.floor_area_sqm is None
        assert result.energy_rating is None
        assert result.epc_lmk_key is None

    def test_rejects_match_outside_staleness_window(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="TOO_OLD",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="85",
                inspection_date="2010-01-01",
            )
        ]

        [result] = enrich_comparables(ppd, epc, config=JoinConfig(max_days_delta=365))

        assert result.match_quality == "none"
        assert result.epc_lmk_key is None


class TestPostcodeFallback:
    def test_fallback_finds_sibling_property_when_exact_misses(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="NEIGHBOUR",
                postcode="SW2 5TN",
                address="14 Acacia Road",
                total_floor_area="85",
                inspection_date="2023-06-01",
            )
        ]

        [result] = enrich_comparables(
            ppd, epc, config=JoinConfig(postcode_fallback=True)
        )

        assert result.match_quality == "postcode_only"
        assert result.epc_lmk_key == "NEIGHBOUR"

    def test_exact_match_wins_over_postcode_fallback(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="EXACT",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="90",
                inspection_date="2023-06-01",
            ),
            _EPCRow(
                lmk_key="NEIGHBOUR",
                postcode="SW2 5TN",
                address="14 Acacia Road",
                total_floor_area="85",
                inspection_date="2023-06-01",
            ),
        ]

        [result] = enrich_comparables(
            ppd, epc, config=JoinConfig(postcode_fallback=True)
        )

        assert result.match_quality == "exact_address"
        assert result.epc_lmk_key == "EXACT"

    def test_fallback_is_opt_in(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="NEIGHBOUR",
                postcode="SW2 5TN",
                address="14 Acacia Road",
                total_floor_area="85",
                inspection_date="2023-06-01",
            )
        ]

        [result] = enrich_comparables(ppd, epc)

        assert result.match_quality == "none"


class TestBatch:
    def test_preserves_ppd_order_and_handles_mixed_matches(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            ),
            _PPDRow(
                transaction_id="T2",
                price=600_000,
                transfer_date="2024-03-15",
                paon="14",
                street="Acacia Road",
                postcode="SW2 5TN",
            ),
            _PPDRow(
                transaction_id="T3",
                price=700_000,
                transfer_date="2024-04-01",
                paon="99",
                street="Other Road",
                postcode="E1 1AA",
            ),
        ]
        epc = [
            _EPCRow(
                lmk_key="EPC-12",
                postcode="SW2 5TN",
                address="12 Acacia Road",
                total_floor_area="80",
                inspection_date="2023-09-01",
            ),
            _EPCRow(
                lmk_key="EPC-14",
                postcode="SW2 5TN",
                address="14 Acacia Road",
                total_floor_area="90",
                inspection_date="2023-11-01",
            ),
        ]

        results = enrich_comparables(ppd, epc)

        assert [r.transaction_id for r in results] == ["T1", "T2", "T3"]
        assert results[0].match_quality == "exact_address"
        assert results[0].floor_area_sqm == 80.0
        assert results[1].match_quality == "exact_address"
        assert results[1].floor_area_sqm == 90.0
        assert results[2].match_quality == "none"
        assert results[2].floor_area_sqm is None

    def test_empty_inputs_return_empty_list(self) -> None:
        assert enrich_comparables([], []) == []
        assert enrich_comparables([], [_EPCRow(lmk_key="x", postcode=None, address=None)]) == []

    def test_empty_epc_returns_unenriched_rows(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        [result] = enrich_comparables(ppd, [])

        assert result.match_quality == "none"
        assert result.transaction_id == "T1"


class TestEPCAddressParsing:
    def test_handles_address_with_flat_prefix(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="Flat 3",
                street="Acacia Road",
                postcode="SW2 5TN",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="EPC-FLAT",
                postcode="SW2 5TN",
                address="Flat 3, Acacia Road",
                total_floor_area="55",
                inspection_date="2023-09-01",
            )
        ]

        [result] = enrich_comparables(ppd, epc)

        assert result.match_quality == "exact_address"
        assert result.floor_area_sqm == 55.0

    def test_ignores_case_and_whitespace_differences(self) -> None:
        ppd = [
            _PPDRow(
                transaction_id="T1",
                price=500_000,
                transfer_date="2024-03-01",
                paon="12a",
                street="acacia rd",
                postcode="sw2 5tn",
            )
        ]
        epc = [
            _EPCRow(
                lmk_key="EPC-1",
                postcode="SW2 5TN",
                address="12A  ACACIA ROAD",
                total_floor_area="85",
                inspection_date="2023-09-01",
            )
        ]

        [result] = enrich_comparables(ppd, epc)

        assert result.match_quality == "exact_address"
