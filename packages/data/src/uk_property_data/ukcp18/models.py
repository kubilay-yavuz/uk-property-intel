"""Pydantic model for a UKCP18 climate projection summary row."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict


class ClimateProjection(BaseModel):
    model_config = ConfigDict(extra="allow")
    region: str
    scenario: Literal["rcp26", "rcp45", "rcp60", "rcp85"]
    epoch: Literal[2030, 2050, 2070, 2090]
    temp_change_c: float
    precip_change_pct: float
    summer_precip_change_pct: float
    winter_precip_change_pct: float
    sea_level_rise_cm: float | None = None  # coastal regions only
