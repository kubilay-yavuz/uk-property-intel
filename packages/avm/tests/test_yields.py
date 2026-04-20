"""Unit tests for :mod:`uk_property_avm.yields`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from uk_property_avm import YieldBreakdown, YieldInputs, compute_rental_yield
from uk_property_avm.models import ValuationEstimate


def _estimate(
    *,
    estimate_gbp: int = 400_000,
    basis: str = "postcode_type",
    confidence: str = "high",
    comparables: int = 25,
    postcode: str = "SW1A 1AA",
) -> ValuationEstimate:
    return ValuationEstimate(
        estimate_gbp=estimate_gbp,
        low_gbp=int(estimate_gbp * 0.9),
        high_gbp=int(estimate_gbp * 1.1),
        confidence=confidence,  # type: ignore[arg-type]
        comparables_used=comparables,
        basis=basis,  # type: ignore[arg-type]
        postcode=postcode,
    )


class TestYieldInputs:
    def test_rent_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            YieldInputs(monthly_rent_gbp=0)
        with pytest.raises(ValidationError):
            YieldInputs(monthly_rent_gbp=-10)

    def test_costs_pct_rejected_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            YieldInputs(monthly_rent_gbp=1500, costs_pct=-0.1)
        with pytest.raises(ValidationError):
            YieldInputs(monthly_rent_gbp=1500, costs_pct=1.0)

    def test_source_defaults_to_unknown(self) -> None:
        inputs = YieldInputs(monthly_rent_gbp=2000)
        assert inputs.rent_source == "unknown"


class TestComputeRentalYield:
    def test_gross_only_when_costs_pct_omitted(self) -> None:
        estimate = _estimate(estimate_gbp=400_000)
        inputs = YieldInputs(monthly_rent_gbp=1500, rent_source="listing")

        breakdown = compute_rental_yield(estimate, inputs)

        assert breakdown is not None
        assert breakdown.annual_rent_gbp == 18_000
        # 18000 / 400000 = 4.5%
        assert breakdown.gross_yield_pct == 4.5
        assert breakdown.net_yield_pct is None
        assert breakdown.costs_pct is None
        assert breakdown.rent_source == "listing"
        assert "scraped listing" in breakdown.methodology
        assert "gross only" in breakdown.methodology

    def test_net_yield_when_costs_pct_supplied(self) -> None:
        estimate = _estimate(estimate_gbp=400_000)
        inputs = YieldInputs(
            monthly_rent_gbp=1500,
            costs_pct=0.25,
            rent_source="voa_prms",
        )

        breakdown = compute_rental_yield(estimate, inputs)

        assert breakdown is not None
        # 4.5% x (1 - 0.25) = 3.375%
        assert breakdown.net_yield_pct == 3.375
        assert breakdown.costs_pct == 0.25
        assert breakdown.rent_source == "voa_prms"
        assert "VOA PRMS median" in breakdown.methodology
        assert "cost-adjusted" in breakdown.methodology

    def test_payback_years_is_reciprocal(self) -> None:
        estimate = _estimate(estimate_gbp=360_000)
        inputs = YieldInputs(monthly_rent_gbp=1500)  # £18k/yr
        breakdown = compute_rental_yield(estimate, inputs)

        assert breakdown is not None
        # 360_000 / 18_000 = 20 years
        assert breakdown.payback_years == 20.0

    def test_returns_none_for_zero_valuation(self) -> None:
        estimate = _estimate(
            estimate_gbp=0,
            basis="insufficient_data",
            confidence="low",
            comparables=0,
        )
        inputs = YieldInputs(monthly_rent_gbp=1500)

        assert compute_rental_yield(estimate, inputs) is None

    def test_high_rent_low_valuation_cap_warning_still_produces_result(self) -> None:
        estimate = _estimate(estimate_gbp=100_000)
        inputs = YieldInputs(monthly_rent_gbp=2_000)  # unrealistic for 100k home
        breakdown = compute_rental_yield(estimate, inputs)

        assert breakdown is not None
        assert breakdown.gross_yield_pct == 24.0

    def test_round_trip_json(self) -> None:
        estimate = _estimate(estimate_gbp=350_000)
        inputs = YieldInputs(
            monthly_rent_gbp=1_400,
            costs_pct=0.28,
            rent_source="user_override",
        )
        breakdown = compute_rental_yield(estimate, inputs)
        assert breakdown is not None
        json_payload = breakdown.model_dump(mode="json")
        reloaded = YieldBreakdown.model_validate(json_payload)
        assert reloaded == breakdown
