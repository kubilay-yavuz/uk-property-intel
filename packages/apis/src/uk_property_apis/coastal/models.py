"""Pydantic models for EA coastal erosion NCERM WFS."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErosionZone(BaseModel):
    """A coastal erosion risk zone from NCERM."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    zone_id: str | None = None
    management_policy: str | None = None
    erosion_rate_m_per_yr: float | None = None
    smp_reference: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class ShorelinePrediction(BaseModel):
    """A shoreline management prediction from NCERM."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    epoch_years: int | None = None
    predicted_distance_m: float | None = None
    risk_category: str | None = None  # e.g. "Advance", "Hold", "Managed retreat", "No active intervention"
    properties: dict[str, Any] = Field(default_factory=dict)
