"""Tests for the UKCP18 climate projection loader."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from uk_property_data.ukcp18 import ClimateProjection, UKCP18Lookup


def test_ukcp18_from_default_has_regions() -> None:
    lookup = UKCP18Lookup.from_default()
    regions = lookup.regions()
    assert "London" in regions
    assert "South East" in regions


def test_ukcp18_projection_found() -> None:
    lookup = UKCP18Lookup.from_default()
    proj = lookup.projection("London", scenario="rcp85", epoch=2050)
    assert proj is not None
    assert proj.region == "London"
    assert proj.temp_change_c == 2.5
    assert proj.precip_change_pct == -6.0


def test_ukcp18_projection_missing_returns_none() -> None:
    lookup = UKCP18Lookup.from_default()
    assert lookup.projection("North West", scenario="rcp85", epoch=2050) is None


def test_ukcp18_regions_list() -> None:
    lookup = UKCP18Lookup.from_default()
    regions = lookup.regions()
    assert isinstance(regions, list)
    assert len(regions) == 2
    assert regions == sorted(regions)


def test_ukcp18_sea_level_rise_optional() -> None:
    lookup = UKCP18Lookup.from_default()
    london = lookup.projection("London", scenario="rcp85", epoch=2050)
    south_east = lookup.projection("South East", scenario="rcp85", epoch=2050)
    assert london is not None
    assert london.sea_level_rise_cm is None
    assert south_east is not None
    assert south_east.sea_level_rise_cm == 15.0


def test_ukcp18_from_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "ukcp18.csv"
    rows = [
        {
            "region": "Yorkshire",
            "scenario": "rcp45",
            "epoch": 2030,
            "temp_change_c": 1.2,
            "precip_change_pct": -3.0,
            "summer_precip_change_pct": -8.0,
            "winter_precip_change_pct": 2.0,
            "sea_level_rise_cm": "",
        },
        {
            "region": "Yorkshire",
            "scenario": "rcp85",
            "epoch": 2050,
            "temp_change_c": 2.1,
            "precip_change_pct": -5.0,
            "summer_precip_change_pct": -12.0,
            "winter_precip_change_pct": 4.0,
            "sea_level_rise_cm": "",
        },
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lookup = UKCP18Lookup.from_csv(csv_file)
    proj = lookup.projection("Yorkshire", scenario="rcp85", epoch=2050)
    assert proj is not None
    assert proj.temp_change_c == 2.1


def test_ukcp18_scenario_filter() -> None:
    lookup = UKCP18Lookup.from_default()
    # Only rcp85 in demo, rcp26 should return None
    assert lookup.projection("London", scenario="rcp26", epoch=2050) is None
    assert lookup.projection("London", scenario="rcp85", epoch=2050) is not None


def test_ukcp18_epoch_filter() -> None:
    lookup = UKCP18Lookup.from_default()
    assert lookup.projection("London", scenario="rcp85", epoch=2050) is not None
    assert lookup.projection("London", scenario="rcp85", epoch=2070) is not None
    assert lookup.projection("London", scenario="rcp85", epoch=2090) is None


def test_ukcp18_model_literal_validation() -> None:
    proj = ClimateProjection(
        region="London",
        scenario="rcp85",
        epoch=2050,
        temp_change_c=2.5,
        precip_change_pct=-6.0,
        summer_precip_change_pct=-15.0,
        winter_precip_change_pct=5.0,
    )
    assert proj.scenario == "rcp85"
    assert proj.epoch == 2050
    assert proj.sea_level_rise_cm is None


def test_ukcp18_region_case_sensitive() -> None:
    lookup = UKCP18Lookup.from_default()
    assert lookup.projection("london") is None  # lowercase should not match
    assert lookup.projection("London") is not None
