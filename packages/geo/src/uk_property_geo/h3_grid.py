"""H3 hexagonal grid utilities for UK property intelligence."""
from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from uk_property_geo.distance import Point

if TYPE_CHECKING:
    pass

try:
    import h3 as _h3

    _H3_AVAILABLE = True
except ImportError:
    _h3 = None  # type: ignore[assignment]
    _H3_AVAILABLE = False


def _require_h3() -> None:
    if not _H3_AVAILABLE:
        msg = "h3 package is required for H3 grid operations. Install with: pip install 'h3>=4.0'"
        raise ImportError(msg)


class HexCell(BaseModel):
    model_config = ConfigDict(extra="allow")
    h3_index: str
    center: Point
    count: int
    density_per_km2: float


def point_to_h3(lat: float, lng: float, *, resolution: int = 9) -> str:
    """Convert a lat/lng coordinate to an H3 cell index."""
    _require_h3()
    return _h3.latlng_to_cell(lat, lng, resolution)


def h3_to_center(h3_index: str) -> Point:
    """Return the center point of an H3 cell."""
    _require_h3()
    lat, lng = _h3.cell_to_latlng(h3_index)
    return Point(lat=lat, lng=lng)


def hex_ring(h3_index: str, *, k: int = 1) -> list[str]:
    """Return all H3 cells at grid distance k from the given cell.

    k=0 returns [] because the origin cell is not a ring neighbor of itself.
    """
    _require_h3()
    if k == 0:
        return []
    return list(_h3.grid_ring(h3_index, k))


def bin_points(points: list[Point], *, resolution: int = 9) -> dict[str, int]:
    """Bin a list of points into H3 cells, returning cell_index -> count."""
    _require_h3()
    counts: dict[str, int] = {}
    for p in points:
        cell = _h3.latlng_to_cell(p.lat, p.lng, resolution)
        counts[cell] = counts.get(cell, 0) + 1
    return counts


def density_map(points: list[Point], *, resolution: int = 9) -> list[HexCell]:
    """Return a density map as a list of HexCell objects with count and area density."""
    _require_h3()
    counts = bin_points(points, resolution=resolution)
    result = []
    for h3_index, count in counts.items():
        center_lat, center_lng = _h3.cell_to_latlng(h3_index)
        area_km2 = _h3.cell_area(h3_index, unit="km^2")
        result.append(HexCell(
            h3_index=h3_index,
            center=Point(lat=center_lat, lng=center_lng),
            count=count,
            density_per_km2=count / area_km2,
        ))
    return result
