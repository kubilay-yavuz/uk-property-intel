"""Async client for a self-hosted OSRM routing service."""
from __future__ import annotations

import math
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from uk_property_geo.distance import Point, haversine_m

Profile = Literal["driving", "walking", "cycling"]


class RouteStep(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    distance: float | None = None
    duration: float | None = None
    mode: str | None = None


class Route(BaseModel):
    model_config = ConfigDict(extra="allow")
    distance_m: float
    duration_s: float
    geometry: str | None = None  # encoded polyline or GeoJSON depending on OSRM config
    steps: list[RouteStep] = Field(default_factory=list)


class TravelTimeMatrix(BaseModel):
    model_config = ConfigDict(extra="allow")
    durations: list[list[float | None]]  # [origin_idx][dest_idx] -> seconds
    distances: list[list[float | None]] = Field(default_factory=list)


class NearestResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    point: Point
    distance_m: float
    name: str | None = None


class IsochronePoint(BaseModel):
    """One reachable grid centre produced by :meth:`OSRMClient.isochrone`."""

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    duration_s: float = Field(..., ge=0.0)
    distance_m: float = Field(..., ge=0.0)


class DriveIsochrone(BaseModel):
    """Driving-isochrone result for a single origin.

    We deliberately return the reachable *grid points* rather than a
    hull polygon: polygonising requires Shapely / alpha-shapes which are
    optional dependencies. Consumers that need a polygon can post-process
    :attr:`reachable_points_by_cutoff` with their own tooling.
    """

    model_config = ConfigDict(extra="forbid")

    cutoffs_min: list[int]
    grid_step_m: float
    grid_max_radius_m: float
    grid_point_count: int
    reachable_count_by_cutoff: dict[int, int]
    max_reach_distance_m_by_cutoff: dict[int, float]
    reachable_points_by_cutoff: dict[int, list[IsochronePoint]]


def radial_grid(
    lat: float,
    lng: float,
    *,
    step_m: float,
    max_radius_m: float,
) -> list[tuple[float, float]]:
    """Generate a radial grid of lat/lng points around an origin.

    Emits the origin plus concentric rings at ``step_m`` metres up to
    ``max_radius_m``. Ring size grows with radius so the arc between
    neighbours matches ``step_m`` — this gives an even point density
    without wasting a dense central cluster. Used by
    :meth:`OSRMClient.isochrone` to feed `/table` queries.
    """

    if step_m <= 0:
        raise ValueError("step_m must be positive")
    if max_radius_m <= 0:
        raise ValueError("max_radius_m must be positive")

    lat_deg_m = 111_111.0
    lng_deg_m = 111_111.0 * math.cos(math.radians(lat))
    points: list[tuple[float, float]] = [(lat, lng)]
    ring_count = max(1, round(max_radius_m / step_m))
    for i in range(1, ring_count + 1):
        radius = i * step_m
        circumference = 2 * math.pi * radius
        n = max(6, round(circumference / step_m))
        for k in range(n):
            theta = (2 * math.pi * k) / n
            dlat = (radius * math.sin(theta)) / lat_deg_m
            dlng = (radius * math.cos(theta)) / lng_deg_m if lng_deg_m != 0 else 0.0
            points.append((lat + dlat, lng + dlng))
    return points


class OSRMClient:
    """Async client for a self-hosted OSRM routing service."""

    def __init__(
        self,
        base_url: str = "http://localhost:5000",
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def route(
        self,
        origin: Point,
        destination: Point,
        *,
        profile: Profile = "driving",
    ) -> Route:
        """Compute the fastest route between two points."""
        coords = f"{origin.lng},{origin.lat};{destination.lng},{destination.lat}"
        client = await self._ensure_client()
        resp = await client.get(
            f"/route/v1/{profile}/{coords}",
            params={"overview": "full", "steps": "true"},
        )
        resp.raise_for_status()
        data = resp.json()
        osrm_route = data["routes"][0]
        steps = [
            RouteStep(
                name=s.get("name"),
                distance=s.get("distance"),
                duration=s.get("duration"),
                mode=s.get("mode"),
            )
            for leg in osrm_route.get("legs", [])
            for s in leg.get("steps", [])
        ]
        return Route(
            distance_m=osrm_route["distance"],
            duration_s=osrm_route["duration"],
            geometry=osrm_route.get("geometry"),
            steps=steps,
        )

    async def table(
        self,
        origins: list[Point],
        destinations: list[Point],
        *,
        profile: Profile = "driving",
    ) -> TravelTimeMatrix:
        """Return a travel-time matrix between origins and destinations."""
        all_coords = origins + destinations
        coords_str = ";".join(f"{p.lng},{p.lat}" for p in all_coords)
        sources = ";".join(str(i) for i in range(len(origins)))
        dests = ";".join(str(i + len(origins)) for i in range(len(destinations)))
        client = await self._ensure_client()
        resp = await client.get(
            f"/table/v1/{profile}/{coords_str}",
            params={"sources": sources, "destinations": dests, "annotations": "duration,distance"},
        )
        resp.raise_for_status()
        data = resp.json()
        return TravelTimeMatrix(
            durations=data.get("durations", []),
            distances=data.get("distances", []),
        )

    async def nearest(
        self,
        point: Point,
        *,
        profile: Profile = "driving",
        number: int = 1,
    ) -> list[NearestResult]:
        """Return the nearest road network point(s) to a coordinate."""
        coords = f"{point.lng},{point.lat}"
        client = await self._ensure_client()
        resp = await client.get(
            f"/nearest/v1/{profile}/{coords}",
            params={"number": number},
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for wp in data.get("waypoints", []):
            loc = wp.get("location", [0.0, 0.0])
            results.append(NearestResult(
                point=Point(lat=loc[1], lng=loc[0]),
                distance_m=wp.get("distance", 0.0),
                name=wp.get("name"),
            ))
        return results

    async def isochrone(
        self,
        origin: Point,
        *,
        cutoffs_min: list[int] | None = None,
        profile: Profile = "driving",
        grid_step_m: float = 500.0,
        max_radius_m: float | None = None,
    ) -> DriveIsochrone:
        """Synthesise a driving isochrone via the OSRM ``/table`` endpoint.

        Builds a radial grid around ``origin`` at ``grid_step_m`` intervals
        out to ``max_radius_m`` (defaults to ``60 * max(cutoffs_min)`` metres
        — i.e. roughly how far a car could travel at 60 km/h in the longest
        cutoff), queries the OSRM travel-time matrix, and buckets the grid
        points by whether their origin-duration falls within each cutoff.

        Returns a :class:`DriveIsochrone` with the reachable points and
        max-reach distance per cutoff. The underlying radial-grid helper is
        exposed as :func:`radial_grid` for consumers that need raw access.
        """

        cutoffs = [15, 30, 45] if cutoffs_min is None else list(cutoffs_min)
        if not cutoffs or any(c <= 0 for c in cutoffs):
            raise ValueError("cutoffs_min must contain at least one positive integer")
        if max_radius_m is None:
            max_radius_m = 60.0 * 16.667 * max(cutoffs)
            max_radius_m = max(max_radius_m, grid_step_m * 2)
        if max_radius_m <= 0:
            raise ValueError("max_radius_m must be positive")

        grid = radial_grid(
            origin.lat, origin.lng, step_m=grid_step_m, max_radius_m=max_radius_m
        )
        destinations = [Point(lat=plat, lng=plng) for plat, plng in grid]
        matrix = await self.table([origin], destinations, profile=profile)

        durations_row = matrix.durations[0] if matrix.durations else []
        reachable: dict[int, list[IsochronePoint]] = {c: [] for c in cutoffs}
        max_distance: dict[int, float] = {c: 0.0 for c in cutoffs}
        for (plat, plng), duration in zip(grid, durations_row, strict=False):
            if duration is None:
                continue
            distance_m = haversine_m(origin.lat, origin.lng, plat, plng)
            for cutoff in cutoffs:
                if duration <= cutoff * 60:
                    reachable[cutoff].append(
                        IsochronePoint(
                            lat=plat,
                            lng=plng,
                            duration_s=duration,
                            distance_m=distance_m,
                        )
                    )
                    if distance_m > max_distance[cutoff]:
                        max_distance[cutoff] = distance_m

        return DriveIsochrone(
            cutoffs_min=cutoffs,
            grid_step_m=grid_step_m,
            grid_max_radius_m=max_radius_m,
            grid_point_count=len(grid),
            reachable_count_by_cutoff={c: len(pts) for c, pts in reachable.items()},
            max_reach_distance_m_by_cutoff=max_distance,
            reachable_points_by_cutoff=reachable,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> OSRMClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
