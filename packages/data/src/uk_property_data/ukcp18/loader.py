"""UKCP18 regional climate projection loader — CSV-based, no network IO."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from uk_property_data.ukcp18.models import ClimateProjection

# Demo seed: 2 regions × 1 scenario × 2 epochs = 4 rows
_DEMO_ROWS: list[dict[str, Any]] = [
    {
        "region": "London",
        "scenario": "rcp85",
        "epoch": 2050,
        "temp_change_c": 2.5,
        "precip_change_pct": -6.0,
        "summer_precip_change_pct": -15.0,
        "winter_precip_change_pct": 5.0,
        "sea_level_rise_cm": None,
    },
    {
        "region": "London",
        "scenario": "rcp85",
        "epoch": 2070,
        "temp_change_c": 3.8,
        "precip_change_pct": -8.0,
        "summer_precip_change_pct": -20.0,
        "winter_precip_change_pct": 7.0,
        "sea_level_rise_cm": None,
    },
    {
        "region": "South East",
        "scenario": "rcp85",
        "epoch": 2050,
        "temp_change_c": 2.3,
        "precip_change_pct": -5.0,
        "summer_precip_change_pct": -14.0,
        "winter_precip_change_pct": 4.0,
        "sea_level_rise_cm": 15.0,
    },
    {
        "region": "South East",
        "scenario": "rcp85",
        "epoch": 2070,
        "temp_change_c": 3.5,
        "precip_change_pct": -7.0,
        "summer_precip_change_pct": -18.0,
        "winter_precip_change_pct": 6.0,
        "sea_level_rise_cm": 22.0,
    },
]


class UKCP18Lookup:
    """In-memory lookup for UKCP18 regional climate projections."""

    def __init__(self, data: list[ClimateProjection]) -> None:
        # Index by (region, scenario, epoch) tuple
        self._data = data
        self._index: dict[tuple[str, str, int], ClimateProjection] = {
            (row.region, row.scenario, row.epoch): row for row in data
        }

    @classmethod
    def from_csv(cls, path: Path) -> UKCP18Lookup:
        """Load UKCP18 projections from CSV.

        Expected columns: region, scenario, epoch, temp_change_c, precip_change_pct,
        summer_precip_change_pct, winter_precip_change_pct, sea_level_rise_cm.
        """
        df = pd.read_csv(path)
        rows = []
        for _, row in df.iterrows():
            sea_level = None if pd.isna(row.get("sea_level_rise_cm")) else float(row["sea_level_rise_cm"])
            rows.append(ClimateProjection(
                region=str(row["region"]),
                scenario=str(row["scenario"]),
                epoch=int(row["epoch"]),
                temp_change_c=float(row["temp_change_c"]),
                precip_change_pct=float(row["precip_change_pct"]),
                summer_precip_change_pct=float(row["summer_precip_change_pct"]),
                winter_precip_change_pct=float(row["winter_precip_change_pct"]),
                sea_level_rise_cm=sea_level,
            ))
        return cls(rows)

    @classmethod
    def from_default(cls) -> UKCP18Lookup:
        """Return a demo instance with 4 rows (2 regions × 1 scenario × 2 epochs)."""
        rows = [ClimateProjection(**r) for r in _DEMO_ROWS]
        return cls(rows)

    def projection(
        self,
        region: str,
        *,
        scenario: str = "rcp85",
        epoch: int = 2050,
    ) -> ClimateProjection | None:
        """Return the climate projection for a region/scenario/epoch combination."""
        return self._index.get((region, scenario, epoch))

    def regions(self) -> list[str]:
        """Return a deduplicated, sorted list of all region names."""
        seen: set[str] = set()
        result: list[str] = []
        for row in self._data:
            if row.region not in seen:
                seen.add(row.region)
                result.append(row.region)
        return sorted(result)
