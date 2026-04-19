"""Pydantic models for BGS geology hazard ArcGIS REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HazardRating(BaseModel):
    """A single geology hazard rating from BGS."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    hazard_type: str | None = None
    grade: str | None = None  # A-E: A=minimal, E=very significant
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class GeohazardAssessment(BaseModel):
    """Aggregate geology hazard assessment for a point."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shrink_swell: HazardRating
    ground_dissolution: HazardRating
    compressible_ground: HazardRating
    landslide: HazardRating
    collapsible_deposits: HazardRating
    running_sand: HazardRating
    has_subsidence_risk: bool = False  # True if any grade >= C
