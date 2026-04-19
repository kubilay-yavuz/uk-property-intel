"""Pydantic models for MHCLG household projections and Housing Delivery Test."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class HouseholdProjection(BaseModel):
    model_config = ConfigDict(extra="allow")
    la_code: str
    la_name: str
    base_year: int
    projections: dict[int, int] = Field(default_factory=dict)  # year -> projected households


class HousingDeliveryResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    la_code: str
    la_name: str
    year: int
    net_homes_required: int
    net_homes_delivered: int
    measurement: int  # % delivery vs requirement
