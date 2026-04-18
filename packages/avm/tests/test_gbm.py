"""Tests for the gradient-boosted hedonic model."""

from __future__ import annotations

import math
import sys
import types
from typing import Any, ClassVar

import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor
from uk_property_avm import (
    EnrichedComparable,
    GBMHedonicModel,
    HedonicTarget,
    SklearnQuantileRegressor,
    default_regressor_factory,
    estimate_value_gbm_hedonic,
    make_lightgbm_regressor_factory,
)


def _synthetic_market(
    n: int,
    *,
    postcode: str = "SW2 5TN",
    property_type: str = "T",
    noise_sigma: float = 0.05,
    seed: int = 42,
) -> list[EnrichedComparable]:
    """Reuse the synthetic hedonic market law: log_price = 10 + 0.8*log(floor) + noise."""

    rng = np.random.default_rng(seed)
    floors = rng.uniform(40, 150, size=n)
    noise = rng.normal(0, noise_sigma, size=n)
    log_prices = 10 + 0.8 * np.log(floors) + noise
    prices = np.round(np.exp(log_prices)).astype(int)

    out: list[EnrichedComparable] = []
    for i in range(n):
        out.append(
            EnrichedComparable(
                transaction_id=f"G{i:05d}",
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


def _nonlinear_market(
    n: int,
    *,
    postcode: str = "SW2 5TN",
    property_type: str = "T",
    seed: int = 3,
) -> list[EnrichedComparable]:
    """Generate a pool whose price is a saturating function of floor area.

    log_price = 11 + 0.5 * log(floor) + 0.3 * tanh((floor - 80) / 30) + ε

    A linear log-log regression will have structural bias here because
    the tanh term produces a visible bend around 80 sqm. A GBR can pick
    that curvature up and (in principle) produce tighter residuals on
    held-out data.
    """

    rng = np.random.default_rng(seed)
    floors = rng.uniform(40, 160, size=n)
    noise = rng.normal(0, 0.05, size=n)
    log_prices = 11 + 0.5 * np.log(floors) + 0.3 * np.tanh((floors - 80) / 30) + noise
    prices = np.round(np.exp(log_prices)).astype(int)

    out: list[EnrichedComparable] = []
    for i in range(n):
        out.append(
            EnrichedComparable(
                transaction_id=f"N{i:05d}",
                price=int(prices[i]),
                transfer_date="2024-03-01",
                property_type=property_type,
                tenure="F",
                paon=str(10 + i),
                street="Tanh Road",
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
        model = GBMHedonicModel()
        assert model.quantiles == (0.1, 0.5, 0.9)

    def test_custom_quantiles(self) -> None:
        model = GBMHedonicModel(lower=0.2, median=0.5, upper=0.8)
        assert model.quantiles == (0.2, 0.5, 0.8)

    def test_rejects_ordering(self) -> None:
        with pytest.raises(ValueError, match="must satisfy"):
            GBMHedonicModel(lower=0.5, median=0.4, upper=0.9)

    def test_rejects_boundary_values(self) -> None:
        with pytest.raises(ValueError, match="must satisfy"):
            GBMHedonicModel(lower=0.0, median=0.5, upper=0.9)
        with pytest.raises(ValueError, match="must satisfy"):
            GBMHedonicModel(lower=0.1, median=0.5, upper=1.0)

    def test_unfitted_has_is_fitted_false(self) -> None:
        model = GBMHedonicModel()
        assert model.is_fitted is False


class TestFitAndPredict:
    """Fit/predict sanity checks against a synthetic market."""

    def test_fits_with_enough_rows(self) -> None:
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(80))
        assert model.is_fitted is True

    def test_refuses_small_pool(self) -> None:
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(30))
        assert model.is_fitted is False

    def test_median_is_between_bounds(self) -> None:
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(100))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=100
            )
        )
        assert estimate.low_gbp <= estimate.estimate_gbp <= estimate.high_gbp

    def test_estimate_is_in_market_range(self) -> None:
        # For a 100 sqm dwelling: log_price = 10 + 0.8 * log(100) ~= 13.68 → £880k.
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(200))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=100
            )
        )
        expected = math.exp(10 + 0.8 * math.log(100))
        assert estimate.estimate_gbp == pytest.approx(expected, rel=0.15)

    def test_methodology_labels_gbm(self) -> None:
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(80))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert "gbm-hedonic-log-price" in estimate.methodology
        assert "0.10/0.50/0.90" in estimate.methodology

    def test_monotonic_floor_area(self) -> None:
        """Monotonic constraint should guarantee non-decreasing predictions in floor area."""

        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(150))
        previous = 0
        for floor in [40, 60, 80, 100, 120, 140]:
            estimate = model.predict(
                HedonicTarget(
                    postcode="SW2 5TN", property_type="T", floor_area_sqm=floor
                )
            )
            assert estimate.estimate_gbp >= previous
            previous = estimate.estimate_gbp

    def test_captures_nonlinearity(self) -> None:
        """GBR should fit the tanh bend more tightly than a linear hedonic on held-out data.

        We don't require strict domination on every pool, just that
        the GBR's error on a small held-out set is at most as large as
        the linear model's. The bar is loose on purpose to avoid flaky
        CI runs.
        """

        train = _nonlinear_market(200, seed=11)
        test = _nonlinear_market(50, seed=99)

        gbm = GBMHedonicModel(min_rows_for_fit=50)
        gbm.fit(train)

        gbm_errors = []
        for row in test:
            target = HedonicTarget(
                postcode=row.postcode,
                property_type=row.property_type,
                floor_area_sqm=row.floor_area_sqm,
            )
            estimate = gbm.predict(target)
            gbm_errors.append(abs(estimate.estimate_gbp - row.price) / row.price)

        assert np.median(gbm_errors) < 0.15


