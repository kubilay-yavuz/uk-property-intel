"""PPD + EPC join pipeline.

Produces :class:`uk_property_avm.models.EnrichedComparable` rows by joining
HM Land Registry Price-Paid transactions to EPC lodgements on a normalised
``(postcode, paon, street)`` key, with a time-nearest rule to handle
properties that have had multiple EPCs lodged over the years.

Why a dedicated module and not just a pandas groupby:

1. Address normalisation is fiddly enough to deserve its own tested surface
   (trailing letters on house numbers, flat prefixes, St/Saint variants,
   comma-vs-space delimiters, …). Centralising it means both the hedonic
   baseline and the eval harness apply the same rules.
2. Many properties have multiple EPC lodgements (a re-EPC every 10 years is
   standard, plus post-renovation re-lodgements). We want the one closest in
   time to the PPD sale — ideally lodged **before** the sale, so the
   floor-area / rating we use reflects the physical state of the dwelling at
   transaction time. Expressing that with a DuckDB window function is both
   faster and clearer than a pandas loop.
3. Large PPD pulls (>50k rows) join faster in DuckDB than in pandas
   ``merge_asof``. Keeping the DuckDB dependency local to this module lets
   us swap the engine later without touching hedonic / eval code.

The pipeline is deliberately fixture-driven first: the public entry points
take :class:`PricePaidRecord`-shaped and :class:`EPCCertificateRow`-shaped
iterables (anything duck-typed with the right attributes works, including
dicts and the real :mod:`uk_property_apis` models). Real-data callers pipe
`client.search_by_postcode(...)` straight in.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

import duckdb
import pandas as pd

from uk_property_avm.models import EnrichedComparable

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "DEFAULT_LOOKBACK_YEARS",
    "JoinConfig",
    "enrich_comparables",
    "normalise_address_key",
    "normalise_postcode",
    "parse_floor_area",
]

DEFAULT_LOOKBACK_YEARS: Final = 5
"""Default upper bound for how stale an EPC can be before we reject the match.

