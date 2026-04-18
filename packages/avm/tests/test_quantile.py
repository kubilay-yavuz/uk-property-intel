"""Tests for the quantile hedonic model."""

from __future__ import annotations

import math

import numpy as np
import pytest
from uk_property_avm import (
    EnrichedComparable,
    HedonicTarget,
    QuantileHedonicModel,
    estimate_value_quantile_hedonic,
)


def _synthetic_market(
    n: int,
    *,
    postcode: str = "SW2 5TN",
    property_type: str = "T",
    noise_sigma: float = 0.05,
    seed: int = 42,
) -> list[EnrichedComparable]:
    """Same synthetic generator used in :mod:`test_hedonic`, pasted here to keep
    the quantile tests decoupled from the linear hedonic test module."""

    rng = np.random.default_rng(seed)
    floors = rng.uniform(40, 150, size=n)
    noise = rng.normal(0, noise_sigma, size=n)
    log_prices = 10 + 0.8 * np.log(floors) + noise
    prices = np.round(np.exp(log_prices)).astype(int)

    out: list[EnrichedComparable] = []
    for i in range(n):
        out.append(
            EnrichedComparable(
                transaction_id=f"T{i:05d}",
                price=int(prices[i]),
                transfer_date="2024-03-01",
                property_type=property_type,
                tenure="F",
                paon=str(10 + i),
                street="Main Road",
                postcode=postcode,
                floor_area_sqm=float(floors[i]),
                energy_rating="C",
                energy_efficiency=70,
                built_form="Mid-Terrace",
                construction_age_band="England and Wales: 1900-1929",
                epc_property_type="House",
                match_quality="exact_address",
            )
        )
    return out


def _asymmetric_market(
    n: int,
    *,
    postcode: str = "SW2 5TN",
    property_type: str = "T",
    seed: int = 7,
) -> list[EnrichedComparable]:
    """Generate a pool with a clearly skewed noise distribution.

    We want ``|upper_quantile - median|`` to be strictly greater than
    ``|median - lower_quantile|`` so the asymmetric-band property is
    actually testable.
    """

    rng = np.random.default_rng(seed)
    floors = rng.uniform(40, 150, size=n)
    # Lognormal noise: symmetric in log-space but heavy right tail in £.
    noise = rng.standard_exponential(size=n) * 0.2 - 0.1
    log_prices = 10 + 0.8 * np.log(floors) + noise
    prices = np.round(np.exp(log_prices)).astype(int)

    out: list[EnrichedComparable] = []
    for i in range(n):
        out.append(
            EnrichedComparable(
                transaction_id=f"A{i:05d}",
                price=int(prices[i]),
                transfer_date="2024-03-01",
                property_type=property_type,
                tenure="F",
                paon=str(10 + i),
                street="Skew Road",
                postcode=postcode,
                floor_area_sqm=float(floors[i]),
                energy_rating="C",
                energy_efficiency=70,
                built_form="Mid-Terrace",
                construction_age_band="England and Wales: 1900-1929",
                epc_property_type="House",
                match_quality="exact_address",
            )
        )
    return out


class TestConstruction:
    """Quantile triple validation + defaults."""

    def test_default_quantiles(self) -> None:
        model = QuantileHedonicModel()
        assert model.quantiles == (0.1, 0.5, 0.9)

    def test_custom_quantiles(self) -> None:
        model = QuantileHedonicModel(lower=0.2, median=0.5, upper=0.8)
        assert model.quantiles == (0.2, 0.5, 0.8)

    def test_rejects_ordering(self) -> None:
        with pytest.raises(ValueError, match="must satisfy"):
            QuantileHedonicModel(lower=0.5, median=0.4, upper=0.9)

    def test_rejects_boundary_values(self) -> None:
        with pytest.raises(ValueError, match="must satisfy"):
            QuantileHedonicModel(lower=0.0, median=0.5, upper=0.9)
        with pytest.raises(ValueError, match="must satisfy"):
            QuantileHedonicModel(lower=0.1, median=0.5, upper=1.0)

    def test_unfitted_has_is_fitted_false(self) -> None:
        model = QuantileHedonicModel()
        assert model.is_fitted is False


