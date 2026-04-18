"""Pydantic models for the ONS Beta API (``api.beta.ons.gov.uk``)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ONSDimension(BaseModel):
    """Dataset dimension metadata."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    label: str | None = None
    description: str | None = None


class ONSDatasetVersion(BaseModel):
    """Dataset edition/version metadata block."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    edition: str | None = None
    version: int | None = None
    release_date: str | None = Field(default=None, alias="release_date")
    last_updated: str | None = Field(default=None, alias="last_updated")
    dimensions: list[ONSDimension] = Field(default_factory=list)
    state: str | None = None


class ONSObservationRow(BaseModel):
    """Single observation row."""

    model_config = ConfigDict(extra="allow")

    observation: str | None = None
    metadata: dict[str, Any] | None = None


class ONSObservationsResponse(BaseModel):
    """Observations payload for a filtered slice."""

    model_config = ConfigDict(extra="allow")

    observations: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: dict[str, Any] = Field(default_factory=dict)
    total_observations: int | None = Field(default=None, alias="total_observations")
    offset: int | None = None
    limit: int | None = None
