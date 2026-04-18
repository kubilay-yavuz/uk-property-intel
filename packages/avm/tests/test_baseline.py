"""Tests for the rolling-median AVM baseline.

We avoid relying on randomness and instead hand in hand-crafted comparable
pools that are easy to reason about: the median and 25/75 percentiles are
computed by pen-and-paper in the fixture setup.
"""

from __future__ import annotations

import pytest
from uk_property_avm import Comparable, ValuationEstimate, comparables_from_ppd, estimate_value


def _cmp(
    idx: int,
    *,
    price: int,
    postcode: str,
    ptype: str | None = "T",
) -> Comparable:
    return Comparable(
        transaction_id=f"T{idx:05d}",
        price=price,
        transfer_date="2024-03-01",
        property_type=ptype,
        postcode=postcode,
    )


class TestEstimateValue:
    def test_uses_same_postcode_same_type_when_enough_data(self) -> None:
        pool = [
            _cmp(i, price=500_000 + i * 10_000, postcode="SW2 5TN", ptype="T")
            for i in range(10)
        ]
        pool.append(_cmp(100, price=2_000_000, postcode="SW19 1AA", ptype="F"))

        est = estimate_value("SW2 5TN", pool, property_type="T")

        assert est.basis == "postcode_type"
        assert est.comparables_used == 10
        assert est.confidence == "high"
        assert 500_000 <= est.estimate_gbp <= 590_000
        assert est.low_gbp <= est.estimate_gbp <= est.high_gbp
        assert est.postcode == "SW2 5TN"

    def test_falls_back_to_postcode_when_type_thin(self) -> None:
        pool = [_cmp(i, price=450_000 + i * 5_000, postcode="E8 1AB", ptype="F") for i in range(6)]
        pool.append(_cmp(7, price=900_000, postcode="E8 1AB", ptype="T"))

        est = estimate_value("E8 1AB", pool, property_type="T")

        assert est.basis == "postcode_area"
        assert est.comparables_used == 7
        assert est.postcode == "E8 1AB"

    def test_falls_back_to_area_type_for_unknown_postcode(self) -> None:
        pool = [_cmp(i, price=700_000 + i * 20_000, postcode="SW2 5AB", ptype="F") for i in range(4)]
        pool.append(_cmp(10, price=2_000_000, postcode="N1 1AA", ptype="D"))

        est = estimate_value("SW2 5ZZ", pool, property_type="F")

        assert est.basis == "area_type"
        assert est.comparables_used == 4

    def test_falls_back_to_area_only_when_type_missing(self) -> None:
        pool = [_cmp(i, price=800_000 + i * 1000, postcode="N1 1AA", ptype="F") for i in range(3)]
        pool.append(_cmp(4, price=3_000_000, postcode="N1 2BB", ptype="D"))

        est = estimate_value("N1 1ZZ", pool, property_type="T")

        assert est.basis == "area_only"
        assert est.comparables_used >= 3

    def test_falls_back_to_national_when_area_unknown(self) -> None:
        pool = [_cmp(i, price=200_000 + i * 10_000, postcode="NE1 1AA") for i in range(5)]

        est = estimate_value("LS1 1AA", pool, property_type=None)

        assert est.basis == "national"
        assert est.comparables_used == 5
        assert est.confidence == "low"

    def test_insufficient_data_returns_zeros(self) -> None:
        est = estimate_value("SW2 5TN", [], property_type="T")

        assert est.basis == "insufficient_data"
        assert est.estimate_gbp == 0
        assert est.low_gbp == 0
        assert est.high_gbp == 0
        assert est.confidence == "low"
        assert est.comparables_used == 0

    def test_band_is_iqr(self) -> None:
        prices = [100_000, 200_000, 300_000, 400_000, 500_000]
        pool = [
            _cmp(i, price=p, postcode="E1 1AA", ptype="F") for i, p in enumerate(prices)
        ]

        est = estimate_value("E1 1AA", pool, property_type="F")

        assert est.basis == "postcode_type"
        assert est.estimate_gbp == 300_000
        assert est.low_gbp == 200_000
        assert est.high_gbp == 400_000

    def test_rejects_bad_postcode(self) -> None:
        with pytest.raises(ValueError, match="Invalid UK postcode"):
            estimate_value("not-a-postcode", [])

    def test_postcode_is_normalised_in_output(self) -> None:
        pool = [_cmp(i, price=1_000_000, postcode="EC1V 3AP", ptype="F") for i in range(5)]
        est = estimate_value("ec1v3ap", pool, property_type="F")

        assert est.postcode == "EC1V 3AP"

    def test_output_is_pydantic_model(self) -> None:
        est = estimate_value("SW2 5TN", [_cmp(1, price=1, postcode="SW2 5TN")])
        assert isinstance(est, ValuationEstimate)
        dumped = est.model_dump(mode="json")
        assert set(dumped) >= {
            "estimate_gbp",
            "low_gbp",
            "high_gbp",
            "confidence",
            "comparables_used",
            "basis",
            "postcode",
            "methodology",
        }


class TestComparablesFromPPD:
    def test_maps_price_paid_records(self) -> None:
        class PPD:
            def __init__(
                self,
                *,
                transaction_id: str,
                price: int,
                transfer_date: str,
                property_type: str,
                postcode: str,
            ) -> None:
                self.transaction_id = transaction_id
                self.price = price
                self.transfer_date = transfer_date
                self.property_type = property_type
                self.postcode = postcode
                self.tenure = "F"
                self.paon = "12"
                self.street = "ACACIA AVENUE"

        records = [
            PPD(
                transaction_id="abc-1",
                price=500_000,
                transfer_date="2024-03-01",
                property_type="T",
                postcode="SW2 5TN",
            ),
            PPD(
                transaction_id="abc-2",
                price=450_000,
                transfer_date="2022-06-01",
                property_type="T",
                postcode="SW2 5TN",
            ),
        ]

        out = comparables_from_ppd(records)

        assert len(out) == 2
        assert out[0].price == 500_000
        assert out[0].postcode == "SW2 5TN"
        assert out[1].transfer_date == "2022-06-01"

    def test_skips_zero_price(self) -> None:
        class PPD:
            transaction_id = "z"
            price = 0
            transfer_date = "2024-01-01"
            property_type = "T"
            postcode = "SW2 5TN"
            tenure = None
            paon = None
            street = None

        assert comparables_from_ppd([PPD()]) == []

    def test_min_transfer_date_filters(self) -> None:
        class PPD:
            def __init__(self, *, transfer_date: str) -> None:
                self.transaction_id = f"t-{transfer_date}"
                self.price = 100_000
                self.transfer_date = transfer_date
                self.property_type = "T"
                self.postcode = "SW2 5TN"
                self.tenure = None
                self.paon = None
                self.street = None

        records = [PPD(transfer_date="2020-01-01"), PPD(transfer_date="2025-01-01")]

        out = comparables_from_ppd(records, min_transfer_date="2023-01-01")

        assert len(out) == 1
        assert out[0].transfer_date == "2025-01-01"

    def test_rejects_bad_min_date(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            comparables_from_ppd([], min_transfer_date="not-a-date")
