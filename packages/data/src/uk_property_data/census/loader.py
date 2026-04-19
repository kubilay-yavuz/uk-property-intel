"""Census 2021 bulk CSV loader — one table per instance."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from uk_property_data.census.models import CensusTable

# TS001 demo seed (5 rows, public ONS data)
_DEMO_TABLE_ID = "TS001"
_DEMO_ROWS: list[dict[str, Any]] = [
    {
        "geography_code": "E09000001",
        "geography_name": "City of London",
        "Total: All usual residents": 9401.0,
    },
    {
        "geography_code": "E09000012",
        "geography_name": "Hackney",
        "Total: All usual residents": 281481.0,
    },
    {
        "geography_code": "E09000019",
        "geography_name": "Islington",
        "Total: All usual residents": 245848.0,
    },
    {
        "geography_code": "E09000028",
        "geography_name": "Southwark",
        "Total: All usual residents": 318830.0,
    },
    {
        "geography_code": "E09000033",
        "geography_name": "Westminster",
        "Total: All usual residents": 261336.0,
    },
]


class CensusLookup:
    """In-memory lookup for a single Census 2021 bulk table."""

    def __init__(self, data: list[CensusTable], *, table_id: str) -> None:
        self._table_id = table_id
        self._index: dict[str, CensusTable] = {row.geography_code: row for row in data}

    @classmethod
    def from_csv(cls, path: Path, *, table_id: str) -> CensusLookup:
        """Load a Census 2021 bulk CSV.

        Expected columns: geography code, geography name, then one column per category.
        The first two columns are treated as geography code and name respectively.
        """
        df = pd.read_csv(path)
        cols = list(df.columns)
        # Detect geography columns (first two)
        geo_code_col = cols[0]
        geo_name_col = cols[1]
        category_cols = cols[2:]

        rows = []
        for _, row in df.iterrows():
            categories = {col: float(row[col]) for col in category_cols if pd.notna(row[col])}
            rows.append(CensusTable(
                table_id=table_id,
                geography_code=str(row[geo_code_col]),
                geography_name=str(row[geo_name_col]),
                categories=categories,
            ))
        return cls(rows, table_id=table_id)

    @classmethod
    def from_default(cls) -> CensusLookup:
        """Return a TS001 demo instance with 5 rows."""
        rows = []
        for r in _DEMO_ROWS:
            cats = {k: v for k, v in r.items() if k not in ("geography_code", "geography_name")}
            rows.append(CensusTable(
                table_id=_DEMO_TABLE_ID,
                geography_code=r["geography_code"],
                geography_name=r["geography_name"],
                categories={k: float(v) for k, v in cats.items()},
            ))
        return cls(rows, table_id=_DEMO_TABLE_ID)

    def by_geography(self, geo_code: str) -> CensusTable | None:
        return self._index.get(geo_code)

    def table_id(self) -> str:
        return self._table_id
