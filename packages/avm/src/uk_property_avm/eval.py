"""Held-out evaluation harness for AVM models.

Every AVM gets two numbers that matter: a point-accuracy metric (how close
is the estimate to the true sale price?) and a calibration metric (do the
bands contain the price at the rate the model claims?). We report both,
segmented by postcode area and property type, so regressions don't hide
inside the headline average.

The harness is **model-agnostic** — it takes any callable that, given a
training pool and a target description, returns a :class:`ValuationEstimate`.
The default predictor is :func:`uk_property_avm.estimate_value_hedonic`, but
you can drop in a competing model and score it on the same split.

The canonical workflow:

    >>> train, test = time_based_split(rows, holdout_months=6)
    >>> report = evaluate_model(train, test)
    >>> report.mape        # headline median absolute % error
    >>> report.coverage    # fraction of test rows inside [low, high]
    >>> report.segments    # per (postcode_area x property_type) bucket

Design notes:

* **Temporal split, not random**: house prices drift year-to-year, so a
  random hold-out leaks HPI into the training signal. A trailing-window
  hold-out mimics how the model will actually be used (predict the next
  N months given the past).
* **MAPE, not MAE**: UK residential prices span two orders of magnitude
  (£80k flats to £8m mansions). MAPE keeps the metric commensurable.
* **Median, not mean**: PPD is heavy-tailed even within a single postcode
  area thanks to the occasional very-luxury sale. Reporting the median
  absolute % error keeps one outlier transaction from distorting the score.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from uk_property_avm.baseline import _postcode_area
from uk_property_avm.hedonic import HedonicTarget, estimate_value_hedonic

if TYPE_CHECKING:
    from collections.abc import Iterable

    from uk_property_avm.models import EnrichedComparable, ValuationEstimate

__all__ = [
    "EvalReport",
    "EvalSegment",
    "Predictor",
    "evaluate_model",
    "format_report_markdown",
    "time_based_split",
]


class Predictor(Protocol):
    """Callable that a harness can score.

    The contract: take the training pool and a description of the target
    dwelling, return a :class:`ValuationEstimate`. Signature is chosen so
    ``estimate_value_hedonic`` is already a valid predictor.
    """

    def __call__(
        self, target: HedonicTarget, pool: Iterable[EnrichedComparable]
    ) -> ValuationEstimate: ...


class EvalSegment(BaseModel):
    """Per-bucket accuracy + calibration numbers."""

    model_config = ConfigDict(extra="forbid")

    postcode_area: str
    property_type: str
    count: int = Field(..., ge=0)
    mape: float = Field(
        ...,
        description="Median absolute percentage error (0-1 scale; 0.10 = 10%).",
    )
    mean_ape: float = Field(
        ...,
        description="Mean absolute percentage error (0-1 scale).",
    )
    median_abs_error_gbp: int = Field(
        ...,
        description="Median absolute pounds difference between estimate and true price.",
    )
    coverage: float = Field(
        ...,
        description=(
            "Fraction of test rows whose true price is inside the [low_gbp,high_gbp] band."
            " Calibrated models should produce ~0.50 for IQR bands."
        ),
    )


class EvalReport(BaseModel):
    """Full evaluation result: headline numbers + per-segment breakdown."""

    model_config = ConfigDict(extra="forbid")

    mape: float = Field(..., description="Median absolute percentage error across all test rows.")
    mean_ape: float = Field(..., description="Mean absolute percentage error across all rows.")
    median_abs_error_gbp: int = Field(
        ..., description="Median absolute £-error across all rows."
    )
    coverage: float = Field(..., description="Fraction of test rows inside [low,high].")
    n_train: int = Field(..., ge=0)
    n_test: int = Field(..., ge=0)
    n_scored: int = Field(
        ...,
        ge=0,
        description="Test rows with a usable estimate (non-zero, non-insufficient_data).",
    )
    segments: list[EvalSegment]


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def time_based_split(
    rows: Iterable[EnrichedComparable],
    *,
    holdout_months: int = 6,
    cutoff_date: str | None = None,
) -> tuple[list[EnrichedComparable], list[EnrichedComparable]]:
    """Split ``rows`` into (train, test) using a temporal cutoff.

    Rows with a ``transfer_date`` on or after the cutoff go to test; rows
    before the cutoff go to train. Rows with an unparseable date are
    dropped — we have no reliable way to place them.

    The cutoff is by default ``max(transfer_date) - holdout_months`` so the
    test set is the most recent N months, mimicking production usage.
    Callers can pass ``cutoff_date`` explicitly to reproduce a fixed split
    across runs.
    """

    pool = [r for r in rows if _parse_date(r.transfer_date) is not None]
    if not pool:
        return [], []

    if cutoff_date is None:
        max_date = max(_parse_date(r.transfer_date) or datetime.min.replace(tzinfo=UTC) for r in pool)
        cutoff = max_date - timedelta(days=holdout_months * 30)
    else:
        parsed = _parse_date(cutoff_date)
        if parsed is None:
            msg = f"cutoff_date must be YYYY-MM-DD; got {cutoff_date!r}"
            raise ValueError(msg)
        cutoff = parsed

    train: list[EnrichedComparable] = []
    test: list[EnrichedComparable] = []
    for row in pool:
        parsed = _parse_date(row.transfer_date)
        if parsed is None:
            continue
        if parsed < cutoff:
            train.append(row)
        else:
            test.append(row)
    return train, test


def evaluate_model(
    train: Iterable[EnrichedComparable],
    test: Iterable[EnrichedComparable],
    *,
    predictor: Predictor | None = None,
) -> EvalReport:
    """Score ``predictor`` on ``test`` after training on ``train``.

    The predictor is called once per test row: we pass it the entire
    training pool and the test row's features packaged as a
    :class:`HedonicTarget`. That's intentionally wasteful — refitting per
    row makes the harness correct even when the underlying model isn't
    idempotent (e.g. a baseline that re-fits per postcode area).

    Callers who want faster evaluation can wrap a pre-fitted model into
    a closure that ignores the pool argument.
    """

    train_pool = list(train)
    test_pool = list(test)
    chosen_predictor = predictor or estimate_value_hedonic

    per_row: list[dict[str, Any]] = []
    for row in test_pool:
        if row.price is None or row.price <= 0:
            continue
        target = HedonicTarget(
            postcode=row.postcode or "",
            property_type=row.property_type,
            floor_area_sqm=row.floor_area_sqm,
            tenure=row.tenure,
            age_band=row.construction_age_band,
            energy_efficiency=row.energy_efficiency,
        )
        if not target.postcode:
            continue
        try:
            estimate = chosen_predictor(target, train_pool)
        except ValueError:
            continue
        if estimate.estimate_gbp <= 0:
            continue
        abs_err = abs(estimate.estimate_gbp - row.price)
        ape = abs_err / row.price
        inside = estimate.low_gbp <= row.price <= estimate.high_gbp
        per_row.append(
            {
                "postcode_area": _safe_postcode_area(row.postcode),
                "property_type": row.property_type or "UNKNOWN",
                "abs_err": abs_err,
                "ape": ape,
                "inside": inside,
            }
        )

    if not per_row:
        return EvalReport(
            mape=0.0,
            mean_ape=0.0,
            median_abs_error_gbp=0,
            coverage=0.0,
            n_train=len(train_pool),
            n_test=len(test_pool),
            n_scored=0,
            segments=[],
        )

    apes = [r["ape"] for r in per_row]
    abs_errs = [r["abs_err"] for r in per_row]
    coverage_flags = [r["inside"] for r in per_row]

    segments: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in per_row:
        key = (entry["postcode_area"], entry["property_type"])
        segments.setdefault(key, []).append(entry)

    segment_models: list[EvalSegment] = []
    for (area, ptype), entries in sorted(segments.items()):
        seg_apes = [e["ape"] for e in entries]
        seg_abs = [e["abs_err"] for e in entries]
        seg_inside = [e["inside"] for e in entries]
        segment_models.append(
            EvalSegment(
                postcode_area=area,
                property_type=ptype,
                count=len(entries),
                mape=statistics.median(seg_apes),
                mean_ape=statistics.fmean(seg_apes),
                median_abs_error_gbp=round(statistics.median(seg_abs)),
                coverage=sum(seg_inside) / len(seg_inside),
            )
        )

    return EvalReport(
        mape=statistics.median(apes),
        mean_ape=statistics.fmean(apes),
        median_abs_error_gbp=round(statistics.median(abs_errs)),
        coverage=sum(coverage_flags) / len(coverage_flags),
        n_train=len(train_pool),
        n_test=len(test_pool),
        n_scored=len(per_row),
        segments=segment_models,
    )


def _safe_postcode_area(raw: str | None) -> str:
    if not raw:
        return "UNKNOWN"
    try:
        return _postcode_area(raw)
    except ValueError:
        return "UNKNOWN"


def format_report_markdown(report: EvalReport) -> str:
    """Render ``report`` as a human-readable Markdown table.

    Useful in CI and for human-readable rollups. Not used by any production
    code path — the structured :class:`EvalReport` is the authoritative
    output.
    """

    lines = [
        "| Metric | Value |",
        "|---|---|",
        f"| MAPE (median) | {report.mape:.1%} |",
        f"| MAPE (mean) | {report.mean_ape:.1%} |",
        f"| Median absolute error | £{report.median_abs_error_gbp:,} |",
        f"| Coverage (IQR band) | {report.coverage:.1%} |",
        f"| Train rows | {report.n_train:,} |",
        f"| Test rows | {report.n_test:,} |",
        f"| Scored rows | {report.n_scored:,} |",
        "",
        "| Postcode area | Property type | n | MAPE | Coverage |",
        "|---|---|---|---|---|",
    ]
    for seg in report.segments:
        lines.append(
            f"| {seg.postcode_area} | {seg.property_type} | {seg.count} | "
            f"{seg.mape:.1%} | {seg.coverage:.1%} |"
        )
    return "\n".join(lines)
