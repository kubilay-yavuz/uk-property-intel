"""Static data loaders for UK government datasets."""
from __future__ import annotations

from uk_property_data.broadband import BroadbandCoverage, BroadbandLookup
from uk_property_data.imd import IMDLookup, IMDRow
from uk_property_data.census import CensusLookup, CensusTable
from uk_property_data.mhclg import MHCLGLookup, HouseholdProjection, HousingDeliveryResult
from uk_property_data.ukcp18 import UKCP18Lookup, ClimateProjection

__version__ = "0.1.0"

__all__ = [
    "BroadbandCoverage",
    "BroadbandLookup",
    "CensusLookup",
    "CensusTable",
    "ClimateProjection",
    "HouseholdProjection",
    "HousingDeliveryResult",
    "IMDLookup",
    "IMDRow",
    "MHCLGLookup",
    "UKCP18Lookup",
    "__version__",
]
