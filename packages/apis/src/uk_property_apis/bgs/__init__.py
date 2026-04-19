"""BGS geology hazard ArcGIS REST client."""

from __future__ import annotations

from uk_property_apis.bgs.client import BGSClient
from uk_property_apis.bgs.models import GeohazardAssessment, HazardRating

__all__ = [
    "BGSClient",
    "GeohazardAssessment",
    "HazardRating",
]
