"""Rental-yield helpers for the AVM output.

The AVM proper values a dwelling by its sale price; investors care
about how that sale price converts into a *yield* once the property
is let. Rental value is the gap this module fills — it combines a
caller-supplied monthly rent with the AVM's sale valuation to
produce a standard ``gross yield / net yield / payback years``
breakdown.

We deliberately keep rent **out of band**: this module doesn't scrape
Rightmove/Zoopla for let comparables or pull ONS benchmarks. Those
signals are better sourced upstream (e.g. from a listings actor that
already has a ``rent_pcm`` field, or from a simple VOA PRMS lookup
per BRMA) and fed in explicitly. That keeps the yield computation
transparent and reproducible, and lets each caller choose how they
want to value their own rental assumption.

The core primitives are:

* :class:`YieldInputs` — monthly rent + optional cost assumptions.
* :class:`YieldBreakdown` — computed gross / net / cash-on-cash
  figures, plus provenance.
* :func:`compute_rental_yield` — the pure transformation that produces
  a breakdown from a :class:`ValuationEstimate` + rental inputs.

A ``net_yield_pct`` is only emitted when the caller supplies a
``costs_pct`` (fraction of annual rent consumed by voids, letting
fees, maintenance, insurance and management). The UK market
convention is 25-30%; we default to ``None`` so callers have to
make the assumption explicit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from uk_property_avm.models import ValuationEstimate

RentSource = Literal[
    "listing", "voa_prms", "ons_benchmark", "user_override", "unknown"
]
"""Provenance of the monthly-rent assumption that drove the yield.

* ``listing`` — scraped from a live let listing (Rightmove / Zoopla).
* ``voa_prms`` — VOA Private Rental Market Statistics (BRMA median).
* ``ons_benchmark`` — ONS ``Private rent prices`` series.
* ``user_override`` — supplied directly by the caller.
* ``unknown`` — no provenance recorded; interpret the yield with care.
"""


class YieldInputs(BaseModel):
    """Rental assumptions fed into the yield computation.

    ``monthly_rent_gbp`` is always required; ``costs_pct`` and
    ``rent_source`` are optional. The model keeps any currency /
    magnitude checks here (not in the compute function) so the
    Pydantic layer can refuse nonsensical inputs before the maths.
    """

    model_config = ConfigDict(extra="forbid")

    monthly_rent_gbp: float = Field(
        ...,
        gt=0,
        description=(
            "Expected monthly rent in GBP. Should be the gross "
            "achievable rent — the computation divides by 12 to get "
            "the implied annual rent before costs."
        ),
    )
    costs_pct: float | None = Field(
        default=None,
        ge=0,
        le=0.95,
        description=(
            "Fraction of annual rent consumed by voids, letting "
            "fees, maintenance, insurance, ground rent, and "
            "management. Typical UK BTL assumption is 0.25-0.30. "
            "Omit to return gross yield only."
        ),
    )
    rent_source: RentSource = Field(
        default="unknown",
        description=(
            "Provenance of the rent figure — drives the "
            "methodology label on the output so downstream "
            "consumers can weight results by source quality."
        ),
    )


class YieldBreakdown(BaseModel):
    """Yield metrics derived from a sale valuation + rental inputs."""

    model_config = ConfigDict(extra="forbid")

    monthly_rent_gbp: float = Field(
        ..., gt=0, description="Gross monthly rent assumption (GBP)."
    )
    annual_rent_gbp: float = Field(
        ..., gt=0, description="Gross annual rent = monthly x 12 (GBP)."
    )
    valuation_gbp: int = Field(
        ..., ge=0, description="Sale valuation the yield is quoted against (GBP)."
    )
    gross_yield_pct: float = Field(
        ...,
        ge=0,
        description=(
            "Gross annual rent ÷ valuation, expressed as a percentage. "
            "``None`` if the valuation is zero."
        ),
    )
    net_yield_pct: float | None = Field(
        default=None,
        ge=0,
        description=(
            "``gross x (1 - costs_pct)``, expressed as a percentage. "
            "Only populated when the caller supplied ``costs_pct``."
        ),
    )
    costs_pct: float | None = Field(
        default=None,
        ge=0,
        le=0.95,
        description="Cost assumption used for the net calculation (fraction).",
    )
    payback_years: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Number of years of gross rent required to recoup the "
            "valuation (= ``valuation / annual_rent``). Shorthand for "
            "``100 / gross_yield_pct``."
        ),
    )
    rent_source: RentSource = Field(
        default="unknown",
        description="Provenance tag carried through from the inputs.",
    )
    methodology: str = Field(
        default="gross-yield from caller-supplied rent",
        description=(
            "Short human-readable label used by the AVM output to "
            "explain where the yield came from."
        ),
    )


def compute_rental_yield(
    estimate: ValuationEstimate,
    inputs: YieldInputs,
) -> YieldBreakdown | None:
    """Convert a sale valuation + rental inputs into a yield breakdown.

    Returns ``None`` when the valuation is zero (``insufficient_data``
    estimates) — dividing by zero yields a meaningless number and the
    caller should surface the gap rather than a placeholder.

    The methodology string is tagged with the rent source so dataset
    consumers can filter / weight rows (e.g. "trust scraped listings
    more than user overrides").
    """

    if estimate.estimate_gbp <= 0:
        return None

    annual_rent = inputs.monthly_rent_gbp * 12
    gross_yield_pct = (annual_rent / estimate.estimate_gbp) * 100

    net_yield_pct: float | None = None
    if inputs.costs_pct is not None:
        net_yield_pct = gross_yield_pct * (1 - inputs.costs_pct)

    payback_years = estimate.estimate_gbp / annual_rent if annual_rent > 0 else None

    methodology = _methodology_for(inputs.rent_source, with_costs=inputs.costs_pct is not None)

    return YieldBreakdown(
        monthly_rent_gbp=inputs.monthly_rent_gbp,
        annual_rent_gbp=annual_rent,
        valuation_gbp=estimate.estimate_gbp,
        gross_yield_pct=round(gross_yield_pct, 3),
        net_yield_pct=round(net_yield_pct, 3) if net_yield_pct is not None else None,
        costs_pct=inputs.costs_pct,
        payback_years=round(payback_years, 2) if payback_years is not None else None,
        rent_source=inputs.rent_source,
        methodology=methodology,
    )


def _methodology_for(source: RentSource, *, with_costs: bool) -> str:
    """Build a short methodology label from the rent provenance."""

    source_label = {
        "listing": "scraped listing",
        "voa_prms": "VOA PRMS median",
        "ons_benchmark": "ONS benchmark",
        "user_override": "user override",
        "unknown": "unspecified",
    }[source]
    suffix = "gross+net (cost-adjusted)" if with_costs else "gross only"
    return f"{suffix} from {source_label}"


__all__ = [
    "RentSource",
    "YieldBreakdown",
    "YieldInputs",
    "compute_rental_yield",
]
