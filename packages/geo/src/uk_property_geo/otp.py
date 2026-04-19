"""Async client for a self-hosted OpenTripPlanner (OTP) routing service."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from uk_property_geo.distance import Point

TransitMode = Literal["TRANSIT,WALK", "BUS,WALK", "RAIL,WALK", "WALK", "BICYCLE"]


class Leg(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    mode: str | None = None
    from_name: str | None = Field(default=None, alias="fromName")
    to_name: str | None = Field(default=None, alias="toName")
    duration_s: float | None = None
    route_name: str | None = None
    agency: str | None = None


class Itinerary(BaseModel):
    model_config = ConfigDict(extra="allow")
    duration_s: float | None = None
    walk_distance_m: float | None = None
    transit_time_s: float | None = None
    transfers: int | None = None
    legs: list[Leg] = Field(default_factory=list)


class Isochrone(BaseModel):
    model_config = ConfigDict(extra="allow")
    cutoff_minutes: int
    geometry: dict[str, Any] | None = None  # GeoJSON geometry


class OTPClient:
    """Async client for a self-hosted OpenTripPlanner routing service."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def plan_journey(
        self,
        origin: Point,
        destination: Point,
        *,
        mode: TransitMode = "TRANSIT,WALK",
        depart_at: datetime | None = None,
    ) -> list[Itinerary]:
        """Plan one or more journeys from origin to destination."""
        params: dict[str, Any] = {
            "fromPlace": f"{origin.lat},{origin.lng}",
            "toPlace": f"{destination.lat},{destination.lng}",
            "mode": mode,
            "numItineraries": 3,
        }
        if depart_at is not None:
            params["date"] = depart_at.strftime("%Y-%m-%d")
            params["time"] = depart_at.strftime("%H:%M:%S")
        client = await self._ensure_client()
        resp = await client.get("/otp/routers/default/plan", params=params)
        resp.raise_for_status()
        data = resp.json()
        plan = data.get("plan", {})
        itineraries = []
        for itin in plan.get("itineraries", []):
            legs = [
                Leg(
                    mode=leg.get("mode"),
                    fromName=leg.get("from", {}).get("name"),
                    toName=leg.get("to", {}).get("name"),
                    duration_s=leg.get("duration"),
                    route_name=leg.get("routeShortName") or leg.get("route"),
                    agency=leg.get("agencyName"),
                )
                for leg in itin.get("legs", [])
            ]
            itineraries.append(Itinerary(
                duration_s=itin.get("duration"),
                walk_distance_m=itin.get("walkDistance"),
                transit_time_s=itin.get("transitTime"),
                transfers=itin.get("transfers"),
                legs=legs,
            ))
        return itineraries

    async def isochrone(
        self,
        origin: Point,
        *,
        cutoff_minutes: list[int] | None = None,
        mode: TransitMode = "TRANSIT,WALK",
    ) -> list[Isochrone]:
        """Return isochrone polygons for multiple travel time cutoffs."""
        if cutoff_minutes is None:
            cutoff_minutes = [15, 30, 45, 60]
        params: dict[str, Any] = {
            "fromPlace": f"{origin.lat},{origin.lng}",
            "mode": mode,
            "cutoffSec": [c * 60 for c in cutoff_minutes],
        }
        client = await self._ensure_client()
        resp = await client.get("/otp/routers/default/isochrone", params=params)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        result = []
        for cutoff, feature in zip(cutoff_minutes, features, strict=False):
            result.append(Isochrone(
                cutoff_minutes=cutoff,
                geometry=feature.get("geometry"),
            ))
        return result

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> OTPClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()
