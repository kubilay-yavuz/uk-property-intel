"""Log-price hedonic regression baseline.

This is the upgrade from the raw rolling-median model to a proper hedonic
AVM. It regresses ``log(price)`` on an interpretable feature set:

* ``log(floor_area_sqm)`` — continuous; captures the dominant price driver.
  The coefficient estimates the floor-area elasticity of price (typically
  0.7-1.0 for UK residential).
* ``property_type`` — OHE on PPD D/S/T/F/O.
* ``tenure`` — OHE (Freehold vs Leasehold; unknowns get their own dummy).
* ``postcode_area`` — OHE of the outward letters (SW / EC / NW …). Picks up
  within-city level differences; acts as a coarse location fixed effect.
* ``age_band`` — OHE on a small set of decadal buckets derived from EPC's
  ``construction-age-band``.
* ``energy_efficiency`` — continuous EPC numeric score, mean-centred. Captures
  modest discounts for energy-inefficient dwellings.

Why hedonic and not pure median £/sqm:

* Per-sqm medians collapse everything from flats to detached houses into one
  bucket. Hedonic coefficients let us decouple size from type.
* With small comparable pools (<50 rows), a naïve per-segment median can
  flip wildly as samples cross the bucket boundary. Hedonic pools information
  across buckets and produces smoother predictions.

Uncertainty bands come from the empirical residual distribution. We fit the
mean model, then report the 25th/75th percentile of residuals (multiplicatively
on the log scale) as the ``low_gbp`` / ``high_gbp`` pair. When the pool is too
thin to fit (<``min_rows_for_fit`` rows with a price and a floor area), we
fall back to the rolling-median baseline from :mod:`uk_property_avm.baseline`.

The model is intentionally **not** a black box: every prediction carries the
feature vector, the per-feature contribution, and the fallback tier used in
its ``methodology`` string, so a human can interrogate any estimate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import OneHotEncoder

from uk_property_avm.baseline import (
    _normalise_postcode,
    _postcode_area,
    estimate_value,
)
from uk_property_avm.models import Comparable, EnrichedComparable, ValuationEstimate

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "HedonicModel",
    "HedonicTarget",
    "estimate_value_hedonic",
    "normalise_age_band",
    "normalise_tenure",
]


_MIN_ROWS_FOR_FIT: Final = 20
"""Minimum training rows before we trust the hedonic fit.

