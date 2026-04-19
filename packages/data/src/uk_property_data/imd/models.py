"""Pydantic model for a single IMD 2019 LSOA row."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict


class IMDRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    lsoa_code: str
    lsoa_name: str
    la_code: str
    la_name: str
    imd_rank: int                     # 1 = most deprived
    imd_decile: int                   # 1-10
    income_rank: int
    employment_rank: int
    education_rank: int
    health_rank: int
    crime_rank: int
    housing_rank: int
    living_environment_rank: int
