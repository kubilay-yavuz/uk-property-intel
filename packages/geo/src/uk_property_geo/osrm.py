"""Async client for a self-hosted OSRM routing service."""
from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from uk_property_geo.distance import Point

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

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> OSRMClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
