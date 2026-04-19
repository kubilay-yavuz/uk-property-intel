"""Async client for EA coastal erosion NCERM WFS."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.coastal.models import ErosionZone, ShorelinePrediction

_BASE_URL = "https://environment.data.gov.uk/spatialdata/"

_LAYER_EROSION_RISK = "Environment_Agency:NCERM_ErosionRisk"
_LAYER_SMP = "Environment_Agency:NCERM_SMP_Management"

# Shoreline prediction property keys by epoch
_EPOCH_DISTANCE_KEYS: dict[int, str] = {
    20: "PREDICTION_20",
    50: "PREDICTION_50",
    100: "PREDICTION_100",
}
_EPOCH_CATEGORY_KEYS: dict[int, str] = {
    20: "POLICY_20",
    50: "POLICY_50",
    100: "POLICY_100",
}


def _wfs_params_dwithin(
    type_name: str, lat: float, lng: float, distance_m: float
) -> dict[str, Any]:
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "outputFormat": "json",
        "typeName": type_name,
        "CQL_FILTER": f"DWITHIN(SHAPE,POINT({lng} {lat}),{distance_m},meters)",
    }


def _wfs_params_intersects(type_name: str, lat: float, lng: float) -> dict[str, Any]:
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


class CoastalErosionClient(BaseAPIClient):
    """Client for EA NCERM coastal erosion WFS at https://environment.data.gov.uk/spatialdata/."""

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

    async def erosion_risk_near(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float = 5.0,
    ) -> list[ErosionZone]:
        """Return coastal erosion risk zones within ``distance_km`` of the coordinate."""
        distance_m = distance_km * 1000.0
        data = await self._get(
            "",
            params=_wfs_params_dwithin(_LAYER_EROSION_RISK, lat, lng, distance_m),
        )
        results = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            try:
                rate_raw = props.get("EROSION_RATE") or props.get("erosion_rate")
                rate = float(rate_raw) if rate_raw is not None else None
            except (TypeError, ValueError):
                rate = None
            results.append(
                self._validate_model(
                    ErosionZone,
                    {
                        "zone_id": (
                            props.get("ZONE_ID")
                            or props.get("zone_id")
                            or props.get("FID")
                            or props.get("OBJECTID")
                        ),
                        "management_policy": (
                            props.get("MANAGEMENT_POLICY") or props.get("management_policy")
                        ),
                        "erosion_rate_m_per_yr": rate,
                        "smp_reference": props.get("SMP_REF") or props.get("smp_reference"),
                        "properties": props,
                    },
                )
            )
        return results

    async def shoreline_prediction(
        self,
        lat: float,
        lng: float,
        *,
        epoch: Literal[20, 50, 100] = 50,
    ) -> ShorelinePrediction | None:
        """Return shoreline management prediction at the coordinate for the given epoch.

        Returns ``None`` if no SMP polygon intersects the point.
        """
        data = await self._get(
            "",
            params=_wfs_params_intersects(_LAYER_SMP, lat, lng),
        )
        features = _extract_features(data)
        if not features:
            return None
        props = features[0].get("properties") or {}
        dist_key = _EPOCH_DISTANCE_KEYS[epoch]
        cat_key = _EPOCH_CATEGORY_KEYS[epoch]
        try:
            dist_raw = props.get(dist_key)
            predicted_distance = float(dist_raw) if dist_raw is not None else None
        except (TypeError, ValueError):
            predicted_distance = None
        return self._validate_model(
            ShorelinePrediction,
            {
                "epoch_years": epoch,
                "predicted_distance_m": predicted_distance,
                "risk_category": props.get(cat_key) or props.get("POLICY") or props.get("policy"),
                "properties": props,
            },
        )
