"""Async client for BGS geology hazard ArcGIS REST API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.bgs.models import GeohazardAssessment, HazardRating

# BGS withdrew the ``BGS_Hazards/GeoHazardsEngland`` ArcGIS REST service
# from public access in 2024; the remaining free MapServer
# (``GeoIndex_Onshore/hazards/MapServer``) exposes only four layers
# (earthquakes, historical earthquakes, landslides, monitoring stations)
# and none of the subsidence / shrink-swell / ground-dissolution /
# compressible / collapsible / running-sand ratings we previously
# queried. Those layers now only ship as WMS tiles (raster imagery) or
# via BGS's paid GeoClimate / BGS Data subscription feed.
#
# Until we wire up a replacement source (GeoClimate Basic WMS with a
# bbox -> thumbnail bucketing shim, or a licensed BGS Data feed), this
# module raises a clear migration error so downstream consumers
# (the climate-risk actor, the agent) surface the state transparently
# via ``partial_errors`` rather than returning silently empty grades.
_BASE_URL = "https://map.bgs.ac.uk/arcgis/rest/services/"

_BGS_MIGRATION_MESSAGE = (
    "BGS_Hazards/GeoHazardsEngland REST layers withdrawn; the six "
    "subsidence hazards now ship only via the paid BGS data feed or "
    "GeoClimate Basic WMS tiles (image-only). A replacement client is "
    "tracked in the API backlog."
)

_LAYER_SHRINK_SWELL = "BGS_Hazards/GeoHazardsEngland/MapServer/0"
_LAYER_GROUND_DISSOLUTION = "BGS_Hazards/GeoHazardsEngland/MapServer/1"
_LAYER_COMPRESSIBLE_GROUND = "BGS_Hazards/GeoHazardsEngland/MapServer/2"
_LAYER_LANDSLIDE = "BGS_Hazards/GeoHazardsEngland/MapServer/3"
_LAYER_COLLAPSIBLE_DEPOSITS = "BGS_Hazards/GeoHazardsEngland/MapServer/4"
_LAYER_RUNNING_SAND = "BGS_Hazards/GeoHazardsEngland/MapServer/5"

# Grades C, D, E indicate meaningful subsidence risk
_SUBSIDENCE_GRADES = {"C", "D", "E"}


def _arcgis_params(lat: float, lng: float) -> dict[str, Any]:
    return {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "f": "json",
    }


def _parse_hazard(data: dict[str, Any], hazard_type: str) -> HazardRating:
    features = data.get("features") or []
    if not features:
        return HazardRating(hazard_type=hazard_type)
    attrs = features[0].get("attributes") or {}
    return HazardRating(
        hazard_type=hazard_type,
        grade=attrs.get("GRADE") or attrs.get("grade"),
        description=attrs.get("DESCRIPTION") or attrs.get("description"),
        properties=attrs,
    )


class BGSClient(BaseAPIClient):
    """Client for BGS geology hazard at https://map.bgs.ac.uk/arcgis/rest/services/."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url=_BASE_URL,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def shrink_swell(self, lat: float, lng: float) -> HazardRating:
        """Return shrink-swell hazard rating at the given coordinate.

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        data = await self._get(  # pragma: no cover - legacy path
            f"{_LAYER_SHRINK_SWELL}/query", params=_arcgis_params(lat, lng)
        )
        return _parse_hazard(data, "shrink_swell")

    async def ground_dissolution(self, lat: float, lng: float) -> HazardRating:
        """Return ground dissolution hazard rating at the given coordinate.

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        data = await self._get(  # pragma: no cover - legacy path
            f"{_LAYER_GROUND_DISSOLUTION}/query", params=_arcgis_params(lat, lng)
        )
        return _parse_hazard(data, "ground_dissolution")

    async def compressible_ground(self, lat: float, lng: float) -> HazardRating:
        """Return compressible ground hazard rating at the given coordinate.

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        data = await self._get(  # pragma: no cover - legacy path
            f"{_LAYER_COMPRESSIBLE_GROUND}/query", params=_arcgis_params(lat, lng)
        )
        return _parse_hazard(data, "compressible_ground")

    async def landslide(self, lat: float, lng: float) -> HazardRating:
        """Return landslide hazard rating at the given coordinate.

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        data = await self._get(  # pragma: no cover - legacy path
            f"{_LAYER_LANDSLIDE}/query", params=_arcgis_params(lat, lng)
        )
        return _parse_hazard(data, "landslide")

    async def collapsible_deposits(self, lat: float, lng: float) -> HazardRating:
        """Return collapsible deposits hazard rating at the given coordinate.

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        data = await self._get(  # pragma: no cover - legacy path
            f"{_LAYER_COLLAPSIBLE_DEPOSITS}/query", params=_arcgis_params(lat, lng)
        )
        return _parse_hazard(data, "collapsible_deposits")

    async def running_sand(self, lat: float, lng: float) -> HazardRating:
        """Return running sand hazard rating at the given coordinate.

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        data = await self._get(  # pragma: no cover - legacy path
            f"{_LAYER_RUNNING_SAND}/query", params=_arcgis_params(lat, lng)
        )
        return _parse_hazard(data, "running_sand")

    async def geohazards_at(self, lat: float, lng: float) -> GeohazardAssessment:
        """Return all geohazard ratings at the given coordinate (parallel fan-out).

        .. warning:: BGS REST hazard layers withdrawn; raises
            :class:`RuntimeError` until the replacement data source lands.
        """
        raise RuntimeError(_BGS_MIGRATION_MESSAGE)
        ss, gd, cg, ls, cd, rs = await asyncio.gather(  # pragma: no cover - legacy path
            self.shrink_swell(lat, lng),
            self.ground_dissolution(lat, lng),
            self.compressible_ground(lat, lng),
            self.landslide(lat, lng),
            self.collapsible_deposits(lat, lng),
            self.running_sand(lat, lng),
        )
        has_subsidence = any(
            r.grade in _SUBSIDENCE_GRADES for r in (ss, gd, cg, ls, cd, rs) if r.grade
        )
        return GeohazardAssessment(
            shrink_swell=ss,
            ground_dissolution=gd,
            compressible_ground=cg,
            landslide=ls,
            collapsible_deposits=cd,
            running_sand=rs,
            has_subsidence_risk=has_subsidence,
        )