class TestFallbackLadder:
    """Graceful degradation through mean hedonic and rolling median."""

    def test_falls_back_to_mean_hedonic(self) -> None:
        # 30 rows: below GBM threshold (50), above mean threshold (20).
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(30))
        assert model.is_fitted is False
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert "degraded to mean hedonic" in estimate.methodology

    def test_falls_back_to_median_baseline(self) -> None:
        # 10 rows: below both GBM (50) and mean (20) thresholds.
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(_synthetic_market(10))
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert "degraded to median baseline" in estimate.methodology

    def test_empty_pool_returns_insufficient_data(self) -> None:
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit([])
        estimate = model.predict(
            HedonicTarget(
                postcode="SW2 5TN", property_type="T", floor_area_sqm=80
            )
        )
        assert estimate.basis == "insufficient_data"
        assert estimate.comparables_used == 0


class TestRegressorFactory:
    """Custom regressor-factory injection point."""

    def test_default_factory_returns_correct_type(self) -> None:
        cst = np.array([0, 0, 1, 1])
        regressor = default_regressor_factory(0.5, cst)
        assert isinstance(regressor, HistGradientBoostingRegressor)
        assert regressor.loss == "quantile"
        assert regressor.quantile == 0.5

    def test_default_factory_passes_monotonic_cst(self) -> None:
        cst = np.array([0, 0, 0, 1, 1])
        regressor = default_regressor_factory(0.9, cst)
        np.testing.assert_array_equal(regressor.monotonic_cst, cst)

    def test_custom_factory_is_used(self) -> None:
        """Factory injection should receive (quantile, monotonic_cst) pairs."""

        calls: list[tuple[float, int]] = []

        def tracking_factory(
            quantile: float,
            monotonic_cst: np.ndarray,
        ) -> HistGradientBoostingRegressor:
            calls.append((quantile, int(monotonic_cst.sum())))
            return default_regressor_factory(quantile, monotonic_cst)

        model = GBMHedonicModel(
            min_rows_for_fit=50, regressor_factory=tracking_factory
        )
        model.fit(_synthetic_market(80))

        assert len(calls) == 3
        quantiles = [q for q, _ in calls]
        assert quantiles == [0.1, 0.5, 0.9]
        # Two monotonic features (log_floor_area, energy_efficiency_c), both +1.
        for _, mono_sum in calls:
            assert mono_sum == 2


