"""Geospatial primitives for UK property intelligence.

Public surface:

- :class:`Point`, :func:`haversine_m`, :func:`bbox_around`,
  :func:`sort_by_distance` — pure geometry with no network IO.
- :class:`OverpassClient`, :class:`AmenityHit`, :class:`AmenityCategory`,
  :func:`build_query`, :func:`parse_elements` — OSM Overpass POI search.
- :class:`OverpassAmenitySource` — async callable that aggregates
  Overpass hits into ``{category: count}`` for consumers of the
  ``uk_property_avm.AmenityDensitySource`` structural protocol.
- :class:`OSRMClient` — self-hosted OSRM routing (driving/walking/cycling).
- :class:`OTPClient` — self-hosted OpenTripPlanner multimodal routing.
- H3 grid utilities: :func:`point_to_h3`, :func:`h3_to_center`,
  :func:`hex_ring`, :func:`bin_points`, :func:`density_map`.
- :class:`OverlayEngine`, :class:`OverlayLayer` — Shapely point-in-polygon.
- :class:`NaPTANLookup` — NaPTAN transport stop spatial lookup.
"""

from __future__ import annotations

from uk_property_geo.amenity_source import OverpassAmenitySource
from uk_property_geo.distance import (
    BoundingBox,
    Point,
    bbox_around,
    haversine_m,
    sort_by_distance,
)
from uk_property_geo.h3_grid import (
    HexCell,
    bin_points,
    density_map,
    h3_to_center,
    hex_ring,
    point_to_h3,
)
from uk_property_geo.naptan import (
    NaPTANLookup,
    TransportStop,
)
from uk_property_geo.osrm import (
    NearestResult,
    OSRMClient,
    Route,
    RouteStep,
    TravelTimeMatrix,
)
from uk_property_geo.otp import (
    Isochrone,
    Itinerary,
    Leg,
    OTPClient,
)
from uk_property_geo.overlays import (
    OverlayEngine,
    OverlayFeature,
    OverlayLayer,
    OverlayResult,
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
    "HexCell",
    "Isochrone",
    "Itinerary",
    "Leg",
    "NaPTANLookup",
    "NearestResult",
    "OSRMClient",
    "OTPClient",
    "OverlayEngine",
    "OverlayFeature",
    "OverlayLayer",
    "OverlayResult",
    "OverpassAmenitySource",
    "OverpassClient",
    "OverpassError",
    "Point",
    "Route",
    "RouteStep",
    "TransportStop",
    "TravelTimeMatrix",
    "__version__",
    "bbox_around",
    "bin_points",
    "build_query",
    "density_map",
    "h3_to_center",
    "haversine_m",
    "hex_ring",
    "parse_elements",
    "point_to_h3",
    "sort_by_distance",
]
