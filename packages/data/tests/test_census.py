"""Tests for the Census 2021 loader."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from uk_property_data.census import CensusLookup, CensusTable


def test_census_from_default_returns_5_rows() -> None:
    lookup = CensusLookup.from_default()
    assert len(lookup._index) == 5


def test_census_by_geography_found() -> None:
    lookup = CensusLookup.from_default()
    row = lookup.by_geography("E09000001")
    assert row is not None
    assert row.geography_code == "E09000001"
    assert row.geography_name == "City of London"


def test_census_by_geography_missing_returns_none() -> None:
    lookup = CensusLookup.from_default()
    assert lookup.by_geography("E09999999") is None


def test_census_table_id() -> None:
    lookup = CensusLookup.from_default()
    assert lookup.table_id() == "TS001"


def test_census_categories_dict_populated() -> None:
    lookup = CensusLookup.from_default()
    row = lookup.by_geography("E09000012")
    assert row is not None
    assert len(row.categories) > 0
    assert "Total: All usual residents" in row.categories
    assert row.categories["Total: All usual residents"] == 281481.0


def test_census_from_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "census.csv"
    rows = [
        {"geography code": "E09000001", "geography name": "City of London", "Pop": 9401},
        {"geography code": "E09000012", "geography name": "Hackney", "Pop": 281481},
        {"geography code": "E09000019", "geography name": "Islington", "Pop": 245848},
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lookup = CensusLookup.from_csv(csv_file, table_id="TS001")
    assert len(lookup._index) == 3
    row = lookup.by_geography("E09000012")
    assert row is not None
    assert row.categories["Pop"] == 281481.0


def test_census_from_csv_multi_category(tmp_path: Path) -> None:
    csv_file = tmp_path / "census_multi.csv"
    rows = [
        {"code": "E01", "name": "Area A", "Cat1": 100, "Cat2": 200, "Cat3": 50},
        {"code": "E02", "name": "Area B", "Cat1": 150, "Cat2": 250, "Cat3": 75},
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lookup = CensusLookup.from_csv(csv_file, table_id="TS003")
    row = lookup.by_geography("E01")
    assert row is not None
    assert len(row.categories) == 3
    assert row.categories["Cat1"] == 100.0
    assert row.categories["Cat2"] == 200.0


def test_census_model_fields() -> None:
    table = CensusTable(
        table_id="TS001",
        geography_code="E09000001",
        geography_name="City of London",
        categories={"Total": 9401.0},
    )
    assert table.table_id == "TS001"
    assert table.geography_code == "E09000001"
    assert table.categories["Total"] == 9401.0


def test_census_all_default_have_codes() -> None:
    lookup = CensusLookup.from_default()
    for code, row in lookup._index.items():
        assert code == row.geography_code
        assert row.geography_code.startswith("E09")


def test_census_geography_name_preserved() -> None:
    lookup = CensusLookup.from_default()
    row = lookup.by_geography("E09000033")
    assert row is not None
    assert row.geography_name == "Westminster"