Set generously: even a 10y-old EPC is often a better proxy than nothing,
but in practice callers usually want to tighten this to 3-5 years.
"""


_POSTCODE_RE: Final = re.compile(
    r"^([A-Z]{1,2})(\d[A-Z0-9]?)\s*(\d)([A-Z]{2})$",
    re.IGNORECASE,
)

_HOUSE_NUMBER_RE: Final = re.compile(r"^(\d+)([A-Z]?)$")
"""Matches a clean house number, optionally with a trailing letter (``12A``)."""

_STREET_WORD_CANON: Final[dict[str, str]] = {
    "ST": "STREET",
    "ST.": "STREET",
    "RD": "ROAD",
    "RD.": "ROAD",
    "AVE": "AVENUE",
    "AVE.": "AVENUE",
    "LN": "LANE",
    "LN.": "LANE",
    "DR": "DRIVE",
    "DR.": "DRIVE",
    "CT": "COURT",
    "CT.": "COURT",
    "CL": "CLOSE",
    "CL.": "CLOSE",
    "CRES": "CRESCENT",
    "CRES.": "CRESCENT",
    "PL": "PLACE",
    "PL.": "PLACE",
    "TER": "TERRACE",
    "TER.": "TERRACE",
    "SQ": "SQUARE",
    "SQ.": "SQUARE",
    "GDN": "GARDEN",
    "GDN.": "GARDEN",
    "GDNS": "GARDENS",
    "GDNS.": "GARDENS",
}


def normalise_postcode(raw: str | None) -> str | None:
    """Return ``raw`` in canonical ``OUTWARD INWARD`` form.

    Returns ``None`` when the input is missing or isn't a recognisable UK
    postcode, rather than raising — we want the join to degrade gracefully
    on malformed upstream data instead of failing the whole pipeline.
    """

    if not raw:
        return None
    stripped = raw.strip().upper().replace(" ", "")
    if not _POSTCODE_RE.match(stripped):
        return None
    return f"{stripped[:-3]} {stripped[-3:]}"


def _normalise_street(raw: str | None) -> str | None:
    """Uppercase, collapse whitespace, expand common abbreviations."""

    if not raw:
        return None
    upper = re.sub(r"\s+", " ", raw.strip().upper())
    if not upper:
        return None
    words = [_STREET_WORD_CANON.get(w, w) for w in upper.split(" ")]
    return " ".join(words)


def _normalise_paon(raw: str | None) -> str | None:
    """Strip whitespace + trailing punctuation; keep ``12A`` shape intact.

    EPC addresses embed the PAON in the free-text ``address`` field, so the
    PPD->EPC join pulls PAON out of the EPC side via :func:`_paon_from_epc`.
    This normaliser is shared.
    """

    if not raw:
        return None
    cleaned = raw.strip().upper().rstrip(",.")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or None


def _paon_from_epc(address: str | None) -> str | None:
    """Pull the leading house number out of an EPC ``address`` string.

    EPC stores the address as one concatenated field, e.g. ``"12A CHURCH
    STREET, LONDON"``. The first whitespace-delimited token is almost
    always the PAON. When the first token isn't a number we return the
    entire first segment — that handles named properties like ``"ROSE
    COTTAGE"``.
    """

    if not address:
        return None
    first_segment = address.split(",", 1)[0].strip()
    if not first_segment:
        return None
    first_token = first_segment.split(" ", 1)[0].strip().upper()
    if _HOUSE_NUMBER_RE.match(first_token):
        return first_token
    return first_segment.strip().upper() or None


def _street_from_epc(address: str | None) -> str | None:
    """Pull the street portion out of an EPC ``address`` string.

    For ``"12A CHURCH STREET, LONDON"`` we keep ``"CHURCH STREET"``. For
    ``"ROSE COTTAGE, CHURCH LANE, ..."`` the EPC convention is the named
    property in the first segment and the actual street in the second;
    we take that second segment when the first doesn't start with a
    number.
    """

    if not address:
        return None
    segments = [s.strip() for s in address.split(",") if s.strip()]
    if not segments:
        return None
    first = segments[0]
    first_token = first.split(" ", 1)[0].strip().upper()
    if _HOUSE_NUMBER_RE.match(first_token):
        _, _, remainder = first.partition(" ")
        return _normalise_street(remainder)
    if len(segments) >= 2:
        return _normalise_street(segments[1])
    return _normalise_street(first)


def normalise_address_key(
    *,
    postcode: str | None,
    paon: str | None,
    street: str | None,
) -> str | None:
    """Compose the canonical join key ``"POSTCODE|PAON|STREET"``.

    Returns ``None`` if any component is missing — those rows can only
    match on postcode alone, which the join handles as a fallback tier.
    """

    pc = normalise_postcode(postcode)
    pa = _normalise_paon(paon)
    st = _normalise_street(street)
    if not pc or not pa or not st:
        return None
    return f"{pc}|{pa}|{st}"


def parse_floor_area(raw: Any) -> float | None:
    """Parse EPC ``total-floor-area`` (string in the raw payload) to sqm.

    Handles numeric types directly and strips commas / whitespace /
    trailing ``m2`` / ``sqm`` units from string inputs. Returns ``None``
    for unparseable or implausible values (<=0, >10,000 sqm).
    """

    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        value = float(raw)
    else:
        text = str(raw).strip().replace(",", "")
        if not text:
            return None
        text = re.sub(r"\s*(m2|sqm|square metres?)\s*$", "", text, flags=re.IGNORECASE)
        try:
            value = float(text)
        except ValueError:
            return None
    if value <= 0 or value > 10_000:
        return None
    return value


def _parse_int(raw: Any) -> int | None:
    """Parse EPC ``current-energy-efficiency`` (often a string) to int."""

    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


class JoinConfig:
    """Tunables for :func:`enrich_comparables`.

    Kept as a plain class (not dataclass / Pydantic) so callers can subclass
    to override behaviour without redefining every field.
    """

    #: Max staleness between PPD transfer date and EPC inspection date, in days.
    #: EPCs stay valid for 10 years by statute, but the closer-to-sale the better.
    max_days_delta: int = DEFAULT_LOOKBACK_YEARS * 365

    #: If ``True``, prefer an EPC lodged **before** the PPD sale over one
    #: lodged after (subject to ``max_days_delta``). Callers running a live
    #: AVM almost always want this: the floor-area at sale date should not
    #: reflect a post-sale renovation.
    prefer_before_sale: bool = True

    #: If ``True``, fall back to a postcode-only join when no exact-address
    #: match exists. When the property is a terraced house with one EPC per
    #: dwelling this falls back to sibling dwellings on the same street,
    #: which is often still informative for floor-area imputation.
    postcode_fallback: bool = False

    def __init__(
        self,
        *,
        max_days_delta: int | None = None,
        prefer_before_sale: bool | None = None,
        postcode_fallback: bool | None = None,
    ) -> None:
        if max_days_delta is not None:
            self.max_days_delta = max_days_delta
        if prefer_before_sale is not None:
            self.prefer_before_sale = prefer_before_sale
        if postcode_fallback is not None:
            self.postcode_fallback = postcode_fallback


def _ppd_frame(rows: Iterable[Any]) -> pd.DataFrame:
    """Build the PPD-side DataFrame with canonicalised join keys."""

    frame = pd.DataFrame(
        [
            {
                "transaction_id": str(getattr(r, "transaction_id", "") or ""),
                "price": getattr(r, "price", None),
                "transfer_date": getattr(r, "transfer_date", None) or "",
                "property_type": getattr(r, "property_type", None),
                "tenure": getattr(r, "tenure", None),
                "paon": getattr(r, "paon", None),
                "street": getattr(r, "street", None),
                "postcode": getattr(r, "postcode", None),
            }
            for r in rows
        ]
    )
    if frame.empty:
        frame = pd.DataFrame(
            columns=[
                "transaction_id",
                "price",
                "transfer_date",
                "property_type",
                "tenure",
                "paon",
                "street",
                "postcode",
                "postcode_norm",
                "address_key",
            ]
        )
        return frame
    frame["postcode_norm"] = frame["postcode"].map(normalise_postcode)
    frame["address_key"] = frame.apply(
        lambda r: normalise_address_key(
            postcode=r["postcode"], paon=r["paon"], street=r["street"]
        ),
        axis=1,
    )
    return frame


def _epc_frame(rows: Iterable[Any]) -> pd.DataFrame:
    """Build the EPC-side DataFrame with canonicalised join keys."""

    records: list[dict[str, Any]] = []
    for r in rows:
        address = getattr(r, "address", None)
        postcode = getattr(r, "postcode", None)
        paon = _paon_from_epc(address)
        street = _street_from_epc(address)
        inspection = getattr(r, "inspection_date", None) or ""
        records.append(
            {
                "lmk_key": getattr(r, "lmk_key", None),
                "postcode": postcode,
                "address": address,
                "paon": paon,
                "street": street,
                "inspection_date": inspection,
                "floor_area_sqm": parse_floor_area(getattr(r, "total_floor_area", None)),
                "energy_rating": getattr(r, "current_energy_rating", None),
                "energy_efficiency": _parse_int(
                    getattr(r, "current_energy_efficiency", None)
                ),
                "built_form": getattr(r, "built_form", None),
                "construction_age_band": getattr(r, "construction_age_band", None),
                "epc_property_type": getattr(r, "property_type", None),
            }
        )
    frame = pd.DataFrame(records)
    if frame.empty:
        frame = pd.DataFrame(
            columns=[
                "lmk_key",
                "postcode",
                "address",
                "paon",
                "street",
                "inspection_date",
                "floor_area_sqm",
                "energy_rating",
                "energy_efficiency",
                "built_form",
                "construction_age_band",
                "epc_property_type",
                "postcode_norm",
                "address_key",
            ]
        )
        return frame
    frame["postcode_norm"] = frame["postcode"].map(normalise_postcode)
    frame["address_key"] = frame.apply(
        lambda r: normalise_address_key(
            postcode=r["postcode"], paon=r["paon"], street=r["street"]
        ),
        axis=1,
    )
    return frame


_JOIN_SQL_EXACT: Final = """
WITH ppd AS (
    SELECT *
    FROM ppd_df
    WHERE address_key IS NOT NULL
),
epc AS (
    SELECT *
    FROM epc_df
    WHERE address_key IS NOT NULL AND inspection_date IS NOT NULL AND inspection_date <> ''
),
candidates AS (
    SELECT
        ppd.transaction_id,
        epc.lmk_key,
        epc.floor_area_sqm,
        epc.energy_rating,
        epc.energy_efficiency,
        epc.built_form,
        epc.construction_age_band,
        epc.epc_property_type,
        epc.inspection_date AS epc_inspection_date,
        ABS(date_diff('day', TRY_STRPTIME(ppd.transfer_date, '%Y-%m-%d'),
                              TRY_STRPTIME(epc.inspection_date, '%Y-%m-%d'))) AS abs_days,
        CASE
            WHEN TRY_STRPTIME(epc.inspection_date, '%Y-%m-%d')
                 <= TRY_STRPTIME(ppd.transfer_date, '%Y-%m-%d') THEN 0
            ELSE 1
        END AS after_sale_flag
    FROM ppd
    JOIN epc USING (address_key)
    WHERE ABS(date_diff('day', TRY_STRPTIME(ppd.transfer_date, '%Y-%m-%d'),
                                TRY_STRPTIME(epc.inspection_date, '%Y-%m-%d'))) <= ?
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY transaction_id
            ORDER BY
                CASE WHEN ? THEN after_sale_flag ELSE 0 END ASC,
                abs_days ASC,
                lmk_key ASC
        ) AS rn
    FROM candidates
)
SELECT
    transaction_id,
    lmk_key,
    floor_area_sqm,
    energy_rating,
    energy_efficiency,
    built_form,
    construction_age_band,
    epc_property_type,
    epc_inspection_date
