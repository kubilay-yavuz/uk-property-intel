"""Geospatial primitives for UK property intelligence.

Public surface:

- :class:`Point`, :func:`haversine_m`, :func:`bbox_around`,
  :func:`sort_by_distance` — pure geometry with no network IO.
- :class:`OverpassClient`, :class:`AmenityHit`, :class:`AmenityCategory`,
  :func:`build_query`, :func:`parse_elements` — OSM Overpass POI search.

Everything else in the plan (OSRM/OTP routing, isochrones, H3 tiling,
polygon overlays) is deliberately deferred. Haversine + Overpass already
answer a surprising fraction of "X within Y metres of Z" questions without
any extra infrastructure.
"""

from __future__ import annotations

from uk_property_geo.distance import (
    BoundingBox,
    Point,
    bbox_around,
    haversine_m,
    sort_by_distance,
)
from uk_property_geo.overpass import (
    AmenityCategory,
    AmenityHit,
    OverpassClient,
    OverpassError,
    build_query,
    parse_elements,
)

__version__ = "0.1.0"

__all__ = [
    "AmenityCategory",
    "AmenityHit",
    "BoundingBox",
    "OverpassClient",
    "OverpassError",
    "Point",
    "__version__",
    "bbox_around",
    "build_query",
    "haversine_m",
    "parse_elements",
    "sort_by_distance",
]
