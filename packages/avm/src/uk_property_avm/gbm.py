"""Gradient-boosted hedonic model.

Upgrade path from the linear and quantile hedonic models. This variant
fits three gradient-boosted regression trees — one per quantile — on
the same feature matrix. Trees let us capture non-linearities that log-
linear regressions can't (price-per-sqm curves that bend around ~80 sqm,
diminishing returns on energy efficiency, locality x type interactions)
while preserving interpretability through monotonic constraints on the
features that must move prices in one direction.

Backend choice
--------------

The default backend is :class:`sklearn.ensemble.HistGradientBoostingRegressor`
because scikit-learn is already a dependency (no extra binary wheel),
it supports both ``loss='quantile'`` and ``monotonic_cst``, and our
pool sizes (50-5000 rows) don't reach the regime where LightGBM's
gradient-boosting primitives materially outpace sklearn's.

For callers who need a LightGBM backend — national training runs,
large overnight batch re-valuations, or teams standardised on LightGBM
for ops reasons — :func:`make_lightgbm_regressor_factory` returns a
drop-in factory compatible with ``GBMHedonicModel(regressor_factory=...)``
that has identical quantile + monotonic-constraint semantics. LightGBM
is not installed by default: the factory does a lazy import and raises
a clear :class:`ImportError` if it's missing, so the core package
stays lean.

The factory contract is structural: any callable returning an object
with ``.fit(X, y)`` / ``.predict(X) → np.ndarray`` that honours
``quantile`` loss and a ``monotonic_cst``-equivalent constraint
vector will slot in. See :class:`SklearnQuantileRegressor` for the
minimal protocol.

Monotonic constraints
---------------------

We impose ``monotonic_cst = +1`` on:

* ``log_floor_area`` — larger dwellings must not predict lower prices
  (all else equal). Without this, sparse high-end data can train the
  model to fit a downward curve at the tails.
* ``energy_efficiency_c`` — more efficient dwellings must not predict
  lower prices. UK buyers have historically paid a small but real
  premium for A/B EPC ratings, and we don't want the model to invert
  that.

Categorical (OHE) features have no monotonic constraint — each property
type / tenure / age band / postcode area is free to move in any
direction.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.preprocessing import OneHotEncoder

from uk_property_avm.baseline import _normalise_postcode
from uk_property_avm.hedonic import (
    HedonicModel,
    HedonicTarget,
    _build_training_frame,
    _design_row,
)
from uk_property_avm.models import ValuationEstimate

if TYPE_CHECKING:
    from collections.abc import Iterable

    from uk_property_avm.models import EnrichedComparable

__all__ = [
    "GBMHedonicModel",
    "RegressorFactory",
    "SklearnQuantileRegressor",
    "default_regressor_factory",
    "estimate_value_gbm_hedonic",
    "make_lightgbm_regressor_factory",
]


_MIN_ROWS_FOR_FIT: Final = 50
"""Minimum training rows before trees are trustable.

Higher than the quantile hedonic (30) because tree ensembles overfit
aggressively on small pools. Below 50 we route through the quantile
hedonic, which in turn routes through the mean hedonic and finally the
median baseline — three levels of graceful degradation.
"""


@runtime_checkable
class SklearnQuantileRegressor(Protocol):
    """Structural type every regressor produced by a factory must satisfy.

    The fit-then-predict contract is the lowest-common-denominator of
    :class:`sklearn.ensemble.HistGradientBoostingRegressor` and
    LightGBM's ``LGBMRegressor``: both return a trained regressor that
    consumes an ``(n, d)`` design matrix plus length-``n`` target
    vector, and predict returns a length-``n`` ``numpy.ndarray``.

    The protocol is deliberately minimal. Implementations may carry
    extra methods (``score``, ``staged_predict``, LightGBM's
    ``booster_``, etc.); we don't call them.
    """

    def fit(self, x: np.ndarray, y: np.ndarray) -> Any:
        ...

    def predict(self, x: np.ndarray) -> np.ndarray:
        ...


RegressorFactory = Callable[[float, np.ndarray], SklearnQuantileRegressor]
"""Signature for a pluggable regressor factory.

