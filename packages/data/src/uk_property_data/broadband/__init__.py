"""Ofcom Connected Nations broadband-coverage lookup.

Loads the per-postcode Fixed Broadband Coverage CSV published annually
by Ofcom (Open Government Licence) and exposes a fast in-memory
lookup keyed by compact postcode.
"""

from __future__ import annotations

from uk_property_data.broadband.loader import BroadbandLookup
from uk_property_data.broadband.models import BroadbandCoverage

__all__ = ["BroadbandCoverage", "BroadbandLookup"]
