"""Distance primitives for UK property geospatial queries.

Pure-Python, no external services, no I/O. Fast enough for thousands of
point-pair calls per second without a spatial index.

Exposes:

- :func:`haversine_m` — great-circle distance in metres between two lat/lng points.
- :func:`bbox_around` — latitude/longitude bounding box around a centre at a
  given radius in metres. Useful for Overpass queries and for narrowing a
  pre-filter before a full distance sort.
- :class:`Point` — minimal lat/lng Pydantic model, used anywhere the agent
  emits or consumes coordinates.

All inputs are validated — pass ``lat`` ∈ [-90, 90] and ``lng`` ∈ [-180, 180]
or the models raise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

_EARTH_RADIUS_M = 6_371_008.8


class Point(BaseModel):
    """Validated lat/lng pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned lat/lng bounding box (south/west/north/east)."""

    south: float
    west: float
    north: float
    east: float

    def as_overpass_bbox(self) -> str:
        """Format as ``south,west,north,east`` for OSM Overpass ``bbox`` clauses."""

        return f"{self.south},{self.west},{self.north},{self.east}"


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points in metres.

    Implements the haversine formula with the mean Earth radius
    (6 371 008.8 m). Error versus a geodesic calculation is <0.5% for the
    short distances we care about in property queries (<100 km).
    """

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return _EARTH_RADIUS_M * c


def bbox_around(lat: float, lng: float, radius_m: float) -> BoundingBox:
    """Small lat/lng bounding box around ``(lat, lng)`` of ``radius_m``.

    The box is an over-approximation — it's the smallest axis-aligned box
    that contains the circle of radius ``radius_m``. Use it to pre-filter
    candidates cheaply, then apply :func:`haversine_m` for the final sort.

    Breaks down near the poles; UK property use-case is nowhere near, so
    we ignore that edge case.
    """

    if radius_m <= 0:
        raise ValueError("radius_m must be positive")

    lat_delta = math.degrees(radius_m / _EARTH_RADIUS_M)
    cos_lat = math.cos(math.radians(lat))
    lng_delta = (
        180.0 if cos_lat <= 1e-6 else math.degrees(radius_m / (_EARTH_RADIUS_M * cos_lat))
    )
    return BoundingBox(
        south=max(-90.0, lat - lat_delta),
        west=max(-180.0, lng - lng_delta),
        north=min(90.0, lat + lat_delta),
        east=min(180.0, lng + lng_delta),
    )


def sort_by_distance(
    origin: tuple[float, float],
    points: list[tuple[float, float]],
) -> list[tuple[int, float]]:
    """Return ``[(original_index, distance_m), ...]`` sorted ascending.

    Returning the original index lets callers pair back to richer records
    without a separate key. Ties are broken by index (stable sort).
    """

    olat, olng = origin
    out = [(idx, haversine_m(olat, olng, lat, lng)) for idx, (lat, lng) in enumerate(points)]
    out.sort(key=lambda row: (row[1], row[0]))
    return out
