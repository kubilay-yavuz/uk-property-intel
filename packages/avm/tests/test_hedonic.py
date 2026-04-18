"""Tests for the log-price hedonic baseline.

The training pools are deliberately synthetic so we can reason about the
expected coefficient and prediction behaviour analytically. When we want a
realistic-looking market distribution we seed numpy to keep results stable.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from uk_property_avm import (
    EnrichedComparable,
    HedonicModel,
    HedonicTarget,
    estimate_value_hedonic,
    normalise_age_band,
    normalise_tenure,
)


def _synthetic_market(
    n: int,
    *,
    postcode: str = "SW2 5TN",
    property_type: str = "T",
    noise_sigma: float = 0.05,
    seed: int = 42,
) -> list[EnrichedComparable]:
    """Make ``n`` rows whose log-price follows a known hedonic formula.

    ``log_price = 10 + 0.8 * log_floor_area + ε``, ε ~ N(0, sigma²).

    That gives a floor-area elasticity of 0.8 at a baseline £22k for a
    1-sqm dwelling — numbers big enough that rounding noise is negligible,
    small enough that tests run fast.
    """

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


class TestHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("F", "FREEHOLD"),
            ("freehold", "FREEHOLD"),
            ("L", "LEASEHOLD"),
            ("Leasehold", "LEASEHOLD"),
            (None, "UNKNOWN"),
            ("", "UNKNOWN"),
            ("commonhold", "UNKNOWN"),
        ],
    )
    def test_normalise_tenure(self, raw: str | None, expected: str) -> None:
        assert normalise_tenure(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("England and Wales: 1900-1929", "1900_1929"),
            ("England and Wales: 1930-1949", "1930_1949"),
            ("Scotland: post-2002", "POST_2002"),
            ("Scotland: pre 1919", "PRE_1900"),
            (None, "UNKNOWN"),
            ("junk", "UNKNOWN"),
            ("England and Wales: 1996-2002", "1996_2002"),
            ("England and Wales: 2007-2011", "POST_2002"),
        ],
    )
    def test_normalise_age_band(self, raw: str | None, expected: str) -> None:
        assert normalise_age_band(raw) == expected


class TestHedonicFit:
    def test_fit_recovers_log_floor_area_elasticity(self) -> None:
        pool = _synthetic_market(200, noise_sigma=0.02)

        model = HedonicModel()
        model.fit(pool)

        assert model.is_fitted

        small = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=50)
        )
        big = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=100)
        )

        ratio = big.estimate_gbp / small.estimate_gbp
        # With beta ~= 0.8 on log_floor_area, doubling area -> 2^0.8 ~= 1.74x price.
        assert 1.65 <= ratio <= 1.85

    def test_prediction_is_monotonic_in_floor_area(self) -> None:
        pool = _synthetic_market(150, noise_sigma=0.02)
        model = HedonicModel()
        model.fit(pool)

        estimates = [
            model.predict(
                HedonicTarget(
                    postcode="SW2 5TN", property_type="T", floor_area_sqm=area
                )
            ).estimate_gbp
            for area in (50, 75, 100, 125, 150)
        ]
        assert estimates == sorted(estimates)

    def test_bands_straddle_point_estimate(self) -> None:
        pool = _synthetic_market(150, noise_sigma=0.1)
        model = HedonicModel()
        model.fit(pool)

        est = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80)
        )

        assert est.low_gbp <= est.estimate_gbp <= est.high_gbp
        assert est.methodology.startswith("hedonic-log-price")
        assert est.postcode == "SW2 5TN"

    def test_heterogeneous_pool_widens_band(self) -> None:
        tight = _synthetic_market(150, noise_sigma=0.02, seed=1)
        wide = _synthetic_market(150, noise_sigma=0.15, seed=1)

        est_tight = estimate_value_hedonic(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80),
            tight,
        )
        est_wide = estimate_value_hedonic(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80),
            wide,
        )

        tight_spread = est_tight.high_gbp - est_tight.low_gbp
        wide_spread = est_wide.high_gbp - est_wide.low_gbp
        assert wide_spread > tight_spread

    def test_missing_floor_area_is_imputed(self) -> None:
        pool = _synthetic_market(100, noise_sigma=0.05)
        model = HedonicModel()
        model.fit(pool)

        est = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=None)
        )

        assert est.estimate_gbp > 0
        assert est.methodology.startswith("hedonic-log-price")


class TestHedonicFallback:
    def test_thin_pool_falls_back_to_median_baseline(self) -> None:
        pool = _synthetic_market(5, noise_sigma=0.02)

        model = HedonicModel(min_rows_for_fit=20)
        model.fit(pool)

        assert not model.is_fitted

        est = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80)
        )

        assert est.methodology.endswith("degraded to median baseline)")
        assert est.comparables_used >= 1

    def test_empty_pool_returns_insufficient_data(self) -> None:
        model = HedonicModel()
        model.fit([])

        est = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80)
        )

        assert est.basis == "insufficient_data"
        assert est.estimate_gbp == 0
        assert est.confidence == "low"

    def test_pool_without_floor_area_falls_back(self) -> None:
        pool = [
            EnrichedComparable(
                transaction_id=f"T{i}",
                price=500_000,
                transfer_date="2024-03-01",
                property_type="T",
                tenure="F",
                postcode="SW2 5TN",
                floor_area_sqm=None,
                match_quality="none",
            )
            for i in range(30)
        ]

        est = estimate_value_hedonic(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80),
            pool,
        )
        assert "degraded to median baseline" in est.methodology


class TestConvenienceEntryPoint:
    def test_one_shot_fit_and_predict(self) -> None:
        pool = _synthetic_market(150, noise_sigma=0.02)

        est = estimate_value_hedonic(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=100),
            pool,
        )

        assert est.methodology.startswith("hedonic-log-price")
        assert est.estimate_gbp > 0
        assert est.low_gbp <= est.estimate_gbp <= est.high_gbp

    def test_output_is_pydantic_valuation_estimate(self) -> None:
        pool = _synthetic_market(100, noise_sigma=0.05)
        est = estimate_value_hedonic(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=80),
            pool,
        )
        dumped = est.model_dump(mode="json")
        assert set(dumped) >= {
            "estimate_gbp",
            "low_gbp",
            "high_gbp",
            "confidence",
            "basis",
            "methodology",
        }


class TestCoefficientInterpretability:
    def test_larger_area_raises_price_more_in_expensive_area(self) -> None:
        """Sanity: doubling floor area should raise the estimate in a plausible range.

        We don't pin the exact multiplier to a single number (OLS on noisy
        data has a confidence interval), but the ratio should land between
        1.5x and 2.0x for a 0.8 elasticity.
        """

        pool = _synthetic_market(300, noise_sigma=0.03, seed=7)
        model = HedonicModel()
        model.fit(pool)

        small = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=60)
        )
        big = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=120)
        )

        ratio = big.estimate_gbp / small.estimate_gbp
        # 2^0.8 ~= 1.74
        assert 1.6 <= ratio <= 1.9

    def test_unknown_feature_levels_do_not_crash_predict(self) -> None:
        """Predicting with a brand-new postcode area / age band should still work.

        OneHotEncoder is fit with ``handle_unknown='ignore'`` so we silently
        treat unseen levels as the reference category.
        """

        pool = _synthetic_market(80, noise_sigma=0.05)
        model = HedonicModel()
        model.fit(pool)

        est = model.predict(
            HedonicTarget(
                postcode="LS1 1AA",
                property_type="D",
                floor_area_sqm=120,
                tenure="Commonhold",
                age_band="some band we never saw",
            )
        )
        assert est.estimate_gbp > 0
        assert math.isfinite(est.estimate_gbp)
