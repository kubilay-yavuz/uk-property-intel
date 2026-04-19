"""Async client for Natural England MAGIC WFS."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.natural_england.models import (
    AONBArea,
    AncientWoodlandArea,
    Designations,
    GreenBeltArea,
    NationalParkArea,
    SSSIArea,
)

_BASE_URL = "https://environment.data.gov.uk/spatialdata/"

_LAYER_GREEN_BELT = "Natural_England:GreenBelt"
_LAYER_SSSI = "Natural_England:SSSI_England"
_LAYER_AONB = "Natural_England:Areas_of_Outstanding_Natural_Beauty_England"
_LAYER_NATIONAL_PARK = "Natural_England:National_Parks_England"
_LAYER_ANCIENT_WOODLAND = "Natural_England:Ancient_Woodland_England"


def _wfs_params(type_name: str, lat: float, lng: float) -> dict[str, Any]:
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "outputFormat": "json",
        "typeName": type_name,
        "CQL_FILTER": f"INTERSECTS(SHAPE,POINT({lng} {lat}))",
    }


def _extract_features(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("features") or []


class NaturalEnglandClient(BaseAPIClient):
    """Client for Natural England MAGIC WFS at https://environment.data.gov.uk/spatialdata/."""

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

    async def green_belt_at(self, lat: float, lng: float) -> list[GreenBeltArea]:
        """Return green belt designations at the given coordinate."""
        data = await self._get("", params=_wfs_params(_LAYER_GREEN_BELT, lat, lng))
        results = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    GreenBeltArea,
                    {
                        "name": props.get("NAME") or props.get("name"),
                        "area_ha": props.get("AREA_HA") or props.get("area_ha"),
                        "properties": props,
                    },
                )
            )
        return results

    async def sssi_at(self, lat: float, lng: float) -> list[SSSIArea]:
        """Return SSSI designations at the given coordinate."""
        data = await self._get("", params=_wfs_params(_LAYER_SSSI, lat, lng))
        results = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    SSSIArea,
                    {
                        "name": props.get("SSSI_NAME") or props.get("name"),
                        "status": props.get("STATUS") or props.get("status"),
                        "properties": props,
                    },
                )
            )
        return results

    async def aonb_at(self, lat: float, lng: float) -> list[AONBArea]:
        """Return AONB designations at the given coordinate."""
        data = await self._get("", params=_wfs_params(_LAYER_AONB, lat, lng))
        results = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    AONBArea,
                    {
                        "name": props.get("NAME") or props.get("name"),
                        "properties": props,
                    },
                )
            )
        return results

    async def national_park_at(self, lat: float, lng: float) -> list[NationalParkArea]:
        """Return National Park designations at the given coordinate."""
        data = await self._get("", params=_wfs_params(_LAYER_NATIONAL_PARK, lat, lng))
        results = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    NationalParkArea,
                    {
                        "name": props.get("NAME") or props.get("name"),
                        "properties": props,
                    },
                )
            )
        return results

    async def ancient_woodland_at(self, lat: float, lng: float) -> list[AncientWoodlandArea]:
        """Return Ancient Woodland designations at the given coordinate."""
        data = await self._get("", params=_wfs_params(_LAYER_ANCIENT_WOODLAND, lat, lng))
        results = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    AncientWoodlandArea,
                    {
                        "name": props.get("NAME") or props.get("name"),
                        "category": props.get("CATEGORY") or props.get("category"),
                        "properties": props,
                    },
                )
            )
        return results

    async def designations_at(self, lat: float, lng: float) -> Designations:
        """Return all Natural England designations at the given coordinate (parallel fan-out)."""
        gb, sssi, aonb, np_, aw = await asyncio.gather(
            self.green_belt_at(lat, lng),
            self.sssi_at(lat, lng),
            self.aonb_at(lat, lng),
            self.national_park_at(lat, lng),
            self.ancient_woodland_at(lat, lng),
        )
        return Designations(
            is_green_belt=bool(gb),
            is_sssi=bool(sssi),
            is_aonb=bool(aonb),
            is_national_park=bool(np_),
            is_ancient_woodland=bool(aw),
            green_belt=gb,
            sssi=sssi,
            aonb=aonb,
            national_parks=np_,
            ancient_woodland=aw,
        )