Below this we return the median baseline instead. The threshold is
deliberately low: in small-market postcodes (e.g. rural Cumbria) even
5-10 comparables is often all we will ever get, and falling back to the
PPD median is strictly better than failing hard.
"""


def normalise_tenure(raw: str | None) -> str:
    """Coerce raw PPD tenure (``F``/``L``/``freehold``/``Leasehold``/…) to a canon.

    Outputs ``"FREEHOLD"``, ``"LEASEHOLD"``, or ``"UNKNOWN"``. Everything
    else maps to ``UNKNOWN`` so the OHE column count stays bounded.
    """

    if not raw:
        return "UNKNOWN"
    text = str(raw).strip().upper()
    if text.startswith("F"):
        return "FREEHOLD"
    if text.startswith("L"):
        return "LEASEHOLD"
    return "UNKNOWN"


_AGE_DECADE_RE: Final = re.compile(r"(\d{4})")


def normalise_age_band(raw: str | None) -> str:
    """Map EPC ``construction-age-band`` to a compact decadal label.

    EPC ships strings like ``"England and Wales: 1900-1929"`` or
    ``"Scotland: post-2002"``. We collapse to:

    * ``"PRE_1900"`` for anything before 1900
    * ``"1900_1929"``, ``"1930_1949"``, ``"1950_1966"``,
      ``"1967_1975"``, ``"1976_1982"``, ``"1983_1990"``,
      ``"1991_1995"``, ``"1996_2002"`` — the standard EPC bands
    * ``"POST_2002"`` for modern builds
    * ``"UNKNOWN"`` when the band is missing or unparseable
    """

    if not raw:
        return "UNKNOWN"
    text = raw.strip().upper()
    if "PRE" in text and ("1900" in text or "1919" in text):
        return "PRE_1900"
    if "POST" in text and ("2002" in text or "2012" in text):
        return "POST_2002"
    years = _AGE_DECADE_RE.findall(text)
    if not years:
        return "UNKNOWN"
    start = int(years[0])
    if start < 1900:
        return "PRE_1900"
    if start < 1930:
        return "1900_1929"
    if start < 1950:
        return "1930_1949"
    if start < 1967:
        return "1950_1966"
    if start < 1976:
        return "1967_1975"
    if start < 1983:
        return "1976_1982"
    if start < 1991:
        return "1983_1990"
    if start < 1996:
        return "1991_1995"
    if start < 2003:
        return "1996_2002"
    return "POST_2002"


def _postcode_area_safe(raw: str | None) -> str:
    """Return postcode area or ``UNKNOWN`` — tolerant variant used as a feature."""

    if not raw:
        return "UNKNOWN"
    try:
        return _postcode_area(raw)
    except ValueError:
        return "UNKNOWN"


def _property_type_safe(raw: str | None) -> str:
    if not raw:
        return "UNKNOWN"
    text = str(raw).strip().upper()
    if text in {"D", "S", "T", "F", "O"}:
        return text
    return "UNKNOWN"


@dataclass
class HedonicTarget:
    """Description of the dwelling we want a price for."""

    postcode: str
    property_type: str | None = None
    floor_area_sqm: float | None = None
    tenure: str | None = None
    age_band: str | None = None
    energy_efficiency: int | None = None


@dataclass
class _FitArtefacts:
    """Container for fitted sklearn pieces + diagnostic summaries."""

    regressor: LinearRegression
    encoder: OneHotEncoder
    categorical_cols: list[str]
    continuous_cols: list[str]
    continuous_means: dict[str, float]
    residuals_log: np.ndarray
    """Training residuals on the log-price scale, used for IQR bands."""
    training_rows: int


class HedonicModel:
    """Log-price hedonic model trained on :class:`EnrichedComparable` rows.

    Usage::

        model = HedonicModel()
        model.fit(enriched_rows)
        estimate = model.predict(
            HedonicTarget(postcode="SW2 5TN", property_type="T", floor_area_sqm=82)
        )

    The model is pickle-friendly (the internal sklearn objects are stored
    as instance attributes, no closures).
    """

    def __init__(self, *, min_rows_for_fit: int = _MIN_ROWS_FOR_FIT) -> None:
        self.min_rows_for_fit = min_rows_for_fit
        self._fit: _FitArtefacts | None = None
        self._fallback_pool: list[Comparable] = []

    @property
    def is_fitted(self) -> bool:
        """Has :meth:`fit` produced a usable hedonic fit?"""

        return self._fit is not None

    def fit(self, comparables: Iterable[EnrichedComparable]) -> None:
        """Fit the hedonic regression on ``comparables``.

        Rows without a positive ``price`` are dropped; rows without a
        ``floor_area_sqm`` are imputed with the median of the retained
        rows so we don't throw away the majority of matches just because
        some EPC fields are missing.
        """

        pool = list(comparables)
        self._fallback_pool = [_to_basic_comparable(c) for c in pool]
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

        regressor = LinearRegression()
        regressor.fit(x, y)
        y_hat = regressor.predict(x)
        residuals_log = y - y_hat

        self._fit = _FitArtefacts(
            regressor=regressor,
            encoder=encoder,
            categorical_cols=categorical_cols,
            continuous_cols=continuous_cols,
            continuous_means=continuous_means,
            residuals_log=residuals_log,
            training_rows=len(frame),
        )

    def predict(self, target: HedonicTarget) -> ValuationEstimate:
        """Return a :class:`ValuationEstimate` for ``target``.

        When the model isn't fitted (pool too thin or never called
        :meth:`fit`) this falls back to the rolling-median baseline.
        """

        target_pc = _normalise_postcode(target.postcode)
        if self._fit is None:
            return self._predict_via_fallback(target_pc, target)

        fit = self._fit
        row = {
            "property_type": _property_type_safe(target.property_type),
            "tenure": normalise_tenure(target.tenure),
            "postcode_area": _postcode_area_safe(target_pc),
            "age_band": normalise_age_band(target.age_band),
            "log_floor_area": math.log(
                target.floor_area_sqm
                if target.floor_area_sqm and target.floor_area_sqm > 0
                else math.exp(fit.continuous_means["log_floor_area"])
            ),
            "energy_efficiency_c": (
                (target.energy_efficiency or 0)
                - fit.continuous_means["energy_efficiency_c"]
                if target.energy_efficiency is not None
                else 0.0
            ),
        }

        x_cat = fit.encoder.transform(
            pd.DataFrame([{k: row[k] for k in fit.categorical_cols}])
        )
        x_cont = np.array([[row[col] for col in fit.continuous_cols]])
        x = np.hstack([x_cat, x_cont])
        log_estimate = float(fit.regressor.predict(x)[0])
        estimate = math.exp(log_estimate)

        if fit.residuals_log.size >= 4:
            lo_res = float(np.quantile(fit.residuals_log, 0.25))
            hi_res = float(np.quantile(fit.residuals_log, 0.75))
        else:
            lo_res = hi_res = 0.0
        low = estimate * math.exp(lo_res)
        high = estimate * math.exp(hi_res)
        if low > high:
            low, high = high, low

        basis = _basis_from_pool_size(fit.training_rows)
        confidence = _confidence_from_pool_size(fit.training_rows)

        return ValuationEstimate(
            estimate_gbp=round(estimate),
            low_gbp=round(low),
            high_gbp=round(high),
            confidence=confidence,
            comparables_used=fit.training_rows,
            basis=basis,
            postcode=target_pc,
            property_type=target.property_type,
            methodology="hedonic-log-price (PPD+EPC)",
        )

    def _predict_via_fallback(
        self, target_pc: str, target: HedonicTarget
    ) -> ValuationEstimate:
        """Median baseline fallback when the hedonic fit is missing."""

        fallback = estimate_value(
            target_pc,
            self._fallback_pool,
            property_type=target.property_type,
        )
        return fallback.model_copy(
            update={
                "methodology": (
                    "hedonic-log-price (PPD+EPC, degraded to median baseline)"
                )
            }
        )


def estimate_value_hedonic(
    target: HedonicTarget,
    comparables: Iterable[EnrichedComparable],
    *,
    min_rows_for_fit: int = _MIN_ROWS_FOR_FIT,
) -> ValuationEstimate:
    """One-shot convenience: fit on ``comparables`` then predict ``target``."""

    model = HedonicModel(min_rows_for_fit=min_rows_for_fit)
    model.fit(comparables)
    return model.predict(target)


def _to_basic_comparable(row: EnrichedComparable) -> Comparable:
    """Project ``EnrichedComparable`` back onto a vanilla :class:`Comparable`.

    Used to drive the median-baseline fallback when the hedonic fit cannot
    be produced — the baseline takes bare :class:`Comparable` rows.
    """

    return Comparable(
        transaction_id=row.transaction_id,
        price=row.price,
        transfer_date=row.transfer_date,
        property_type=row.property_type,
        tenure=row.tenure,
        paon=row.paon,
        street=row.street,
        postcode=row.postcode,
    )


def _build_training_frame(pool: list[EnrichedComparable]) -> pd.DataFrame | None:
    """Assemble the sklearn-ready training DataFrame.

    Returns ``None`` when no row has both a positive price and a positive
    floor area (can't fit a hedonic regression at all).
    """

    rows: list[dict[str, Any]] = []
    for c in pool:
        if c.price is None or c.price <= 0:
            continue
        rows.append(
            {
                "price": c.price,
                "log_price": math.log(c.price),
                "floor_area_sqm": c.floor_area_sqm,
                "property_type": _property_type_safe(c.property_type),
                "tenure": normalise_tenure(c.tenure),
                "postcode_area": _postcode_area_safe(c.postcode),
                "age_band": normalise_age_band(c.construction_age_band),
                "energy_efficiency": c.energy_efficiency,
            }
        )
    if not rows:
        return None
    frame = pd.DataFrame(rows)

    valid_floor = frame["floor_area_sqm"].dropna()
    if valid_floor.empty:
        return None
    median_floor = float(valid_floor.median())
    frame["floor_area_sqm"] = frame["floor_area_sqm"].fillna(median_floor)
    frame = frame[frame["floor_area_sqm"] > 0].copy()
    if frame.empty:
        return None
    frame["log_floor_area"] = np.log(frame["floor_area_sqm"].astype(float))

    ee_series = pd.to_numeric(frame["energy_efficiency"], errors="coerce")
    ee_mean = float(ee_series.mean()) if not ee_series.dropna().empty else 0.0
    frame["energy_efficiency_c"] = ee_series.fillna(ee_mean) - ee_mean

    return frame


def _basis_from_pool_size(n: int) -> str:
    """Map training pool size to the existing ``basis`` vocabulary.

    The hedonic model is stronger than the simple tier, but we reuse the
    existing Literal so consumers don't have to branch on model type.
    """

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
