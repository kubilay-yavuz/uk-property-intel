"""UK House Price Index (HPI) adjuster.

Adjusts historical nominal prices to a target month's money so hedonic
models can pool transactions from across several years without the
inflation signal dominating the floor-area / type / location effects.

The math is deliberately simple::

    adjusted = price * (HPI[to]  / HPI[from])

where ``HPI`` is a monthly index rebased to 2015 average = 100 (matching
the ONS's public series). ``from`` is the PPD transfer date; ``to`` is
either the "today" of the model run (default: the latest month in the
bundled series) or a caller-supplied pivot.

Sources and caveats
-------------------

The default series (:data:`_DEFAULT_SERIES`) is deliberately a
**minimal reference seed** — five quarterly anchor points spanning 2005
through today that are just enough to let every code path in this
module be exercised by the open test suite without a private data
dependency. Anything that actually cares about inflation-adjusted
valuations must supply a real series via
:meth:`HPIAdjuster.from_mapping` or :meth:`HPIAdjuster.from_csv`.

The full quarterly series used by the hosted A10 ``uk-avm`` actor
(~20 years, quarterly granularity, refreshed on ONS's publication
cadence) lives in the private
:mod:`uk_property_apify_shared.avm_data.hpi` module — ongoing data
maintenance is part of what the paid actor provides, so keeping the
public seed minimal is a deliberate moat-tightening choice.

Granularity stays quarterly (January / April / July / October of each
year) rather than monthly — this keeps the on-disk footprint tiny
without sacrificing adjustment accuracy: UK HPI rarely moves more than
1-2% month on month, and AVM models consume this multiplicatively so
the rounding error is a fraction of a percent. Callers needing monthly
precision can load the full ONS CSV via :meth:`HPIAdjuster.from_csv`.

Regional series are **not** bundled — shipping even a single extra
region doubles the data maintenance burden. Instead, the class exposes
:meth:`HPIAdjuster.from_ons_csv`, which loads the official ONS UK HPI
full-file CSV and lets the caller pick any region (``"United Kingdom"``,
``"London"``, ``"North East"``, any local-authority name, …). The
monthly publication is at::

    https://landregistry.data.gov.uk/app/ukhpi/download/

and the canonical file is ``UK-HPI-full-file-YYYY-MM.csv``. Once loaded,
the regional adjuster plugs into
:func:`adjust_comparable_prices` / :func:`adjust_enriched_prices`
identically to the bundled default — consumers swap one line of wiring
to upgrade from the minimal seed to a production-grade series.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from uk_property_avm.models import Comparable, EnrichedComparable

__all__ = [
    "HPIAdjuster",
    "adjust_comparable_prices",
    "adjust_enriched_prices",
    "list_ons_regions",
    "parse_month_key",
]

# Minimal reference HPI seed — five quarterly anchor points spanning the
# years we routinely see in PPD transfers (2005-current). This is
# deliberately NOT a production series: real valuations need the full
# ~85-point ONS-derived snapshot maintained in the private
# ``uk_property_apify_shared.avm_data.hpi`` module. The five anchors here
# are enough to exercise every code path (lookup, binary-search fallback,
# bounds errors, CSV round-trip) without shipping curated data.
_DEFAULT_SERIES: Final[Mapping[str, float]] = {
    "2005-01": 70.0,
    "2010-01": 76.0,
    "2015-01": 92.0,
    "2020-01": 121.0,
    "2026-01": 149.5,
}


def parse_month_key(date_str: str) -> str:
    """Normalise ``date_str`` to a ``YYYY-MM`` key.

    Accepts ``YYYY``, ``YYYY-MM``, and ``YYYY-MM-DD`` forms. Anything
    shorter than 7 characters raises :class:`ValueError` so we don't
    silently accept ``"20"`` or a date-less string.
    """

    text = date_str.strip()
    if len(text) < 7:
        msg = f"HPI lookup requires at least YYYY-MM, got {date_str!r}"
        raise ValueError(msg)
    # Validate year + month shape so "ABCD-EF" doesn't quietly pass through.
    year_str = text[:4]
    month_str = text[5:7]
    if not year_str.isdigit() or not month_str.isdigit():
        msg = f"HPI lookup requires a YYYY-MM date, got {date_str!r}"
        raise ValueError(msg)
    month = int(month_str)
    if not 1 <= month <= 12:
        msg = f"HPI month must be 1-12, got {month}"
        raise ValueError(msg)
    return f"{year_str}-{month_str}"


class HPIAdjuster:
    """Adjust nominal prices between two months using an HPI series.

    Usage::

        adjuster = HPIAdjuster.default()
        now_price = adjuster.adjust(280_000, "2015-06-12")
        # or pin to a specific target:
        pinned = adjuster.adjust(280_000, "2015-06-12", "2024-01-01")

    The lookup policy is "most-recent-on-or-before": if the requested
    month is not present in the series we fall back to the latest key
    strictly earlier than it. This makes the adjuster robust to the
    series' quarterly granularity without callers needing to know
    which months are populated.

    Out-of-range requests raise :class:`ValueError` rather than
    extrapolating — an HPI ratio outside the index's support is
    almost always a bug upstream (mis-parsed date, unit confusion,
    future pivot when the series hasn't been refreshed).
    """

    def __init__(self, series: Mapping[str, float]) -> None:
        if not series:
            msg = "HPI series must contain at least one (month, value) pair"
            raise ValueError(msg)
        cleaned: dict[str, float] = {}
        for raw_key, raw_value in series.items():
            key = parse_month_key(str(raw_key))
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                msg = f"HPI value for {raw_key!r} is not numeric: {raw_value!r}"
                raise ValueError(msg) from exc
            if value <= 0:
                msg = f"HPI value must be positive; got {value} at {raw_key!r}"
                raise ValueError(msg)
            cleaned[key] = value
        self._series: dict[str, float] = dict(sorted(cleaned.items()))
        self._keys: list[str] = list(self._series.keys())

    @classmethod
    def default(cls) -> HPIAdjuster:
        """Return an adjuster backed by the bundled quarterly snapshot."""

        return cls(_DEFAULT_SERIES)

    @classmethod
    def from_mapping(cls, series: Mapping[str, float]) -> HPIAdjuster:
        """Alias for the constructor, kept for parallelism with :meth:`from_csv`."""

        return cls(series)

    @classmethod
    def from_csv(cls, path: str | Path, *, month_col: str = "date", value_col: str = "value") -> HPIAdjuster:
        """Load an HPI series from a two-column CSV.

        The CSV must have a header row. ``month_col`` is parsed via
        :func:`parse_month_key` so ``YYYY``, ``YYYY-MM``, and
        ``YYYY-MM-DD`` all work — but every row must normalise to a
        unique ``YYYY-MM``. Duplicate months raise :class:`ValueError`
        so silent shadowing can't happen.
        """

        series: dict[str, float] = {}
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or month_col not in reader.fieldnames:
                msg = f"HPI CSV missing '{month_col}' column; got {reader.fieldnames!r}"
                raise ValueError(msg)
            if value_col not in reader.fieldnames:
                msg = f"HPI CSV missing '{value_col}' column; got {reader.fieldnames!r}"
                raise ValueError(msg)
            for row in reader:
                key = parse_month_key(row[month_col])
                if key in series:
                    msg = f"HPI CSV has duplicate month {key!r}"
                    raise ValueError(msg)
                series[key] = float(row[value_col])
        return cls(series)

    @classmethod
    def from_ons_csv(
        cls,
        path: str | Path,
        *,
        region: str = "United Kingdom",
        region_col: str = "RegionName",
        date_col: str = "Date",
        index_col: str = "Index",
    ) -> HPIAdjuster:
        """Load a monthly HPI series from the official ONS UK HPI full-file CSV.

        The ONS publishes one long-form CSV each month at
        ``https://landregistry.data.gov.uk/app/ukhpi/download/`` with
        columns such as ``Date`` / ``RegionName`` / ``AreaCode`` /
        ``AveragePrice`` / ``Index`` / ``IndexSA``. Every region
        (country, region, county, local authority) is represented by
        one row per month, so the file is filtered down to a single
        region before it becomes a usable HPI series.

        Parameters
        ----------
        path:
            Filesystem path to the ONS CSV.
        region:
            Value to match in ``region_col``. Case-sensitive.
            Defaults to ``"United Kingdom"`` to match the bundled seed.
            Use :func:`list_ons_regions` to discover the set of values
            available in a given publication vintage.
        region_col:
            Column whose value must equal ``region``. Defaults to
            ``"RegionName"`` (ONS's canonical label). Use
            ``"AreaCode"`` to filter by the GSS code instead.
        date_col:
            Date column. Defaults to ``"Date"``. Accepts
            ``YYYY-MM-DD``, ``YYYY-MM``, and ``DD/MM/YYYY`` forms.
        index_col:
            Index column. Defaults to ``"Index"`` (rebased 2015=100).
            Use ``"IndexSA"`` for the seasonally-adjusted series,
            or per-type variants like ``"IndexDetached"``,
            ``"IndexSemiDetached"``, ``"IndexTerraced"``,
            ``"IndexFlat"`` when the hedonic pool is segmented.

        Raises
        ------
        ValueError
            If the CSV is missing any of the required columns, if no
            rows match ``region``, or if the matched rows yield
            duplicate months.
        """

        matched: dict[str, float] = {}
        ons_path = Path(path)
        with ons_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            for required in (region_col, date_col, index_col):
                if required not in fieldnames:
                    msg = (
                        f"ONS HPI CSV missing '{required}' column; "
                        f"got {fieldnames!r}"
                    )
                    raise ValueError(msg)
            for row in reader:
                if row.get(region_col) != region:
                    continue
                raw_date = (row.get(date_col) or "").strip()
                raw_value = (row.get(index_col) or "").strip()
                if not raw_date or not raw_value:
                    continue
                key = _parse_ons_date(raw_date)
                try:
                    value = float(raw_value)
                except ValueError:
                    continue
                if key in matched:
                    msg = (
                        f"ONS HPI CSV has duplicate month {key!r} for "
                        f"region {region!r}"
                    )
                    raise ValueError(msg)
                matched[key] = value
        if not matched:
            msg = (
                f"ONS HPI CSV had no rows matching {region_col}={region!r}; "
                f"use list_ons_regions({ons_path!s}) to inspect the "
                "available values."
            )
            raise ValueError(msg)
        return cls(matched)

    @property
    def earliest_month(self) -> str:
        """First month in the series (``YYYY-MM``)."""

        return self._keys[0]

    @property
    def latest_month(self) -> str:
        """Last month in the series (``YYYY-MM``)."""

        return self._keys[-1]

    @property
    def keys(self) -> Sequence[str]:
        """Sorted tuple of ``YYYY-MM`` keys in the series."""

        return tuple(self._keys)

    def lookup(self, date_str: str) -> float:
        """Return the HPI value for ``date_str`` (most-recent on-or-before).

        Raises :class:`ValueError` if the month is before the series
        starts or after its last entry; callers should refresh the
        series rather than extrapolate.
        """

        key = parse_month_key(date_str)
        if key < self._keys[0]:
            msg = (
                f"HPI lookup {date_str!r} predates series start "
                f"({self._keys[0]}); refresh the series or choose a later pivot"
            )
            raise ValueError(msg)
        if key > self._keys[-1]:
            msg = (
                f"HPI lookup {date_str!r} postdates series end "
                f"({self._keys[-1]}); refresh the series or choose an earlier pivot"
            )
            raise ValueError(msg)
        if key in self._series:
            return self._series[key]
        # Binary search for the last key <= target.
        lo, hi = 0, len(self._keys) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._keys[mid] <= key:
                lo = mid
            else:
                hi = mid - 1
        return self._series[self._keys[lo]]

    def adjust(
        self,
        price: float,
        from_date: str,
        to_date: str | None = None,
    ) -> float:
        """Return ``price`` rescaled from ``from_date`` to ``to_date``.

        ``to_date`` defaults to :attr:`latest_month`; this is the common
        case of "bring every sale into today's money before regressing".
        """

        if price < 0:
            msg = f"HPI adjust requires a non-negative price, got {price}"
            raise ValueError(msg)
        from_idx = self.lookup(from_date)
        to_idx = self.lookup(to_date) if to_date else self._series[self._keys[-1]]
        return price * (to_idx / from_idx)

    def adjust_int(
        self,
        price: int,
        from_date: str,
        to_date: str | None = None,
    ) -> int:
        """Round-to-int flavour of :meth:`adjust` for ``Comparable.price``."""

        return round(self.adjust(float(price), from_date, to_date))


def adjust_comparable_prices(
    rows: Iterable[Comparable],
    adjuster: HPIAdjuster,
    *,
    to_date: str | None = None,
) -> list[Comparable]:
    """Return HPI-adjusted copies of ``rows``.

    Rows with a blank / unparseable ``transfer_date`` come through
    unchanged so downstream code can still see them. Rows whose date
    is out of the series' support are logged via :class:`ValueError`
    being *caught and ignored* — callers that want strict behaviour
    can use :meth:`HPIAdjuster.adjust` directly.
    """

    out: list[Comparable] = []
    for row in rows:
        adjusted_price = _try_adjust(row.price, row.transfer_date, adjuster, to_date)
        if adjusted_price is None:
            out.append(row)
        else:
            out.append(row.model_copy(update={"price": adjusted_price}))
    return out


def adjust_enriched_prices(
    rows: Iterable[EnrichedComparable],
    adjuster: HPIAdjuster,
    *,
    to_date: str | None = None,
) -> list[EnrichedComparable]:
    """HPI-adjust a sequence of :class:`EnrichedComparable` rows.

    See :func:`adjust_comparable_prices` for the out-of-range and
    unparseable-date policy.
    """

    out: list[EnrichedComparable] = []
    for row in rows:
        adjusted_price = _try_adjust(row.price, row.transfer_date, adjuster, to_date)
        if adjusted_price is None:
            out.append(row)
        else:
            out.append(row.model_copy(update={"price": adjusted_price}))
    return out


def _try_adjust(
    price: int,
    transfer_date: str,
    adjuster: HPIAdjuster,
    to_date: str | None,
) -> int | None:
    """Return the adjusted integer price, or ``None`` if adjustment isn't possible."""

    if not transfer_date:
        return None
    try:
        return adjuster.adjust_int(price, transfer_date, to_date)
    except ValueError:
        return None


def _parse_ons_date(value: str) -> str:
    """Normalise the ONS CSV's date column to ``YYYY-MM``.

    The ONS publication has used at least three date formats over the
    last decade (``DD/MM/YYYY`` in early vintages, ``YYYY-MM-DD`` in
    current releases, and ``YYYY-MM`` in manually-prepared snapshots).
    All three are canonicalised here; anything else raises via
    :func:`parse_month_key` so the caller sees a precise error.
    """

    text = value.strip()
    if "/" in text:
        parts = text.split("/")
        if len(parts) != 3:
            msg = f"Unrecognised ONS HPI date {value!r}"
            raise ValueError(msg)
        day_str, month_str, year_str = parts
        if not (year_str.isdigit() and month_str.isdigit() and day_str.isdigit()):
            msg = f"Unrecognised ONS HPI date {value!r}"
            raise ValueError(msg)
        return parse_month_key(f"{year_str}-{month_str.zfill(2)}")
    return parse_month_key(text)


def list_ons_regions(
    path: str | Path,
    *,
    region_col: str = "RegionName",
) -> list[str]:
    """Return the sorted set of region labels present in an ONS HPI CSV.

    Useful when inspecting a fresh ONS publication to find the exact
    spelling of a region before calling :meth:`HPIAdjuster.from_ons_csv`.
    """

    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        if region_col not in fieldnames:
            msg = f"ONS HPI CSV missing '{region_col}' column; got {fieldnames!r}"
            raise ValueError(msg)
        for row in reader:
            raw = (row.get(region_col) or "").strip()
            if raw:
                seen.add(raw)
    return sorted(seen)
