"""IMD 2019 loader — CSV-based, no network IO."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from uk_property_data.imd.models import IMDRow

# 10-row demo seed (public OSS, safe to bundle)
_DEMO_ROWS: list[dict[str, Any]] = [
    {
        "lsoa_code": "E01000001", "lsoa_name": "City of London 001A",
        "la_code": "E09000001", "la_name": "City of London",
        "imd_rank": 10000, "imd_decile": 7, "income_rank": 9000, "employment_rank": 8000,
        "education_rank": 7000, "health_rank": 9500, "crime_rank": 8500,
        "housing_rank": 7500, "living_environment_rank": 8000,
    },
    {
        "lsoa_code": "E01000002", "lsoa_name": "City of London 001B",
        "la_code": "E09000001", "la_name": "City of London",
        "imd_rank": 11000, "imd_decile": 8, "income_rank": 10000, "employment_rank": 9000,
        "education_rank": 8000, "health_rank": 10000, "crime_rank": 9000,
        "housing_rank": 8000, "living_environment_rank": 9000,
    },
    {
        "lsoa_code": "E01000003", "lsoa_name": "Hackney 001A",
        "la_code": "E09000012", "la_name": "Hackney",
        "imd_rank": 500, "imd_decile": 1, "income_rank": 400, "employment_rank": 300,
        "education_rank": 600, "health_rank": 450, "crime_rank": 700,
        "housing_rank": 800, "living_environment_rank": 300,
    },
    {
        "lsoa_code": "E01000004", "lsoa_name": "Hackney 001B",
        "la_code": "E09000012", "la_name": "Hackney",
        "imd_rank": 800, "imd_decile": 1, "income_rank": 700, "employment_rank": 600,
        "education_rank": 900, "health_rank": 750, "crime_rank": 1000,
        "housing_rank": 1100, "living_environment_rank": 600,
    },
    {
        "lsoa_code": "E01000005", "lsoa_name": "Westminster 001A",
        "la_code": "E09000033", "la_name": "Westminster",
        "imd_rank": 5000, "imd_decile": 4, "income_rank": 4500, "employment_rank": 5500,
        "education_rank": 3500, "health_rank": 4000, "crime_rank": 6000,
        "housing_rank": 2000, "living_environment_rank": 5500,
    },
    {
        "lsoa_code": "E01000006", "lsoa_name": "Tower Hamlets 001A",
        "la_code": "E09000030", "la_name": "Tower Hamlets",
        "imd_rank": 200, "imd_decile": 1, "income_rank": 150, "employment_rank": 180,
        "education_rank": 250, "health_rank": 220, "crime_rank": 300,
        "housing_rank": 400, "living_environment_rank": 100,
    },
    {
        "lsoa_code": "E01000007", "lsoa_name": "Islington 001A",
        "la_code": "E09000019", "la_name": "Islington",
        "imd_rank": 1500, "imd_decile": 2, "income_rank": 1400, "employment_rank": 1200,
        "education_rank": 1600, "health_rank": 1300, "crime_rank": 1700,
        "housing_rank": 1800, "living_environment_rank": 1200,
    },
    {
        "lsoa_code": "E01000008", "lsoa_name": "Richmond 001A",
        "la_code": "E09000027", "la_name": "Richmond upon Thames",
        "imd_rank": 28000, "imd_decile": 10, "income_rank": 27000, "employment_rank": 28500,
        "education_rank": 29000, "health_rank": 27500, "crime_rank": 30000,
        "housing_rank": 22000, "living_environment_rank": 26000,
    },
    {
        "lsoa_code": "E01000009", "lsoa_name": "Southwark 001A",
        "la_code": "E09000028", "la_name": "Southwark",
        "imd_rank": 3000, "imd_decile": 2, "income_rank": 2800, "employment_rank": 3200,
        "education_rank": 2600, "health_rank": 2900, "crime_rank": 3500,
        "housing_rank": 4000, "living_environment_rank": 2500,
    },
    {
        "lsoa_code": "E01000010", "lsoa_name": "Lambeth 001A",
        "la_code": "E09000022", "la_name": "Lambeth",
        "imd_rank": 2000, "imd_decile": 2, "income_rank": 1900, "employment_rank": 2100,
        "education_rank": 1800, "health_rank": 1700, "crime_rank": 2500,
        "housing_rank": 3000, "living_environment_rank": 1900,
    },
]

_COLUMN_MAP = {
    "LSOA code (2011)": "lsoa_code",
    "LSOA name (2011)": "lsoa_name",
    "Local Authority District code (2019)": "la_code",
    "Local Authority District name (2019)": "la_name",
    # Short synonyms accepted for pre-cleaned mirrors.
    "Index of Multiple Deprivation (IMD) Rank": "imd_rank",
    "Index of Multiple Deprivation (IMD) Decile": "imd_decile",
    "Income Rank": "income_rank",
    "Employment Rank": "employment_rank",
    "Education, Skills and Training Rank": "education_rank",
    "Health Deprivation and Disability Rank": "health_rank",
    "Crime Rank": "crime_rank",
    "Barriers to Housing and Services Rank": "housing_rank",
    "Living Environment Rank": "living_environment_rank",
    # Canonical GOV.UK "File 7" column names (with the
    # "(where 1 is most deprived)" suffix on ranks and the "10% of
    # LSOAs" suffix on deciles).
    "Index of Multiple Deprivation (IMD) Rank (where 1 is most deprived)": "imd_rank",
    "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)": "imd_decile",
    "Income Rank (where 1 is most deprived)": "income_rank",
    "Employment Rank (where 1 is most deprived)": "employment_rank",
    "Education, Skills and Training Rank (where 1 is most deprived)": "education_rank",
    "Health Deprivation and Disability Rank (where 1 is most deprived)": "health_rank",
    "Crime Rank (where 1 is most deprived)": "crime_rank",
    "Barriers to Housing and Services Rank (where 1 is most deprived)": "housing_rank",
    "Living Environment Rank (where 1 is most deprived)": "living_environment_rank",
}

_REQUIRED_COLS = [
    "lsoa_code", "lsoa_name", "la_code", "la_name",
    "imd_rank", "imd_decile", "income_rank", "employment_rank",
    "education_rank", "health_rank", "crime_rank",
    "housing_rank", "living_environment_rank",
]


class IMDLookup:
    """In-memory lookup for IMD 2019 LSOA deprivation data."""

    def __init__(self, data: list[IMDRow]) -> None:
        self._index: dict[str, IMDRow] = {row.lsoa_code: row for row in data}

    @classmethod
    def from_csv(cls, path: Path) -> IMDLookup:
        """Load from the official IMD 2019 CSV (GOV.UK download)."""
        df = pd.read_csv(path)
        # Rename standard column names if present; fall through if already renamed
        rename = {k: v for k, v in _COLUMN_MAP.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)
        rows = [
            IMDRow(**{col: row[col] for col in _REQUIRED_COLS})
            for _, row in df[_REQUIRED_COLS].iterrows()
        ]
        return cls(rows)

    @classmethod
    def from_default(cls) -> IMDLookup:
        """Return a tiny 10-row demo instance (bundled public seed)."""
        return cls([IMDRow(**r) for r in _DEMO_ROWS])

    def by_lsoa(self, lsoa_code: str) -> IMDRow | None:
        """Return the IMD row for a LSOA code, or None if not found."""
        return self._index.get(lsoa_code)

    def decile_summary(self, lsoa_code: str) -> dict[str, int]:
        """Return a summary dict of all domain deciles for a LSOA."""
        row = self._index.get(lsoa_code)
        if row is None:
            return {}
        return {
            "imd": row.imd_decile,
            "income": (row.income_rank - 1) // 3285 + 1,
            "employment": (row.employment_rank - 1) // 3285 + 1,
            "education": (row.education_rank - 1) // 3285 + 1,
            "health": (row.health_rank - 1) // 3285 + 1,
            "crime": (row.crime_rank - 1) // 3285 + 1,
            "housing": (row.housing_rank - 1) // 3285 + 1,
            "living_environment": (row.living_environment_rank - 1) // 3285 + 1,
        }
