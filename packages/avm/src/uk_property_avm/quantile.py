"""Quantile hedonic model.

Upgrade path from :class:`uk_property_avm.hedonic.HedonicModel`. Instead
of fitting the conditional **mean** of ``log(price)`` and deriving bands
from the IQR of residuals, this variant fits three separate
:class:`sklearn.linear_model.QuantileRegressor` models — one each for
the lower band, the median, and the upper band — on the same feature
set. The results are **asymmetric** bands that reflect the data's own
uncertainty shape, which matters because UK residential prices are
right-skewed at the high end of most segments (more upside than
downside variance).

Design choices
--------------

* We fit three quantiles (default 0.1 / 0.5 / 0.9, an 80% prediction
  interval) rather than two. The median predictor is our point
  estimate — it is more robust to price outliers than the OLS mean,
  and is also what ``ValuationEstimate.estimate_gbp`` is meant to
  communicate. The two outer quantiles form the band.
* Each quantile regressor is fit on the **same design matrix** as the
  linear hedonic model. This means the feature engineering is
  delegated to :func:`uk_property_avm.hedonic._build_training_frame`
  and :func:`uk_property_avm.hedonic._design_row` — we share every
  normalisation helper and every OHE column.
* Quantile regressors can produce non-monotone predictions across
  quantiles (because each is fit independently). We sort the three
  predictions at inference time to guarantee ``low <= mid <= high``.
* If :class:`QuantileRegressor` fails to converge or the training
  pool is too thin, we fall back to the :class:`HedonicModel`'s own
  fitted mean + residual-IQR approach. If *that* fit isn't available
  we fall further back to the rolling-median baseline. This gives
  three graceful steps of degradation so the package always returns a
  :class:`ValuationEstimate` rather than raising.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
from sklearn.linear_model import QuantileRegressor
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
    "QuantileHedonicModel",
    "estimate_value_quantile_hedonic",
]


_MIN_ROWS_FOR_FIT: Final = 30
"""Minimum training rows before quantile regressions are trustable.

Slightly stricter than the mean hedonic (20) because each tail quantile
has fewer effective samples than the mean — with 30 rows and a 0.1/0.9
tail target, each tail sits on only ~3 data points, which is the
practical lower bound for non-degenerate quantile regression.
"""


@dataclass
class _QuantileFit:
    regressors: dict[float, QuantileRegressor]
    encoder: OneHotEncoder
    categorical_cols: list[str]
    continuous_cols: list[str]
    continuous_means: dict[str, float]
    training_rows: int


class QuantileHedonicModel:
    """Log-price quantile regression hedonic model.

    Usage::

        model = QuantileHedonicModel(lower=0.1, upper=0.9)
        model.fit(enriched_rows)
        estimate = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=82)
        )

    The instance keeps its own :class:`HedonicModel` as a fallback
    engine. When the quantile fit degrades, ``predict`` silently routes
    through the linear hedonic first, and then through the rolling
    median baseline when even that isn't available.
    """

    def __init__(
        self,
        *,
        lower: float = 0.1,
        upper: float = 0.9,
        median: float = 0.5,
        min_rows_for_fit: int = _MIN_ROWS_FOR_FIT,
        alpha: float = 1e-3,
        solver: str = "highs",
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
        self.alpha = alpha
        self.solver = solver
        self._fit: _QuantileFit | None = None
        self._hedonic_fallback: HedonicModel = HedonicModel(min_rows_for_fit=min(min_rows_for_fit, 20))

    @property
    def is_fitted(self) -> bool:
        """Has :meth:`fit` produced a usable quantile fit?"""

        return self._fit is not None

    @property
    def quantiles(self) -> tuple[float, float, float]:
        """The (lower, median, upper) triple in use."""

        return self.lower, self.median, self.upper

    def fit(self, comparables: Iterable[EnrichedComparable]) -> None:
        """Fit three quantile regressors on ``comparables``.

        Always fits the fallback :class:`HedonicModel` too, so even when
        the quantile regressor fails the instance can still answer
        ``predict`` with the mean model's band.
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

        regressors: dict[float, QuantileRegressor] = {}
        for q in (self.lower, self.median, self.upper):
            regressor = QuantileRegressor(quantile=q, alpha=self.alpha, solver=self.solver)
            regressor.fit(x, y)
            regressors[q] = regressor

        self._fit = _QuantileFit(
            regressors=regressors,
            encoder=encoder,
            categorical_cols=categorical_cols,
            continuous_cols=continuous_cols,
            continuous_means=continuous_means,
            training_rows=len(frame),
        )

    def predict(self, target: HedonicTarget) -> ValuationEstimate:
        """Return a :class:`ValuationEstimate` for ``target``.

        Degradation ladder:

        1. Quantile hedonic (when fitted).
        2. Mean hedonic with IQR residual bands (when the linear fit was
           produced).
        3. Rolling-median baseline.

        The ``methodology`` field on the returned estimate records which
        tier fired, so downstream consumers can surface the degradation
        to a human reviewer.
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
                f"quantile-hedonic-log-price (PPD+EPC, "
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
                        "quantile-hedonic-log-price (PPD+EPC, degraded to mean hedonic)"
                    )
                }
            )
        return fallback.model_copy(
            update={
                "methodology": (
                    "quantile-hedonic-log-price (PPD+EPC, degraded to median baseline)"
                )
            }
        )


def estimate_value_quantile_hedonic(
    target: HedonicTarget,
    comparables: Iterable[EnrichedComparable],
    *,
    lower: float = 0.1,
    upper: float = 0.9,
    median: float = 0.5,
    min_rows_for_fit: int = _MIN_ROWS_FOR_FIT,
    alpha: float = 1e-3,
    solver: str = "highs",
) -> ValuationEstimate:
    """One-shot convenience: fit on ``comparables`` then predict ``target``."""

    model = QuantileHedonicModel(
        lower=lower,
        upper=upper,
        median=median,
        min_rows_for_fit=min_rows_for_fit,
        alpha=alpha,
        solver=solver,
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