class TestFitAndPredict:
    """Fit/predict sanity checks against a synthetic market with a known law."""

    def test_fits_with_enough_rows(self) -> None:
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(60))
        assert model.is_fitted is True

    def test_refuses_small_pool(self) -> None:
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(10))
        assert model.is_fitted is False

    def test_median_is_between_bounds(self) -> None:
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(80))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=100
            )
        )
        assert estimate.low_gbp <= estimate.estimate_gbp <= estimate.high_gbp

    def test_estimate_is_in_market_range(self) -> None:
        # For a 100 sqm dwelling under the law
        # log_price = 10 + 0.8 * log(100) = 10 + 3.684 ~ 13.684 → £880k.
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(150))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=100
            )
        )
        expected = math.exp(10 + 0.8 * math.log(100))
        assert estimate.estimate_gbp == pytest.approx(expected, rel=0.1)

    def test_methodology_contains_quantiles(self) -> None:
        model = QuantileHedonicModel(lower=0.1, upper=0.9, min_rows_for_fit=30)
        model.fit(_synthetic_market(60))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert "quantile-hedonic-log-price" in estimate.methodology
        assert "0.10/0.50/0.90" in estimate.methodology

    def test_floor_area_monotone(self) -> None:
        # Larger dwellings predict higher prices (under constant location
        # + type).
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(100))
        small = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=50
            )
        )
        big = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=140
            )
        )
        assert big.estimate_gbp > small.estimate_gbp
        # Bands move the same direction.
        assert big.low_gbp > small.low_gbp
        assert big.high_gbp > small.high_gbp

    def test_band_widens_with_noise(self) -> None:
        quiet = QuantileHedonicModel(min_rows_for_fit=30)
        quiet.fit(_synthetic_market(100, noise_sigma=0.01, seed=1))
        loud = QuantileHedonicModel(min_rows_for_fit=30)
        loud.fit(_synthetic_market(100, noise_sigma=0.30, seed=1))

        target = HedonicTarget(
            postcode="SW2 5TN", property_type="T", floor_area_sqm=100
        )
        quiet_est = quiet.predict(target)
        loud_est = loud.predict(target)
        quiet_spread = quiet_est.high_gbp - quiet_est.low_gbp
        loud_spread = loud_est.high_gbp - loud_est.low_gbp
        assert loud_spread > quiet_spread


class TestAsymmetricBands:
    """With a skewed noise distribution, the upper band should be further
    from the median than the lower band."""

    def test_right_tail_is_wider(self) -> None:
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_asymmetric_market(200))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=100
            )
        )
        upper_gap = estimate.high_gbp - estimate.estimate_gbp
        lower_gap = estimate.estimate_gbp - estimate.low_gbp
        assert upper_gap > lower_gap


class TestFallbackLadder:
    """Graceful degradation through mean hedonic and rolling median."""

    def test_falls_back_to_mean_hedonic_when_quantile_pool_thin(self) -> None:
        # 25 rows: below our quantile threshold (30), above mean threshold (20).
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(25))
        assert model.is_fitted is False
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=90
            )
        )
        assert "degraded to mean hedonic" in estimate.methodology

    def test_falls_back_to_median_when_even_mean_thin(self) -> None:
        # 10 rows: below both quantile (30) and mean (20) thresholds.
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(_synthetic_market(10))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert "degraded to median baseline" in estimate.methodology

    def test_empty_pool_returns_insufficient_data(self) -> None:
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit([])
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert estimate.basis == "insufficient_data"
        assert estimate.comparables_used == 0


class TestEstimateValueQuantileHedonic:
    """One-shot convenience function."""

    def test_one_shot_matches_fit_predict(self) -> None:
        market = _synthetic_market(80)
        target = HedonicTarget(
            postcode="SW2 5TN", property_type="T", floor_area_sqm=100
        )
        model = QuantileHedonicModel(min_rows_for_fit=30)
        model.fit(market)
        expected = model.predict(target)
        got = estimate_value_quantile_hedonic(
            target, market, min_rows_for_fit=30
        )
        assert got.estimate_gbp == expected.estimate_gbp
        assert got.low_gbp == expected.low_gbp
        assert got.high_gbp == expected.high_gbp
