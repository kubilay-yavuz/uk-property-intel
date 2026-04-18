"""Tests for :mod:`uk_property_geo.distance`."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from uk_property_geo.distance import (
    BoundingBox,
    Point,
    bbox_around,
    haversine_m,
    sort_by_distance,
)

LONDON = (51.5074, -0.1278)
EDINBURGH = (55.9533, -3.1883)
CAMBRIDGE = (52.2053, 0.1218)
NORWICH = (52.6309, 1.2974)


class TestHaversine:
    def test_identity_is_zero(self) -> None:
        assert haversine_m(*LONDON, *LONDON) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance_london_edinburgh(self) -> None:
        # Reference: ~534 km "as the crow flies" London → Edinburgh.
        d = haversine_m(*LONDON, *EDINBURGH)
        assert 530_000 < d < 540_000

    def test_known_distance_london_cambridge(self) -> None:
        # Reference: ~80 km London → Cambridge city centre.
        d = haversine_m(*LONDON, *CAMBRIDGE)
        assert 77_000 < d < 85_000

    def test_symmetry(self) -> None:
        assert haversine_m(*LONDON, *EDINBURGH) == pytest.approx(
            haversine_m(*EDINBURGH, *LONDON), rel=1e-9
        )

    def test_antipode(self) -> None:
        # London → antipode (south of NZ) should be ~half Earth circumference.
        lat, lng = -LONDON[0], LONDON[1] + 180
        d = haversine_m(*LONDON, lat, lng)
        assert 20_000_000 < d < 20_050_000

    def test_small_distance_precision(self) -> None:
        # Two points 100 m apart on a parallel near London.
        lat, lng = LONDON
        # 1° longitude at lat 51.5 is ~69 360 m; so 100 m ≈ 0.001441°.
        d = haversine_m(lat, lng, lat, lng + 0.001441)
        assert 95 < d < 105


class TestPoint:
    def test_valid(self) -> None:
        p = Point(lat=52.2, lng=0.1)
        assert p.lat == 52.2
        assert p.lng == 0.1

    def test_out_of_range_lat(self) -> None:
        with pytest.raises(ValidationError):
            Point(lat=95.0, lng=0.0)

    def test_out_of_range_lng(self) -> None:
        with pytest.raises(ValidationError):
            Point(lat=0.0, lng=200.0)

    def test_frozen(self) -> None:
        p = Point(lat=0.0, lng=0.0)
        with pytest.raises(ValidationError):
            p.lat = 1.0  # type: ignore[misc]


class TestBboxAround:
    def test_returns_bounding_box(self) -> None:
        bb = bbox_around(LONDON[0], LONDON[1], 1000.0)
        assert isinstance(bb, BoundingBox)
        assert bb.south < LONDON[0] < bb.north
        assert bb.west < LONDON[1] < bb.east

    def test_diagonal_contains_radius(self) -> None:
        # Corner of the box should be ≥ radius from the centre.
        bb = bbox_around(LONDON[0], LONDON[1], 5000.0)
        ne_dist = haversine_m(LONDON[0], LONDON[1], bb.north, bb.east)
        assert ne_dist >= 5000.0

    def test_rejects_nonpositive_radius(self) -> None:
        with pytest.raises(ValueError):
            bbox_around(0.0, 0.0, 0.0)
        with pytest.raises(ValueError):
            bbox_around(0.0, 0.0, -100.0)

    def test_width_scales_with_cos_lat(self) -> None:
        bb_equator = bbox_around(0.0, 0.0, 1000.0)
        bb_uk = bbox_around(55.0, 0.0, 1000.0)
        width_equator = bb_equator.east - bb_equator.west
        width_uk = bb_uk.east - bb_uk.west
        # At lat 55 the box must be wider (in degrees) than at the equator.
        assert width_uk > width_equator

    def test_as_overpass_bbox(self) -> None:
        bb = bbox_around(LONDON[0], LONDON[1], 500.0)
        s = bb.as_overpass_bbox()
        parts = s.split(",")
        assert len(parts) == 4
        parsed = [float(p) for p in parts]
        assert parsed == [bb.south, bb.west, bb.north, bb.east]


class TestSortByDistance:
    def test_orders_ascending(self) -> None:
        ordered = sort_by_distance(LONDON, [EDINBURGH, CAMBRIDGE, NORWICH])
        distances = [d for _, d in ordered]
        assert distances == sorted(distances)
        # Cambridge is closest, Edinburgh furthest.
        assert ordered[0][0] == 1
        assert ordered[-1][0] == 0

    def test_preserves_original_index(self) -> None:
        ordered = sort_by_distance(LONDON, [EDINBURGH, CAMBRIDGE])
        idxs = [i for i, _ in ordered]
        assert sorted(idxs) == [0, 1]

    def test_empty_list(self) -> None:
        assert sort_by_distance(LONDON, []) == []

    def test_stable_on_ties(self) -> None:
        # Two identical points — tie broken by original index.
        ordered = sort_by_distance(LONDON, [CAMBRIDGE, CAMBRIDGE])
        assert ordered[0][0] == 0
        assert ordered[1][0] == 1
        assert math.isclose(ordered[0][1], ordered[1][1])
