"""Tests for NaPTANLookup."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from uk_property_geo.naptan import NaPTANLookup, TransportStop


def test_naptan_from_default_returns_20_stops() -> None:
    lookup = NaPTANLookup.from_default()
    assert len(lookup._stops) == 20


def test_naptan_by_atco_found() -> None:
    lookup = NaPTANLookup.from_default()
    stop = lookup.by_atco("9100VICTRIA")
    assert stop is not None
    assert stop.name == "London Victoria Rail Station"
    assert stop.stop_type == "rail_station"


def test_naptan_by_atco_missing_returns_none() -> None:
    lookup = NaPTANLookup.from_default()
    assert lookup.by_atco("DOESNOTEXIST") is None


def test_naptan_nearest_returns_sorted() -> None:
    lookup = NaPTANLookup.from_default()
    # Query near London Victoria (51.4952, -0.1441)
    results = lookup.nearest(51.4952, -0.1441, limit=5)
    assert len(results) > 0
    distances = [d for _, d in results]
    assert distances == sorted(distances)


def test_naptan_nearest_limit() -> None:
    lookup = NaPTANLookup.from_default()
    results = lookup.nearest(51.5, -0.1, limit=3)
    assert len(results) <= 3


def test_naptan_nearest_stop_type_filter() -> None:
    lookup = NaPTANLookup.from_default()
    results = lookup.nearest(51.5, -0.14, stop_type="metro_stop", limit=5)
    assert all(s.stop_type == "metro_stop" for s, _ in results)


def test_naptan_within_radius_all_inside() -> None:
    lookup = NaPTANLookup.from_default()
    # Victoria area — all stops within 2km should be very close
    results = lookup.within_radius(51.4952, -0.1441, radius_m=500)
    assert len(results) > 0
    assert all(d <= 500 for _, d in results)


def test_naptan_within_radius_none() -> None:
    lookup = NaPTANLookup.from_default()
    # Middle of the North Sea — no UK stops within 1km
    results = lookup.within_radius(55.0, 3.0, radius_m=1000)
    assert results == []


def test_naptan_within_radius_stop_type_filter() -> None:
    lookup = NaPTANLookup.from_default()
    results = lookup.within_radius(51.5, -0.14, radius_m=5000, stop_type="bus_stop")
    assert all(s.stop_type == "bus_stop" for s, _ in results)


def test_naptan_from_csv(tmp_path: Path) -> None:
    csv_file = tmp_path / "naptan.csv"
    rows = [
        {
            "ATCOCode": "9100VICTRIA",
            "CommonName": "Victoria Station",
            "Latitude": "51.4952",
            "Longitude": "-0.1441",
            "StopType": "RSE",
            "Indicator": "",
            "LocalityName": "Westminster",
            "ParentLocalityName": "London",
        },
        {
            "ATCOCode": "490003452W",
            "CommonName": "Oxford Street Bus Stop",
            "Latitude": "51.5133",
            "Longitude": "-0.1520",
            "StopType": "BCT",
            "Indicator": "Stop W",
            "LocalityName": "Westminster",
            "ParentLocalityName": "London",
        },
    ]
    with open(csv_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lookup = NaPTANLookup.from_csv(csv_file)
    assert len(lookup._stops) == 2
    stop = lookup.by_atco("9100VICTRIA")
    assert stop is not None
    assert stop.stop_type == "rail_station"
    bus = lookup.by_atco("490003452W")
    assert bus is not None
    assert bus.stop_type == "bus_stop"


def test_naptan_stop_types_in_demo() -> None:
    lookup = NaPTANLookup.from_default()
    types = {s.stop_type for s in lookup._stops}
    assert "rail_station" in types
    assert "bus_stop" in types
    assert "metro_stop" in types
    assert "airport" in types


def test_naptan_nearest_sorted_ascending() -> None:
    lookup = NaPTANLookup.from_default()
    results = lookup.nearest(51.5, -0.1, limit=10)
    distances = [d for _, d in results]
    for i in range(len(distances) - 1):
        assert distances[i] <= distances[i + 1]