Arguments are ``(quantile, monotonic_cst)``. The callable must return
an *unfit* regressor satisfying :class:`SklearnQuantileRegressor` — the
``GBMHedonicModel`` will call ``.fit(x, y)`` itself.

The default factory (:func:`default_regressor_factory`) returns
scikit-learn's :class:`HistGradientBoostingRegressor`;
:func:`make_lightgbm_regressor_factory` returns an equivalent factory
backed by LightGBM. Custom factories can plug in XGBoost, CatBoost, or
any sklearn-compatible quantile-regressor with monotonic-constraint
support without modifying this module.
"""


def default_regressor_factory(
    quantile: float,
    monotonic_cst: np.ndarray,
) -> HistGradientBoostingRegressor:
    """Default factory using scikit-learn's histogram-based GBR.

    Tuned for pool sizes of 30-5000 rows:

    * ``max_iter=150`` — enough boosting rounds to capture meaningful
      curvature without overfitting on small pools.
    * ``learning_rate=0.05`` — a safely conservative step size for
      noisy property data.
    * ``max_depth=6`` — deep enough to pick up location x type
      interactions, shallow enough to avoid the leaf-count explosion
      that kills generalisation on ~100-row training sets.
    * ``l2_regularization=0.1`` — small but non-zero; mostly a safety
      net for degenerate leaves.
    """

    return HistGradientBoostingRegressor(
        loss="quantile",
        quantile=quantile,
        max_iter=150,
        learning_rate=0.05,
        max_depth=6,
        min_samples_leaf=10,
        l2_regularization=0.1,
        monotonic_cst=monotonic_cst,
        random_state=0,
    )


@dataclass
class _GBMFit:
    """Container for fitted GBR pieces + diagnostic summaries."""

    regressors: dict[float, SklearnQuantileRegressor]
    encoder: OneHotEncoder
    categorical_cols: list[str]
    continuous_cols: list[str]
    continuous_means: dict[str, float]
    training_rows: int
    monotonic_cst: np.ndarray


class GBMHedonicModel:
    """Gradient-boosted quantile regression over the hedonic features.

    Usage::

        model = GBMHedonicModel(lower=0.1, upper=0.9)
        model.fit(enriched_rows)
        estimate = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=82)
        )

    The instance keeps its own :class:`HedonicModel` as an inner
    fallback. When the GBM fit degrades, predictions silently route
    through the linear hedonic → median baseline ladder, with the
    ``methodology`` field on the returned estimate recording which
    tier fired.
    """

    def __init__(
        self,
        *,
        lower: float = 0.1,
        upper: float = 0.9,
        median: float = 0.5,
        min_rows_for_fit: int = _MIN_ROWS_FOR_FIT,
        regressor_factory: RegressorFactory = default_regressor_factory,
    ) -> None:
        if not (0.0 < lower < median < upper < 1.0):
            msg = (
                "Quantile triple must satisfy 0 < lower < median < upper < 1; "
                f"got lower={lower}, median={median}, upper={upper}"
            )
            raise ValueError(msg)
        self.lower = lower
        self.median = median
        self.upper = upper
        self.min_rows_for_fit = min_rows_for_fit
        self.regressor_factory = regressor_factory
        self._fit: _GBMFit | None = None
        self._hedonic_fallback: HedonicModel = HedonicModel(
            min_rows_for_fit=min(min_rows_for_fit, 20)
        )

    @property
    def is_fitted(self) -> bool:
        """Has :meth:`fit` produced a usable GBM fit?"""

        return self._fit is not None

    @property
    def quantiles(self) -> tuple[float, float, float]:
        """The (lower, median, upper) triple in use."""

        return self.lower, self.median, self.upper

    def fit(self, comparables: Iterable[EnrichedComparable]) -> None:
        """Fit three gradient-boosted quantile regressors on ``comparables``.

        Always fits the fallback :class:`HedonicModel` too, so even
        when the GBM fit is skipped ``predict`` can still return a
        usable estimate.
        """

        pool = list(comparables)
        self._hedonic_fallback.fit(pool)

        frame = _build_training_frame(pool)
        if frame is None or len(frame) < self.min_rows_for_fit:
            self._fit = None
            return

        categorical_cols = ["property_type", "tenure", "postcode_area", "age_band"]
        continuous_cols = ["log_floor_area", "energy_efficiency_c"]
        continuous_means = {col: float(frame[col].mean()) for col in continuous_cols}

        encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        encoder.fit(frame[categorical_cols])
        x_cat = encoder.transform(frame[categorical_cols])
        x_cont = frame[continuous_cols].to_numpy()
        x = np.hstack([x_cat, x_cont])
        y = frame["log_price"].to_numpy()

        n_cat_cols = x_cat.shape[1]
        monotonic_cst = np.concatenate(
            [
                np.zeros(n_cat_cols, dtype=int),
                np.array([1, 1], dtype=int),
            ]
        )

        regressors: dict[float, SklearnQuantileRegressor] = {}
        for q in (self.lower, self.median, self.upper):
            regressor = self.regressor_factory(q, monotonic_cst)
            regressor.fit(x, y)
            regressors[q] = regressor

        self._fit = _GBMFit(
            regressors=regressors,
            encoder=encoder,
            categorical_cols=categorical_cols,
            continuous_cols=continuous_cols,
            continuous_means=continuous_means,
            training_rows=len(frame),
            monotonic_cst=monotonic_cst,
        )

    def predict(self, target: HedonicTarget) -> ValuationEstimate:
        """Return a :class:`ValuationEstimate` for ``target``.

        Degradation ladder:

        1. Gradient-boosted quantile hedonic (when fitted).
        2. Mean hedonic with IQR residual bands (when the linear fit
           was produced).
        3. Rolling-median baseline.

        The ``methodology`` field on the returned estimate records
        which tier fired, so downstream consumers can surface the
        degradation to a human reviewer.
        """

        target_pc = _normalise_postcode(target.postcode)
        if self._fit is None:
            return self._predict_via_fallback(target)

        fit = self._fit
        x = _design_row(
            target,
            target_pc,
            fit.encoder,
            fit.categorical_cols,
            fit.continuous_cols,
            fit.continuous_means,
        )
        log_predictions = {
            q: float(regressor.predict(x)[0]) for q, regressor in fit.regressors.items()
        }
        ordered = sorted(log_predictions.values())
        low_log, mid_log, high_log = ordered[0], ordered[1], ordered[2]
        low = math.exp(low_log)
        median_estimate = math.exp(mid_log)
        high = math.exp(high_log)

        basis = _basis_from_pool_size(fit.training_rows)
        confidence = _confidence_from_pool_size(fit.training_rows)

        return ValuationEstimate(
            estimate_gbp=round(median_estimate),
            low_gbp=round(low),
            high_gbp=round(high),
            confidence=confidence,
            comparables_used=fit.training_rows,
            basis=basis,
            postcode=target_pc,
            property_type=target.property_type,
            methodology=(
                f"gbm-hedonic-log-price (PPD+EPC, "
                f"q={self.lower:.2f}/{self.median:.2f}/{self.upper:.2f})"
            ),
        )

    def _predict_via_fallback(self, target: HedonicTarget) -> ValuationEstimate:
        """Route through the mean hedonic (which itself falls back to median)."""

        fallback = self._hedonic_fallback.predict(target)
        if self._hedonic_fallback.is_fitted:
            return fallback.model_copy(
                update={
                    "methodology": (
                        "gbm-hedonic-log-price (PPD+EPC, degraded to mean hedonic)"
                    )
                }
            )
        return fallback.model_copy(
            update={
                "methodology": (
                    "gbm-hedonic-log-price (PPD+EPC, degraded to median baseline)"
                )
            }
        )


def estimate_value_gbm_hedonic(
    target: HedonicTarget,
    comparables: Iterable[EnrichedComparable],
    *,
    lower: float = 0.1,
    upper: float = 0.9,
    median: float = 0.5,
    min_rows_for_fit: int = _MIN_ROWS_FOR_FIT,
    regressor_factory: RegressorFactory = default_regressor_factory,
) -> ValuationEstimate:
    """One-shot convenience: fit on ``comparables`` then predict ``target``."""

    model = GBMHedonicModel(
        lower=lower,
        upper=upper,
        median=median,
        min_rows_for_fit=min_rows_for_fit,
        regressor_factory=regressor_factory,
    )
    model.fit(comparables)
    return model.predict(target)


def _basis_from_pool_size(n: int) -> str:
    if n >= 100:
        return "postcode_type"
    if n >= 50:
        return "postcode_area"
    if n >= 25:
        return "area_type"
    return "area_only"


def _confidence_from_pool_size(n: int) -> str:
    if n >= 50:
        return "high"
    if n >= 20:
        return "medium"
    return "low"


def make_lightgbm_regressor_factory(
    *,
    n_estimators: int = 500,
    learning_rate: float = 0.05,
    max_depth: int = 6,
    num_leaves: int = 31,
    min_data_in_leaf: int = 10,
    reg_lambda: float = 0.1,
    random_state: int = 0,
    verbose: int = -1,
) -> RegressorFactory:
    """Return a :data:`RegressorFactory` backed by LightGBM's ``LGBMRegressor``.

    LightGBM is an *optional* dependency — the factory lazy-imports it
    on each call, so ``uk-property-avm`` itself stays lean. Install it
    explicitly when you need this backend::

        pip install lightgbm

    Defaults mirror :func:`default_regressor_factory` where the two
    libraries overlap (learning rate, depth, leaf minimum,
    L2 regularisation). ``n_estimators=500`` is higher than
    sklearn's default ``max_iter=150`` because LightGBM's per-round
    gradient estimate is noisier on small pools — the model usually
    stabilises somewhere between rounds 200 and 400.

    Monotonicity is honoured: the ``monotonic_cst`` vector passed to
    the factory is forwarded to LightGBM's ``monotone_constraints``
    kwarg unchanged, so ``+1`` / ``-1`` / ``0`` carry the same meaning
    across both backends.

    Usage::

        from uk_property_avm import GBMHedonicModel, make_lightgbm_regressor_factory

        factory = make_lightgbm_regressor_factory(n_estimators=300)
        model = GBMHedonicModel(regressor_factory=factory)
        model.fit(enriched_rows)

    Raises
    ------
    ImportError
        If ``lightgbm`` isn't installed. The error is only raised at
        call time — importing ``uk_property_avm`` never requires
        LightGBM.
    """

    def factory(quantile: float, monotonic_cst: np.ndarray) -> SklearnQuantileRegressor:
        try:
            from lightgbm import LGBMRegressor
        except ImportError as exc:
            msg = (
                "LightGBM backend requested but `lightgbm` is not installed. "
                "Install it with `pip install lightgbm` and retry, or swap "
                "in the default sklearn factory."
            )
            raise ImportError(msg) from exc
        regressor: SklearnQuantileRegressor = LGBMRegressor(
            objective="quantile",
            alpha=quantile,
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            num_leaves=num_leaves,
            min_child_samples=min_data_in_leaf,
            reg_lambda=reg_lambda,
            monotone_constraints=monotonic_cst.tolist(),
            random_state=random_state,
            verbose=verbose,
        )
        return regressor

    return factory
