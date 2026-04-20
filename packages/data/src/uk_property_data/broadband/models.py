"""Pydantic models for Ofcom Fixed Broadband coverage rows.

Column names mirror the Ofcom Connected Nations 2024 postcode CSV
schema (``/siteassets/.../202407-fixed-coverage-postcodes-r01.zip``).
We keep the original ``% of premises ...`` columns as short, safe
Python identifiers so downstream JSON dumps are ergonomic.

One row describes one UK postcode. All percentages are 0-100 inclusive.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BroadbandCoverage(BaseModel):
    """Fixed-broadband coverage for one UK postcode.

    Source: Ofcom Connected Nations postcode-level CSV (published
    annually, Open Government Licence). Percentages are share of
    premises in the postcode; e.g. ``ufbb_pct=94.1`` means 94.1 % of
    premises can receive ultrafast broadband (300 Mbit/s+).

    The row is typically derived from operator-level data returned to
    Ofcom by fixed broadband providers; it's retrospective by ~6
    months relative to the publication date.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    postcode: str = Field(
        description="UK postcode with a space (e.g. ``SW1A 1AA``).",
    )
    postcode_area: str = Field(
        description=(
            "Leading alphabetical postcode area (``SW``, ``CB`` …). "
            "Useful for grouping when joining to other Ofcom products."
        ),
    )

    # Speed-band coverage — share of premises achievable.
    pct_premises_below_2m: float = Field(
        description="% of premises unable to receive 2 Mbit/s download.",
    )
    pct_premises_below_5m: float = Field(
        description="% of premises unable to receive 5 Mbit/s download.",
    )
    pct_premises_below_10m: float = Field(
        description="% of premises unable to receive 10 Mbit/s download.",
    )
    pct_premises_below_30m: float = Field(
        description="% of premises unable to receive 30 Mbit/s download.",
    )
    pct_premises_below_uso: float = Field(
        description=(
            "% of premises below the Universal Service Obligation "
            "(10 Mbit/s down + 1 Mbit/s up)."
        ),
    )

    # Headline availability.
    sfbb_pct: float = Field(
        description=(
            "Superfast Fixed Broadband availability (≥30 Mbit/s) as "
            "% of premises."
        ),
    )
    ufbb_pct: float = Field(
        description=(
            "Ultrafast Fixed Broadband availability (≥300 Mbit/s) as "
            "% of premises."
        ),
    )
    ufbb_100m_pct: float = Field(
        description=(
            "Ultrafast availability at 100 Mbit/s threshold "
            "(Ofcom's secondary UFBB band) as % of premises."
        ),
    )
    gigabit_pct: float = Field(
        description="Gigabit-capable availability as % of premises.",
    )
    nga_pct: float = Field(
        description=(
            "Next Generation Access availability (FTTC/FTTP/cable "
            "combined) as % of premises."
        ),
    )
    fwa_decent_pct: float = Field(
        description=(
            "% of premises able to receive decent broadband from "
            "Fixed Wireless Access (useful for rural coverage gaps)."
        ),
    )


__all__ = ["BroadbandCoverage"]
