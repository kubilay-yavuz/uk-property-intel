"""Pydantic model for a Census 2021 table row."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class CensusTable(BaseModel):
    model_config = ConfigDict(extra="allow")
    table_id: str
    geography_code: str
    geography_name: str
    categories: dict[str, float] = Field(default_factory=dict)
