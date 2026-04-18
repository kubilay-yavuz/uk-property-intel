"""Pure per-portal search-URL builders.

Kept deliberately pure (no network, no dependencies beyond stdlib) so both
public crawlers and the private production :class:`Crawler` can share them.
"""

from __future__ import annotations

from urllib.parse import quote

from uk_property_listings.types import SearchQuery


def build_zoopla_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Construct a Zoopla search URL for ``query``.

    Zoopla expects slugified location in the path, plus price/beds as query
    params. ``pn`` is the 1-indexed page number.
    """
    slug = _slugify(query.location)
    path = "/for-sale/property" if query.transaction == "sale" else "/to-rent/property"
    params: list[str] = []
    if query.min_price is not None:
        params.append(f"price_min={query.min_price}")
    if query.max_price is not None:
        params.append(f"price_max={query.max_price}")
    if query.min_beds is not None:
        params.append(f"beds_min={query.min_beds}")
    if query.max_beds is not None:
        params.append(f"beds_max={query.max_beds}")
    params.append(f"q={quote(query.location)}")
    params.append("search_source=for-sale" if query.transaction == "sale" else "search_source=to-rent")
    if page > 1:
        params.append(f"pn={page}")
    qs = "&".join(params)
    return f"https://www.zoopla.co.uk{path}/{slug}/?{qs}"


def build_rightmove_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Construct a Rightmove search URL.

    Note: Rightmove's "production" search uses an internal ``locationIdentifier``
    token rather than a readable slug. For human-facing queries we use the
    ``/property-for-sale/`` / ``/property-to-rent/`` endpoints which accept a
    town name directly via ``searchLocation``; this is what Rightmove's own
    autocomplete falls back to.
    """
    path = "property-for-sale" if query.transaction == "sale" else "property-to-rent"
    slug = _slugify(query.location).replace("-", "+")
    qs = [f"searchLocation={slug}"]
    if query.min_price is not None:
        qs.append(f"minPrice={query.min_price}")
    if query.max_price is not None:
        qs.append(f"maxPrice={query.max_price}")
    if query.min_beds is not None:
        qs.append(f"minBedrooms={query.min_beds}")
    if query.max_beds is not None:
        qs.append(f"maxBedrooms={query.max_beds}")
    if page > 1:
        qs.append(f"index={(page - 1) * 24}")
    return f"https://www.rightmove.co.uk/{path}/find.html?{'&'.join(qs)}"


def build_onthemarket_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Construct an OnTheMarket search URL."""
    slug = _slugify(query.location)
    path = "for-sale/property" if query.transaction == "sale" else "to-rent/property"
    qs: list[str] = []
    if query.min_price is not None:
        qs.append(f"min-price={query.min_price}")
    if query.max_price is not None:
        qs.append(f"max-price={query.max_price}")
    if query.min_beds is not None:
        qs.append(f"min-bedrooms={query.min_beds}")
    if query.max_beds is not None:
        qs.append(f"max-bedrooms={query.max_beds}")
    if page > 1:
        qs.append(f"page={page}")
    query_string = f"?{'&'.join(qs)}" if qs else ""
    return f"https://www.onthemarket.com/{path}/{slug}/{query_string}"


def _slugify(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c == " " else " " for c in value)
    return "-".join(part for part in cleaned.lower().split() if part)
