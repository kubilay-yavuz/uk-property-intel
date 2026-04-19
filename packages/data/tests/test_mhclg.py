"""Tests for the MHCLG household projections and Housing Delivery Test loader."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from uk_property_data.mhclg import HouseholdProjection, HousingDeliveryResult, MHCLGLookup


def test_mhclg_from_default_has_projections() -> None:
    lookup = MHCLGLookup.from_default()
    assert len(lookup._projections) == 3


def test_mhclg_projections_for_found() -> None:
    lookup = MHCLGLookup.from_default()
    proj = lookup.projections_for("E09000001")
    assert proj is not None
    assert proj.la_code == "E09000001"
    assert proj.la_name == "City of London"
    assert proj.base_year == 2018


def test_mhclg_projections_for_missing_returns_none() -> None:
    lookup = MHCLGLookup.from_default()
    assert lookup.projections_for("E09999999") is None


def test_mhclg_delivery_test_found() -> None:
    lookup = MHCLGLookup.from_default()
    results = lookup.delivery_test("E09000001")
    assert len(results) == 1
    assert results[0].year == 2023
    assert results[0].measurement == 90


def test_mhclg_delivery_test_missing_returns_empty() -> None:
    lookup = MHCLGLookup.from_default()
    assert lookup.delivery_test("E09999999") == []


def test_mhclg_from_projections_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "projections.csv"
    rows = [
        {"la_code": "E09000001", "la_name": "City of London", "base_year": 2018,
         "2023": 9500, "2028": 9600},
        {"la_code": "E09000012", "la_name": "Hackney", "base_year": 2018,
         "2023": 285000, "2028": 295000},
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lookup = MHCLGLookup.from_projections_csv(csv_file)
    proj = lookup.projections_for("E09000012")
    assert proj is not None
    assert proj.projections[2023] == 285000
    assert proj.projections[2028] == 295000


def test_mhclg_from_hdt_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "hdt.csv"
    rows = [
        {"la_code": "E09000001", "la_name": "City of London", "year": 2023,
         "net_homes_required": 50, "net_homes_delivered": 45, "measurement": 90},
        {"la_code": "E09000012", "la_name": "Hackney", "year": 2023,
         "net_homes_required": 2000, "net_homes_delivered": 1800, "measurement": 90},
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lookup = MHCLGLookup.from_hdt_csv(csv_file)
    results = lookup.delivery_test("E09000001")
    assert len(results) == 1
    assert results[0].net_homes_required == 50
    assert results[0].net_homes_delivered == 45


def test_mhclg_projections_year_keys_are_ints() -> None:
    lookup = MHCLGLookup.from_default()
    proj = lookup.projections_for("E09000033")
    assert proj is not None
    for key in proj.projections:
        assert isinstance(key, int)


def test_mhclg_delivery_test_measurement_field() -> None:
    lookup = MHCLGLookup.from_default()
    results = lookup.delivery_test("E09000033")
    assert results[0].measurement == 83


def test_mhclg_model_fields_projection() -> None:
    proj = HouseholdProjection(
        la_code="E09000001",
        la_name="City of London",
        base_year=2018,
        projections={2023: 9500, 2028: 9600},
    )
    assert proj.la_code == "E09000001"
    assert proj.projections[2023] == 9500


def test_mhclg_model_fields_hdt() -> None:
    result = HousingDeliveryResult(
        la_code="E09000001",
        la_name="City of London",
        year=2023,
        net_homes_required=50,
        net_homes_delivered=45,
        measurement=90,
    )
    assert result.measurement == 90
    assert result.net_homes_delivered == 45


def test_mhclg_from_default_has_hdt() -> None:
    lookup = MHCLGLookup.from_default()
    assert len(lookup._hdt) == 3
    assert "E09000001" in lookup._hdt
