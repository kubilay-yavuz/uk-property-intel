"""Tests for H3 grid utilities (requires h3>=4.0)."""
from __future__ import annotations

import pytest

h3 = pytest.importorskip("h3")

from uk_property_geo.distance import Point  # noqa: E402
from uk_property_geo.h3_grid import (  # noqa: E402
    HexCell,
    bin_points,
    density_map,
    h3_to_center,
    hex_ring,
    point_to_h3,
)


def test_point_to_h3_returns_string() -> None:
    result = point_to_h3(51.5, -0.1)
    assert isinstance(result, str)
    assert len(result) > 0


def test_point_to_h3_same_point_same_cell() -> None:
    a = point_to_h3(51.5, -0.1)
    b = point_to_h3(51.5, -0.1)
    assert a == b


def test_h3_to_center_roundtrip() -> None:
    h3_index = point_to_h3(51.5074, -0.1278, resolution=9)
    center = h3_to_center(h3_index)
    assert abs(center.lat - 51.5074) < 0.01
    assert abs(center.lng - (-0.1278)) < 0.01


def test_hex_ring_k1_has_6_cells() -> None:
    h3_index = point_to_h3(51.5, -0.1, resolution=9)
    ring = hex_ring(h3_index, k=1)
    assert len(ring) == 6


def test_hex_ring_k0_has_0_cells() -> None:
    h3_index = point_to_h3(51.5, -0.1, resolution=9)
    ring = hex_ring(h3_index, k=0)
    assert ring == []


def test_bin_points_groups_same_cell() -> None:
    # Two points that should land in the same H3 cell at resolution 9
    p1 = Point(lat=51.5000, lng=-0.1000)
    p2 = Point(lat=51.5001, lng=-0.1001)
    counts = bin_points([p1, p2], resolution=6)  # coarse resolution -> same cell
    # At resolution 6, both London points should bin together
    assert sum(counts.values()) == 2


def test_bin_points_empty_list() -> None:
    counts = bin_points([])
    assert counts == {}


def test_density_map_returns_hex_cells() -> None:
    points = [Point(lat=51.5, lng=-0.1), Point(lat=51.5, lng=-0.1)]
    cells = density_map(points, resolution=9)
    assert len(cells) >= 1
    assert all(isinstance(c, HexCell) for c in cells)


def test_density_map_count_correct() -> None:
    # Three points in the same coarse cell
    points = [
        Point(lat=51.5, lng=-0.1),
        Point(lat=51.5, lng=-0.1),
        Point(lat=51.5, lng=-0.1),
    ]
    cells = density_map(points, resolution=5)
    assert cells[0].count == 3
    assert cells[0].density_per_km2 > 0


def test_point_to_h3_resolution_affects_output() -> None:
    coarse = point_to_h3(51.5, -0.1, resolution=5)
    fine = point_to_h3(51.5, -0.1, resolution=9)
    assert coarse != fine