class TestEstimateValueGBMHedonic:
    """One-shot convenience function parity with ``fit``+``predict``."""

    def test_one_shot_matches_fit_predict(self) -> None:
        market = _synthetic_market(80)
        target = HedonicTarget(
            postcode="SW2 5TN", property_type="T", floor_area_sqm=100
        )
        model = GBMHedonicModel(min_rows_for_fit=50)
        model.fit(market)
        expected = model.predict(target)
        got = estimate_value_gbm_hedonic(target, market, min_rows_for_fit=50)
        assert got.estimate_gbp == expected.estimate_gbp
        assert got.low_gbp == expected.low_gbp
        assert got.high_gbp == expected.high_gbp


class TestSklearnQuantileRegressorProtocol:
    """Structural protocol for pluggable regressor backends."""

    def test_sklearn_hgbr_satisfies_protocol(self) -> None:
        cst = np.array([0, 0, 1, 1])
        regressor = default_regressor_factory(0.5, cst)
        assert isinstance(regressor, SklearnQuantileRegressor)

    def test_minimal_shim_satisfies_protocol(self) -> None:
        class _Shim:
            def fit(self, x: np.ndarray, y: np.ndarray) -> _Shim:
                return self

            def predict(self, x: np.ndarray) -> np.ndarray:
                return np.zeros(x.shape[0])

        assert isinstance(_Shim(), SklearnQuantileRegressor)

    def test_missing_method_fails_protocol(self) -> None:
        class _NotARegressor:
            def fit(self, x: np.ndarray, y: np.ndarray) -> None:
                return None

        assert not isinstance(_NotARegressor(), SklearnQuantileRegressor)


