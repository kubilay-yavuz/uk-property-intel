"""Pydantic models for the UK property AVM baseline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PropertyType = Literal["D", "S", "T", "F", "O"]
"""HM Land Registry PPD ``propertyType`` codes:

* ``D`` - Detached
* ``S`` - Semi-detached
* ``T`` - Terraced
* ``F`` - Flat / Maisonette
* ``O`` - Other (non-standard / commercial)
"""


class Comparable(BaseModel):
    """One historic sale used as a data point for the estimate."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    price: int = Field(..., description="Sale price in GBP pounds (integer).")
    transfer_date: str = Field(..., description="ISO-8601 transfer date (YYYY-MM-DD).")
    property_type: str | None = None
    tenure: str | None = None
    paon: str | None = None
    street: str | None = None
    postcode: str | None = None


class EnrichedComparable(BaseModel):
    """PPD sale joined to the time-nearest EPC lodgement.

    Produced by :mod:`uk_property_avm.join`. The PPD side carries every field
    a :class:`Comparable` does (the same public subset) plus the EPC-side
    attributes needed for a hedonic model:

    * ``floor_area_sqm`` — parsed ``total-floor-area`` in square metres.
    * ``energy_rating`` / ``energy_efficiency`` — EPC score (A-G / 0-100).
    * ``built_form`` — Detached / Semi-Detached / End-Terrace / Mid-Terrace /
      Enclosed End-Terrace / Enclosed Mid-Terrace.
    * ``construction_age_band`` — e.g. ``"England and Wales: 1900-1929"``.
    * ``epc_property_type`` — EPC's own ``property-type`` label (House, Flat,
      Bungalow, Maisonette, Park Home). Distinct from the PPD 1-letter code;
      useful because PPD's ``F`` collapses flats + maisonettes.

    When the EPC side is missing (no match within the tolerance window) all
    EPC fields come back ``None`` — downstream code can treat such rows as
    unenriched comparables and rely on PPD-only fallbacks.
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    price: int = Field(..., ge=0)
    transfer_date: str
    property_type: str | None = None
    tenure: str | None = None
    paon: str | None = None
    street: str | None = None
    postcode: str | None = None

    floor_area_sqm: float | None = Field(
        default=None,
        description=("Habitable floor area in square metres (from EPC ``total-floor-area``)."),
    )
    energy_rating: str | None = Field(
        default=None,
        description="Current EPC rating letter (A-G), from ``current-energy-rating``.",
    )
    energy_efficiency: int | None = Field(
        default=None,
        description=("Current EPC numeric score 0-100 (``current-energy-efficiency``)."),
    )
    built_form: str | None = Field(
        default=None,
        description="EPC ``built-form`` (e.g. 'End-Terrace', 'Semi-Detached', 'Mid-Terrace').",
    )
    construction_age_band: str | None = Field(
        default=None,
        description="EPC ``construction-age-band`` label (free text, country-prefixed).",
    )
    epc_property_type: str | None = Field(
        default=None,
        description=(
            "EPC ``property-type`` label ('House'/'Flat'/'Bungalow'/"
            "'Maisonette'/'Park home'). Complements PPD 1-letter code."
        ),
    )
    epc_inspection_date: str | None = Field(
        default=None,
        description="ISO date of the EPC lodgement selected (YYYY-MM-DD).",
    )
    epc_lmk_key: str | None = Field(
        default=None,
        description="EPC ``lmk-key`` of the selected lodgement (for traceability).",
    )
    match_quality: Literal["exact_address", "postcode_only", "none"] = Field(
        default="none",
        description=(
            "How confident the join key was:\n"
            "* exact_address - matched on normalised postcode + paon + street\n"
            "* postcode_only - only postcode matched (fallback when address normalises"
            " mismatch but postcode is common)\n"
            "* none - no EPC lodgement found within tolerance window"
        ),
    )


class ValuationEstimate(BaseModel):
    """Point estimate + lo/hi band + methodology metadata.

    The baseline model is a simple **local median + uncertainty band** keyed
    off postcode-area and property-type. It's designed to be transparent and
    monotonic: more comparables always widen or tighten the band in intuitive
    ways, and the output carries enough provenance that a user can decide
    whether the estimate is credible for their property.
    """

    model_config = ConfigDict(extra="forbid")

    estimate_gbp: int = Field(..., description="Point-estimate value in GBP pounds.")
    low_gbp: int = Field(..., description="Lower band (25th percentile of comparables).")
    high_gbp: int = Field(..., description="Upper band (75th percentile of comparables).")
    confidence: Literal["high", "medium", "low"] = Field(
        ...,
        description=(
            "Qualitative confidence derived from the comparable pool:\n"
            "* high: >= 10 same-postcode-area same-type sales\n"
            "* medium: >= 4 comparables across either relaxation\n"
            "* low: fallback to postcode-area median or national median"
        ),
    )
    comparables_used: int = Field(
        ...,
        ge=0,
        description="Number of price-paid rows that fed the median.",
    )
    basis: Literal[
        "postcode_type",
        "postcode_area",
        "area_type",
        "area_only",
        "national",
        "insufficient_data",
    ] = Field(
        ...,
        description=(
            "Which fallback tier produced the estimate:\n"
            "* postcode_type  - same postcode + same property type\n"
            "* postcode_area  - same postcode (ignoring property type)\n"
            "* area_type      - same postcode area (e.g. SW) + same type\n"
            "* area_only      - same postcode area (ignoring type)\n"
            "* national       - UK-wide fallback from the comparable pool\n"
            "* insufficient_data - no comparables at all"
        ),
    )
    postcode: str = Field(..., description="Target postcode (normalised).")
    property_type: str | None = Field(default=None)
    methodology: str = Field(
        default="rolling-median baseline (PPD)",
        description="Human-readable name of the model used.",
    )


__all__ = [
    "Comparable",
    "EnrichedComparable",
    "PropertyType",
    "ValuationEstimate",
]
