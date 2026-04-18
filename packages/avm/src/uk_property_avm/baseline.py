"""Rolling-median baseline valuation model.

This is the *middle tier* promised in the package README - interpretable and
transparent, deliberately simpler than a gradient-boosted production AVM, but
materially better than a blanket "£/sqft * area" back-of-envelope.

It works by bucketing recent PPD sales on progressively looser keys and
returning the first bucket that has enough samples to trust:

1. Same full postcode + same property type (tightest).
2. Same full postcode (any type).
3. Same postcode *area* (e.g. ``SW``) + same type.
4. Same postcode area, any type.
5. All supplied comparables (wide net - falls back to national median when
   the caller hands in a broad dataset).

Each tier's "trust" threshold is fixed: we require at least 3 sales to
accept a tier. Below that we step down. The returned band (``low_gbp``,
``high_gbp``) is the 25th/75th percentile of the chosen bucket, so the band
widens naturally when the local market is heterogeneous.

Inflation adjustment is intentionally omitted from the baseline - most callers
will have already filtered ``comparables`` to a recent window (e.g. 3 years).
When production-grade HPI adjustment lands it will plug in at
:func:`_robust_median`.
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from uk_property_avm.models import Comparable, ValuationEstimate

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

_MIN_TRUSTED_SAMPLES: Final = 3
_HIGH_CONFIDENCE_SAMPLES: Final = 10
_MEDIUM_CONFIDENCE_SAMPLES: Final = 4

_POSTCODE_RE: Final = re.compile(
    r"^([A-Z]{1,2})(\d[A-Z0-9]?)\s*(\d)([A-Z]{2})$",
    re.IGNORECASE,
)


def _normalise_postcode(raw: str) -> str:
    """Return ``raw`` in canonical ``OUTWARD INWARD`` form (uppercased)."""

    stripped = raw.strip().upper().replace(" ", "")
    if not _POSTCODE_RE.match(stripped):
        msg = f"Invalid UK postcode: {raw!r}"
        raise ValueError(msg)
    return f"{stripped[:-3]} {stripped[-3:]}"


def _postcode_area(postcode: str) -> str:
    """Return the 1-or-2-letter area code (e.g. ``SW`` in ``SW2 5TN``)."""

    match = _POSTCODE_RE.match(postcode.replace(" ", ""))
    if match is None:
        msg = f"Invalid UK postcode: {postcode!r}"
        raise ValueError(msg)
    return match.group(1).upper()


def _robust_median(values: Sequence[int]) -> int:
    """Median rounded to the nearest pound (``int``)."""

    if not values:
        raise ValueError("cannot take median of empty sequence")
    return round(statistics.median(values))


def _percentile(values: Sequence[int], pct: float) -> int:
    """25th/75th percentile with linear interpolation. Works on short lists."""

    if not values:
        raise ValueError("cannot take percentile of empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo_idx = math.floor(rank)
    hi_idx = math.ceil(rank)
    if lo_idx == hi_idx:
        return int(ordered[int(rank)])
    lo, hi = ordered[lo_idx], ordered[hi_idx]
    frac = rank - lo_idx
    return round(lo + (hi - lo) * frac)


def _bucket_from_key(
    comparables: Iterable[Comparable],
    *,
    postcode: str | None,
    property_type: str | None,
) -> list[Comparable]:
    """Filter ``comparables`` by ``postcode`` and/or ``property_type``.

    Matching is case-insensitive on ``postcode``; whitespace is ignored. A
    ``None`` filter means "don't filter on this axis".
    """

    want_pc = postcode.replace(" ", "").upper() if postcode else None
    want_type = property_type.upper() if property_type else None
    out: list[Comparable] = []
    for c in comparables:
        if want_pc is not None and (
            not c.postcode or c.postcode.replace(" ", "").upper() != want_pc
        ):
            continue
        if want_type is not None and (
            not c.property_type or c.property_type.upper() != want_type
        ):
            continue
        out.append(c)
    return out


def _bucket_by_area(
    comparables: Iterable[Comparable],
    *,
    area: str,
    property_type: str | None,
) -> list[Comparable]:
    want_type = property_type.upper() if property_type else None
    out: list[Comparable] = []
    for c in comparables:
        if not c.postcode:
            continue
        try:
            if _postcode_area(c.postcode) != area:
                continue
        except ValueError:
            continue
        if want_type is not None and (
            not c.property_type or c.property_type.upper() != want_type
        ):
            continue
        out.append(c)
    return out


def _confidence_for(count: int, basis: str) -> str:
    if basis == "insufficient_data":
        return "low"
    if basis in {"national", "area_only"}:
        return "low"
    if count >= _HIGH_CONFIDENCE_SAMPLES and basis == "postcode_type":
        return "high"
    if count >= _MEDIUM_CONFIDENCE_SAMPLES:
        return "medium"
    return "low"


def estimate_value(
    postcode: str,
    comparables: Iterable[Comparable],
    *,
    property_type: str | None = None,
) -> ValuationEstimate:
    """Estimate the value of a dwelling at ``postcode``.

    Parameters
    ----------
    postcode:
        Target postcode in any case / spacing. Normalised internally.
    comparables:
        Recent PPD sales to value against. Callers typically fetch these
        via :class:`uk_property_apis.LandRegistryClient.search_by_postcode`
        or a wider postcode-area pull.
    property_type:
        Optional single-letter PPD code (``D``/``S``/``T``/``F``/``O``). When
        provided the model can tighten the band by matching like-with-like.

    Returns
    -------
    ValuationEstimate
        Point estimate plus provenance (band, tier, count).
    """

    target_pc = _normalise_postcode(postcode)
    area = _postcode_area(target_pc)
    pool = list(comparables)

    tiers: list[tuple[str, list[Comparable]]] = []
    if property_type:
        tiers.append(
            (
                "postcode_type",
                _bucket_from_key(pool, postcode=target_pc, property_type=property_type),
            )
        )
    tiers.append(("postcode_area", _bucket_from_key(pool, postcode=target_pc, property_type=None)))
    if property_type:
        tiers.append(
            ("area_type", _bucket_by_area(pool, area=area, property_type=property_type))
        )
    tiers.append(("area_only", _bucket_by_area(pool, area=area, property_type=None)))
    tiers.append(("national", pool))

    chosen_bucket: list[Comparable] = []
    chosen_basis = "insufficient_data"
    for basis, bucket in tiers:
        if len(bucket) >= _MIN_TRUSTED_SAMPLES:
            chosen_bucket = bucket
            chosen_basis = basis
            break

    if not chosen_bucket:
        return ValuationEstimate(
            estimate_gbp=0,
            low_gbp=0,
            high_gbp=0,
            confidence="low",
            comparables_used=0,
            basis="insufficient_data",
            postcode=target_pc,
            property_type=property_type,
        )

    prices = [c.price for c in chosen_bucket]
    mid = _robust_median(prices)
    lo = _percentile(prices, 25)
    hi = _percentile(prices, 75)
    if lo > hi:
        lo, hi = hi, lo

    return ValuationEstimate(
        estimate_gbp=mid,
        low_gbp=lo,
        high_gbp=hi,
        confidence=_confidence_for(len(chosen_bucket), chosen_basis),  # type: ignore[arg-type]
        comparables_used=len(chosen_bucket),
        basis=chosen_basis,  # type: ignore[arg-type]
        postcode=target_pc,
        property_type=property_type,
    )


def comparables_from_ppd(
    records: Iterable[object],
    *,
    min_transfer_date: str | None = None,
) -> list[Comparable]:
    """Convert :class:`PricePaidRecord` rows into :class:`Comparable` rows.

    Accepts anything with ``.transaction_id``, ``.price``, ``.transfer_date``
    and the optional address fields so that downstream code can feed raw
    Land Registry output directly into :func:`estimate_value`.

    ``min_transfer_date`` is an optional YYYY-MM-DD lower bound; rows older
    than it are dropped. Passing ``None`` keeps all rows.
    """

    cutoff = _parse_cutoff(min_transfer_date) if min_transfer_date else None
    out: list[Comparable] = []
    for row in records:
        price = _safe_int(getattr(row, "price", None))
        if price is None or price <= 0:
            continue
        transfer_date = getattr(row, "transfer_date", None) or ""
        if cutoff is not None and transfer_date < cutoff:
            continue
        out.append(
            Comparable(
                transaction_id=str(getattr(row, "transaction_id", "") or ""),
                price=price,
                transfer_date=transfer_date or "",
                property_type=getattr(row, "property_type", None),
                tenure=getattr(row, "tenure", None),
                paon=getattr(row, "paon", None),
                street=getattr(row, "street", None),
                postcode=getattr(row, "postcode", None),
            )
        )
    return out


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and not math.isnan(value):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _parse_cutoff(iso_date: str) -> str:
    """Validate ``iso_date`` and return it as-is.

    Parsing is strict: the cutoff is only used as a string comparison against
    PPD's ISO-formatted ``transfer_date``, so we reject anything that does
    not parse.
    """

    try:
        datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        msg = f"min_transfer_date must be YYYY-MM-DD; got {iso_date!r}"
        raise ValueError(msg) from exc
    return iso_date


__all__ = [
    "comparables_from_ppd",
    "estimate_value",
]