FROM ranked
WHERE rn = 1
"""


_JOIN_SQL_POSTCODE: Final = """
WITH ppd_missing AS (
    SELECT *
    FROM ppd_df
    WHERE postcode_norm IS NOT NULL
      AND transaction_id NOT IN (
          SELECT transaction_id FROM exact_hits
      )
),
epc AS (
    SELECT *
    FROM epc_df
    WHERE postcode_norm IS NOT NULL AND inspection_date IS NOT NULL AND inspection_date <> ''
),
candidates AS (
    SELECT
        ppd.transaction_id,
        epc.lmk_key,
        epc.floor_area_sqm,
        epc.energy_rating,
        epc.energy_efficiency,
        epc.built_form,
        epc.construction_age_band,
        epc.epc_property_type,
        epc.inspection_date AS epc_inspection_date,
        ABS(date_diff('day', TRY_STRPTIME(ppd.transfer_date, '%Y-%m-%d'),
                              TRY_STRPTIME(epc.inspection_date, '%Y-%m-%d'))) AS abs_days,
        CASE
            WHEN TRY_STRPTIME(epc.inspection_date, '%Y-%m-%d')
                 <= TRY_STRPTIME(ppd.transfer_date, '%Y-%m-%d') THEN 0
            ELSE 1
        END AS after_sale_flag
    FROM ppd_missing AS ppd
    JOIN epc USING (postcode_norm)
    WHERE ABS(date_diff('day', TRY_STRPTIME(ppd.transfer_date, '%Y-%m-%d'),
                                TRY_STRPTIME(epc.inspection_date, '%Y-%m-%d'))) <= ?
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY transaction_id
            ORDER BY
                CASE WHEN ? THEN after_sale_flag ELSE 0 END ASC,
                abs_days ASC,
                lmk_key ASC
        ) AS rn
    FROM candidates
)
SELECT
    transaction_id,
    lmk_key,
    floor_area_sqm,
    energy_rating,
    energy_efficiency,
    built_form,
    construction_age_band,
    epc_property_type,
    epc_inspection_date
