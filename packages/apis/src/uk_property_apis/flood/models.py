"""Pydantic models for Environment Agency flood monitoring API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FloodSeverityItem(BaseModel):
    """Severity level reference."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    label: str | None = None
    severity_value: int | None = Field(default=None, alias="severityValue")


class FloodWarning(BaseModel):
    """Active flood warning or alert."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    description: str | None = None
    message: str | None = None
    severity: str | None = None
    severity_level: FloodSeverityItem | None = Field(default=None, alias="severityLevel")
    river_or_sea: str | None = Field(default=None, alias="riverOrSea")
    ea_area_name: str | None = Field(default=None, alias="eaAreaName")
    flood_area_id: str | None = Field(default=None, alias="floodAreaId")
    is_tidal: bool | None = Field(default=None, alias="isTidal")
    time_raised: str | None = Field(default=None, alias="timeRaised")
    time_severity_changed: str | None = Field(default=None, alias="timeSeverityChanged")
    time_message_changed: str | None = Field(default=None, alias="timeMessageChanged")


class FloodArea(BaseModel):
    """Polygon / metadata for a flood area."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    county: str | None = None
    river_or_sea: str | None = Field(default=None, alias="riverOrSea")
    label: str | None = None
    description: str | None = None
    ea_area_name: str | None = Field(default=None, alias="eaAreaName")
    area_code: str | None = Field(default=None, alias="areaCode")
    polygon: str | None = None


class StageScaleItem(BaseModel):
    """Stage scale entry on a station."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    value: float | None = None
    label: str | None = None


class MonitoringStation(BaseModel):
    """River / rain gauge or similar monitoring station."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    label: str | None = None
    river_name: str | None = Field(default=None, alias="riverName")
    town: str | None = None
    lat: float | None = None
    long: float | None = None
    status: str | None = None
    stage_scale: list[StageScaleItem] | None = Field(default=None, alias="stageScale")
    measures: list[dict[str, Any]] | None = None


class FloodListResponse(BaseModel):
    """Envelope for list endpoints under ``/id/...``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    context: str | None = Field(default=None, alias="@context")
    meta: dict[str, Any] | None = Field(default=None, alias="meta")
    items: list[dict[str, Any]] = Field(default_factory=list)
