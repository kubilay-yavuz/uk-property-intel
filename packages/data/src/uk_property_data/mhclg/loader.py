"""MHCLG household projections and Housing Delivery Test loader — CSV-based, no network IO."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from uk_property_data.mhclg.models import HouseholdProjection, HousingDeliveryResult

# Demo projection seed: 3 LAs
_DEMO_PROJECTIONS: list[dict[str, Any]] = [
    {
        "la_code": "E09000001",
        "la_name": "City of London",
        "base_year": 2018,
        "projections": {2023: 9500, 2028: 9600, 2033: 9700},
    },
    {
        "la_code": "E09000012",
        "la_name": "Hackney",
        "base_year": 2018,
        "projections": {2023: 285000, 2028: 295000, 2033: 305000},
    },
    {
        "la_code": "E09000033",
        "la_name": "Westminster",
        "base_year": 2018,
        "projections": {2023: 265000, 2028: 270000, 2033: 275000},
    },
]

# Demo HDT seed: 3 rows
_DEMO_HDT: list[dict[str, Any]] = [
    {
        "la_code": "E09000001",
        "la_name": "City of London",
        "year": 2023,
        "net_homes_required": 50,
        "net_homes_delivered": 45,
        "measurement": 90,
    },
    {
        "la_code": "E09000012",
        "la_name": "Hackney",
        "year": 2023,
        "net_homes_required": 2000,
        "net_homes_delivered": 1800,
        "measurement": 90,
    },
    {
        "la_code": "E09000033",
        "la_name": "Westminster",
        "year": 2023,
        "net_homes_required": 3000,
        "net_homes_delivered": 2500,
        "measurement": 83,
    },
]


class MHCLGLookup:
    """In-memory lookup for MHCLG household projections and Housing Delivery Test data."""

    def __init__(
        self,
        projections: list[HouseholdProjection],
        hdt: list[HousingDeliveryResult],
    ) -> None:
        self._projections: dict[str, HouseholdProjection] = {p.la_code: p for p in projections}
        self._hdt: dict[str, list[HousingDeliveryResult]] = {}
        for result in hdt:
            self._hdt.setdefault(result.la_code, []).append(result)

    @classmethod
    def from_projections_csv(cls, path: Path) -> MHCLGLookup:
        """Load household projections from CSV.

        Expected columns: la_code, la_name, base_year, then integer year columns.
        """
        df = pd.read_csv(path)
        projections = []
        for _, row in df.iterrows():
            year_cols = {
                int(col): int(row[col])
                for col in df.columns
                if col not in ("la_code", "la_name", "base_year")
                and str(col).isdigit()
            }
            projections.append(HouseholdProjection(
                la_code=str(row["la_code"]),
                la_name=str(row["la_name"]),
                base_year=int(row["base_year"]),
                projections=year_cols,
            ))
        return cls(projections, hdt=[])

    @classmethod
    def from_hdt_csv(cls, path: Path) -> MHCLGLookup:
        """Load Housing Delivery Test results from CSV.

        Expected columns: la_code, la_name, year, net_homes_required,
        net_homes_delivered, measurement.
        """
        df = pd.read_csv(path)
        hdt = []
        for _, row in df.iterrows():
            hdt.append(HousingDeliveryResult(
                la_code=str(row["la_code"]),
                la_name=str(row["la_name"]),
                year=int(row["year"]),
                net_homes_required=int(row["net_homes_required"]),
                net_homes_delivered=int(row["net_homes_delivered"]),
                measurement=int(row["measurement"]),
            ))
        return cls(projections=[], hdt=hdt)

    @classmethod
    def from_default(cls) -> MHCLGLookup:
        """Return demo instance with 3 projections and 3 HDT rows."""
        projections = [
            HouseholdProjection(
                la_code=p["la_code"],
                la_name=p["la_name"],
                base_year=p["base_year"],
                projections=p["projections"],
            )
            for p in _DEMO_PROJECTIONS
        ]
        hdt = [
            HousingDeliveryResult(
                la_code=h["la_code"],
                la_name=h["la_name"],
                year=h["year"],
                net_homes_required=h["net_homes_required"],
                net_homes_delivered=h["net_homes_delivered"],
                measurement=h["measurement"],
            )
            for h in _DEMO_HDT
        ]
        return cls(projections=projections, hdt=hdt)

    def projections_for(self, la_code: str) -> HouseholdProjection | None:
        """Return household projections for a local authority, or None if not found."""
        return self._projections.get(la_code)

    def delivery_test(self, la_code: str) -> list[HousingDeliveryResult]:
        """Return Housing Delivery Test results for a local authority."""
        return self._hdt.get(la_code, [])