FROM ranked
WHERE rn = 1
"""


def enrich_comparables(
    ppd_rows: Iterable[Any],
    epc_rows: Iterable[Any],
    *,
    config: JoinConfig | None = None,
) -> list[EnrichedComparable]:
    """Join PPD sales to EPC lodgements and return enriched comparables.

    The join engine is in-process DuckDB on pandas DataFrames. Two tiers
    are evaluated:

    1. **Exact address** — match on normalised ``postcode|paon|street`` and
       pick the single EPC lodgement closest in time, with ``prefer_before_sale``
       breaking ties.
    2. **Postcode fallback** (optional, off by default) — for PPD rows
       without an exact match, fall back to any EPC on the same postcode
       within the staleness window.

    Unmatched PPD rows are still returned (with all EPC fields ``None``)
    so callers can filter or impute downstream. The returned list preserves
    the order of ``ppd_rows``.
    """

    cfg = config or JoinConfig()
    ppd_df = _ppd_frame(ppd_rows)
    epc_df = _epc_frame(epc_rows)

    if ppd_df.empty:
        return []

    con = duckdb.connect(":memory:")
    try:
        con.register("ppd_df", ppd_df)
        con.register("epc_df", epc_df)

        exact_hits = con.execute(
            _JOIN_SQL_EXACT,
            [cfg.max_days_delta, cfg.prefer_before_sale],
        ).df()
        con.register("exact_hits", exact_hits)

        if cfg.postcode_fallback:
            postcode_hits = con.execute(
                _JOIN_SQL_POSTCODE,
                [cfg.max_days_delta, cfg.prefer_before_sale],
            ).df()
        else:
            postcode_hits = pd.DataFrame(columns=exact_hits.columns)
    finally:
        con.close()

    exact_ids = set(exact_hits["transaction_id"].tolist())
    postcode_ids = set(postcode_hits["transaction_id"].tolist())
    exact_by_id = exact_hits.set_index("transaction_id").to_dict(orient="index")
    postcode_by_id = postcode_hits.set_index("transaction_id").to_dict(orient="index")

    out: list[EnrichedComparable] = []
    for _, row in ppd_df.iterrows():
        txn_id = row["transaction_id"]
        match: dict[str, Any] | None = None
        match_quality: str = "none"
        if txn_id in exact_ids:
            match = exact_by_id[txn_id]
            match_quality = "exact_address"
        elif txn_id in postcode_ids:
            match = postcode_by_id[txn_id]
            match_quality = "postcode_only"

        enriched = EnrichedComparable(
            transaction_id=str(txn_id),
            price=_safe_price(row["price"]),
            transfer_date=row["transfer_date"] or "",
            property_type=row["property_type"],
            tenure=row["tenure"],
            paon=row["paon"],
            street=row["street"],
            postcode=row["postcode"],
            floor_area_sqm=_nullable_float(match, "floor_area_sqm") if match else None,
            energy_rating=_nullable_str(match, "energy_rating") if match else None,
            energy_efficiency=_nullable_int(match, "energy_efficiency") if match else None,
            built_form=_nullable_str(match, "built_form") if match else None,
            construction_age_band=_nullable_str(match, "construction_age_band")
            if match
            else None,
            epc_property_type=_nullable_str(match, "epc_property_type") if match else None,
            epc_inspection_date=_nullable_str(match, "epc_inspection_date")
            if match
            else None,
            epc_lmk_key=_nullable_str(match, "lmk_key") if match else None,
            match_quality=match_quality,  # type: ignore[arg-type]
        )
        out.append(enriched)
    return out


def _safe_price(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _nullable_str(match: dict[str, Any], key: str) -> str | None:
    """Coerce a cell from the DuckDB result dict to ``str | None``.

    DuckDB writes missing values as ``None`` or ``NaN``; both should map
    back to Python ``None`` so the Pydantic model accepts them.
    """

    value = match.get(key)
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _nullable_float(match: dict[str, Any], key: str) -> float | None:
    value = match.get(key)
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nullable_int(match: dict[str, Any], key: str) -> int | None:
    value = match.get(key)
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