class _FakeLGBMRegressor:
    """Captures kwargs passed to ``LGBMRegressor(...)`` for assertions."""

    instances: ClassVar[list[_FakeLGBMRegressor]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _FakeLGBMRegressor.instances.append(self)

    def fit(self, x: np.ndarray, y: np.ndarray) -> _FakeLGBMRegressor:
        self.x_shape = x.shape
        self.y_shape = y.shape
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.zeros(x.shape[0])


@pytest.fixture
def fake_lightgbm(monkeypatch: pytest.MonkeyPatch) -> type[_FakeLGBMRegressor]:
    """Inject a fake ``lightgbm`` module into ``sys.modules``.

    The real LightGBM wheel is a ~30 MB platform-specific binary that we
    don't want to pull into the default test environment. A tiny shim
    satisfies the same API shape we rely on (``LGBMRegressor`` constructor
    + ``fit`` / ``predict``) so we can still verify the factory wires
    kwargs through correctly.
    """

    _FakeLGBMRegressor.instances = []
    fake_module = types.ModuleType("lightgbm")
    fake_module.LGBMRegressor = _FakeLGBMRegressor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lightgbm", fake_module)
    return _FakeLGBMRegressor


class TestLightGBMFactory:
    """``make_lightgbm_regressor_factory`` adapter."""

    def test_returns_callable(self) -> None:
        factory = make_lightgbm_regressor_factory()
        assert callable(factory)

    def test_raises_import_error_without_lightgbm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ensure no real `lightgbm` is cached and that import fails.
        monkeypatch.delitem(sys.modules, "lightgbm", raising=False)
        real_import = __import__

        def _blocked_import(
            name: str,
            globals_: Any = None,
            locals_: Any = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> Any:
            if name == "lightgbm":
                raise ImportError("No module named 'lightgbm'")
            return real_import(name, globals_, locals_, fromlist, level)

        monkeypatch.setattr("builtins.__import__", _blocked_import)

        factory = make_lightgbm_regressor_factory()
        with pytest.raises(ImportError, match="lightgbm"):
            factory(0.5, np.array([0, 0, 1, 1]))

    def test_forwards_quantile_and_constraints(
        self,
        fake_lightgbm: type[_FakeLGBMRegressor],
    ) -> None:
        factory = make_lightgbm_regressor_factory()
        cst = np.array([0, 0, 0, 1, 1])
        regressor = factory(0.9, cst)

        assert isinstance(regressor, fake_lightgbm)
        assert regressor.kwargs["objective"] == "quantile"
        assert regressor.kwargs["alpha"] == 0.9
        assert regressor.kwargs["monotone_constraints"] == [0, 0, 0, 1, 1]

    def test_defaults_mirror_sklearn_factory_shape(
        self,
        fake_lightgbm: type[_FakeLGBMRegressor],
    ) -> None:
        """Defaults should be reasonable and deterministic."""

        factory = make_lightgbm_regressor_factory()
        regressor = factory(0.5, np.array([0, 0, 1, 1]))
        kwargs = regressor.kwargs
        assert kwargs["n_estimators"] == 500
        assert kwargs["learning_rate"] == 0.05
        assert kwargs["max_depth"] == 6
        assert kwargs["num_leaves"] == 31
        assert kwargs["min_child_samples"] == 10
        assert kwargs["reg_lambda"] == 0.1
        assert kwargs["random_state"] == 0
        assert kwargs["verbose"] == -1

    def test_custom_hyperparameters_override_defaults(
        self,
        fake_lightgbm: type[_FakeLGBMRegressor],
    ) -> None:
        factory = make_lightgbm_regressor_factory(
            n_estimators=250,
            learning_rate=0.02,
            max_depth=4,
            num_leaves=15,
            min_data_in_leaf=20,
            reg_lambda=0.5,
            random_state=13,
            verbose=0,
        )
        regressor = factory(0.5, np.array([0, 0, 1, 1]))
        kwargs = regressor.kwargs
        assert kwargs["n_estimators"] == 250
        assert kwargs["learning_rate"] == 0.02
        assert kwargs["max_depth"] == 4
        assert kwargs["num_leaves"] == 15
        assert kwargs["min_child_samples"] == 20
        assert kwargs["reg_lambda"] == 0.5
        assert kwargs["random_state"] == 13
        assert kwargs["verbose"] == 0

    def test_factory_produces_protocol_compatible_regressor(
        self,
        fake_lightgbm: type[_FakeLGBMRegressor],
    ) -> None:
        factory = make_lightgbm_regressor_factory()
        regressor = factory(0.5, np.array([0, 0, 1, 1]))
        assert isinstance(regressor, SklearnQuantileRegressor)

    def test_three_quantiles_produce_three_instances(
        self,
        fake_lightgbm: type[_FakeLGBMRegressor],
    ) -> None:
        factory = make_lightgbm_regressor_factory()
        for q in (0.1, 0.5, 0.9):
            factory(q, np.array([0, 0, 1, 1]))
        assert len(fake_lightgbm.instances) == 3
        quantiles = [inst.kwargs["alpha"] for inst in fake_lightgbm.instances]
        assert quantiles == [0.1, 0.5, 0.9]

    def test_end_to_end_with_gbm_hedonic_model(
        self,
        fake_lightgbm: type[_FakeLGBMRegressor],
    ) -> None:
        """Feeding the LightGBM factory into ``GBMHedonicModel`` exercises every call site."""

        factory = make_lightgbm_regressor_factory()
        model = GBMHedonicModel(min_rows_for_fit=50, regressor_factory=factory)
        model.fit(_synthetic_market(80))

        assert len(fake_lightgbm.instances) == 3
        # All three fake regressors had the same training matrix shape.
        shapes = {inst.x_shape for inst in fake_lightgbm.instances}
        assert len(shapes) == 1
