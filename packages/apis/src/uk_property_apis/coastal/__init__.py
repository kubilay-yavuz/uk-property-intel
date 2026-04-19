"""EA coastal erosion NCERM WFS client."""

from __future__ import annotations

from uk_property_apis.coastal.client import CoastalErosionClient
from uk_property_apis.coastal.models import ErosionZone, ShorelinePrediction

__all__ = [
    "CoastalErosionClient",
    "ErosionZone",
    "ShorelinePrediction",
]
