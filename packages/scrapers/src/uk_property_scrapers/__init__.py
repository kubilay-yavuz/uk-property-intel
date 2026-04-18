"""uk-property-scrapers - pure-Python parsers for UK property listing sites.

The base package is I/O-free: pass HTML in, get :class:`Listing` models out.

For fetching HTML there are two companion packages:

* :mod:`uk_property_listings` (public, in the same monorepo) - the free tier,
  ``httpx``-only ``SimpleCrawler`` plus ``SearchQuery``, URL builders, and
  pagination helpers for Zoopla/Rightmove/OnTheMarket.
* ``uk-property-apify-shared`` (private, in the ``uk-property-apify`` repo) -
  the production-grade ``Crawler`` with TLS-impersonating + Playwright-stealth
  fetchers, anti-bot classification, proxy rotation, rate limiting, and tier
  escalation. Used by the hosted Apify actors.
"""

from uk_property_scrapers import onthemarket, rightmove, zoopla
from uk_property_scrapers.schema import (
    Address,
    Agent,
    Image,
    Listing,
    ListingFeature,
    ListingType,
    Price,
    PriceQualifier,
    PropertyType,
    RentPeriod,
    RentPrice,
    Source,
    Tenure,
    TransactionType,
)

__version__ = "0.1.0"

__all__ = [
    "Address",
    "Agent",
    "Image",
    "Listing",
    "ListingFeature",
    "ListingType",
    "Price",
    "PriceQualifier",
    "PropertyType",
    "RentPeriod",
    "RentPrice",
    "Source",
    "Tenure",
    "TransactionType",
    "__version__",
    "onthemarket",
    "rightmove",
    "zoopla",
]
