"""Tests for the IMD 2019 loader."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from uk_property_data.imd import IMDLookup, IMDRow


def test_imd_from_default_returns_10_rows() -> None:
    lookup = IMDLookup.from_default()
    assert len(lookup._index) == 10


def test_imd_by_lsoa_found() -> None:
    lookup = IMDLookup.from_default()
    row = lookup.by_lsoa("E01000001")
    assert row is not None
    assert row.lsoa_code == "E01000001"
    assert row.lsoa_name == "City of London 001A"


def test_imd_by_lsoa_missing_returns_none() -> None:
    lookup = IMDLookup.from_default()
    assert lookup.by_lsoa("E01999999") is None


def test_imd_decile_summary_found() -> None:
    lookup = IMDLookup.from_default()
    summary = lookup.decile_summary("E01000001")
    assert "imd" in summary
    assert summary["imd"] == 7
    assert all(1 <= v <= 10 for v in summary.values())


def test_imd_decile_summary_missing_returns_empty_dict() -> None:
    lookup = IMDLookup.from_default()
    assert lookup.decile_summary("E01999999") == {}


def test_imd_from_csv_loads_correctly(tmp_path: Path) -> None:
    csv_file = tmp_path / "imd.csv"
    rows = [
        {
            "lsoa_code": "E01000001", "lsoa_name": "Test LSOA A",
            "la_code": "E09000001", "la_name": "Test LA",
            "imd_rank": 100, "imd_decile": 1,
            "income_rank": 90, "employment_rank": 80,
            "education_rank": 70, "health_rank": 60,
            "crime_rank": 50, "housing_rank": 40,
            "living_environment_rank": 30,
        },
        {
            "lsoa_code": "E01000002", "lsoa_name": "Test LSOA B",
            "la_code": "E09000001", "la_name": "Test LA",
            "imd_rank": 5000, "imd_decile": 4,
            "income_rank": 4500, "employment_rank": 5000,
            "education_rank": 4000, "health_rank": 3500,
            "crime_rank": 6000, "housing_rank": 2000,
            "living_environment_rank": 5500,
        },
        {
            "lsoa_code": "E01000003", "lsoa_name": "Test LSOA C",
            "la_code": "E09000002", "la_name": "Another LA",
            "imd_rank": 28000, "imd_decile": 10,
            "income_rank": 27000, "employment_rank": 28000,
            "education_rank": 29000, "health_rank": 26000,
            "crime_rank": 30000, "housing_rank": 22000,
            "living_environment_rank": 25000,
        },
    ]
    fieldnames = list(rows[0].keys())
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lookup = IMDLookup.from_csv(csv_file)
    assert len(lookup._index) == 3
    assert lookup.by_lsoa("E01000002") is not None
    assert lookup.by_lsoa("E01000002").imd_decile == 4


def test_imd_from_csv_with_official_column_names(tmp_path: Path) -> None:
    csv_file = tmp_path / "imd_official.csv"
    rows = [
        {
            "LSOA code (2011)": "E01000001",
            "LSOA name (2011)": "Official LSOA",
            "Local Authority District code (2019)": "E09000001",
            "Local Authority District name (2019)": "Official LA",
            "Index of Multiple Deprivation (IMD) Rank": 200,
            "Index of Multiple Deprivation (IMD) Decile": 1,
            "Income Rank": 150,
            "Employment Rank": 180,
            "Education, Skills and Training Rank": 250,
            "Health Deprivation and Disability Rank": 220,
            "Crime Rank": 300,
            "Barriers to Housing and Services Rank": 400,
            "Living Environment Rank": 100,
        },
    ]
    fieldnames = list(rows[0].keys())
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lookup = IMDLookup.from_csv(csv_file)
    row = lookup.by_lsoa("E01000001")
    assert row is not None
    assert row.lsoa_name == "Official LSOA"
    assert row.imd_rank == 200
    assert row.imd_decile == 1


def test_imd_row_model_fields() -> None:
    row = IMDRow(
        lsoa_code="E01000001", lsoa_name="Test", la_code="E09000001", la_name="LA",
        imd_rank=1000, imd_decile=4, income_rank=900, employment_rank=800,
        education_rank=700, health_rank=950, crime_rank=850, housing_rank=750,
        living_environment_rank=800,
    )
    assert row.lsoa_code == "E01000001"
    assert row.imd_rank == 1000
    assert row.imd_decile == 4


def test_imd_decile_is_between_1_and_10() -> None:
    lookup = IMDLookup.from_default()
    for row in lookup._index.values():
        assert 1 <= row.imd_decile <= 10


def test_imd_multiple_lsoa_lookup() -> None:
    lookup = IMDLookup.from_default()
    codes = ["E01000001", "E01000003", "E01000008"]
    results = [lookup.by_lsoa(code) for code in codes]
    assert all(r is not None for r in results)
    assert results[0].la_name == "City of London"
    assert results[1].la_name == "Hackney"
    assert results[2].la_name == "Richmond upon Thames"


def test_imd_from_default_all_have_lsoa_codes() -> None:
    lookup = IMDLookup.from_default()
    for row in lookup._index.values():
        assert row.lsoa_code.startswith("E01")
        assert len(row.lsoa_code) == 9


def test_imd_index_keyed_by_lsoa_code() -> None:
    lookup = IMDLookup.from_default()
    for code, row in lookup._index.items():
        assert code == row.lsoa_code
