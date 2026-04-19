"""Parser for Zoopla search-results HTML.

Selectors were derived from live Zoopla crawls (April 2026, fixture at
``tests/fixtures/zoopla/search_cambridgeshire_2026-04.html``). Zoopla is a
Next.js app that ships CSS-module class names of the shape
``<componentName>_<cssRule>__<hashSuffix>``. The prefix (``price_priceText``,
``amenities_amenityListSlim``, etc.) is stable across deploys; the trailing
hash rotates. All selectors use ``[class*="<prefix>"]`` substring matching so
the parser is resilient to hash churn.

Zoopla serves three URL patterns for listing detail pages:
    * ``/for-sale/details/{id}/``    — resale properties
    * ``/new-homes/details/{id}/``   — new builds
    * ``/to-rent/details/{id}/``     — rentals

All functions are pure: they accept an HTML ``str`` and return Pydantic models.
Browser orchestration, proxies, rate-limiting, retries — those live in the
caller (MCP server, Apify actor, or CLI).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Final

from pydantic import ValidationError
from selectolax.parser import HTMLParser, Node

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

# ── URL patterns ─────────────────────────────────────────────────────────────

_ZOOPLA_ORIGIN: Final = "https://www.zoopla.co.uk"

_DETAIL_HREF_PATTERNS: Final = (
    "/for-sale/details/",
    "/new-homes/details/",
    "/to-rent/details/",
)
_EXCLUDED_HREF_SUBSTRINGS: Final = (
    "/contact/",
    "/enquiry/",
    "/viewing-request/",
)

_DETAIL_ID_RE: Final = re.compile(r"/details/(\d+)/?")
_LISTING_ID_FROM_ATTR_RE: Final = re.compile(r"^listing_(\d+)$")

_TRANSACTION_FROM_URL: Final = {
    "/for-sale/details/": TransactionType.SALE,
    "/new-homes/details/": TransactionType.SALE,
    "/to-rent/details/": TransactionType.RENT,
}

# ── Text-level patterns ──────────────────────────────────────────────────────

_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_INT_RE: Final = re.compile(r"(\d+)")
_FLOAT_RE: Final = re.compile(r"(\d+(?:[.,]\d+)?)")
_IMAGE_COUNT_RE: Final = re.compile(r"(\d+)\s*/\s*(\d+)")

_QUALIFIER_MAP: Final[dict[str, PriceQualifier]] = {
    "guide price": PriceQualifier.GUIDE_PRICE,
    "offers in excess of": PriceQualifier.OFFERS_IN_EXCESS_OF,
    "offers in the region of": PriceQualifier.OFFERS_IN_REGION,
    "offers over": PriceQualifier.OFFERS_OVER,
    "oieo": PriceQualifier.OFFERS_IN_EXCESS_OF,
    "oiro": PriceQualifier.OFFERS_IN_REGION,
    "from": PriceQualifier.FROM,
    "fixed price": PriceQualifier.FIXED_PRICE,
    "asking price": PriceQualifier.ASKING_PRICE,
    "poa": PriceQualifier.POA,
    "price on application": PriceQualifier.POA,
    "shared ownership from": PriceQualifier.SHARED_OWNERSHIP_FROM,
}

_RENT_PERIOD_MAP: Final[dict[str, RentPeriod]] = {
    "per calendar month": RentPeriod.PER_MONTH,
    "pcm": RentPeriod.PER_MONTH,
    "per month": RentPeriod.PER_MONTH,
    "a month": RentPeriod.PER_MONTH,
    "/ month": RentPeriod.PER_MONTH,
    "per week": RentPeriod.PER_WEEK,
    "a week": RentPeriod.PER_WEEK,
    "pw": RentPeriod.PER_WEEK,
    "/ week": RentPeriod.PER_WEEK,
    "per annum": RentPeriod.PER_YEAR,
    "per year": RentPeriod.PER_YEAR,
    " pa": RentPeriod.PER_YEAR,
    " pd": RentPeriod.PER_DAY,
    "per day": RentPeriod.PER_DAY,
}

# Ordered most-specific-first so "detached house" wins over "detached".
_PROPERTY_TYPE_HINTS: Final[tuple[tuple[str, PropertyType], ...]] = (
    ("end of terrace", PropertyType.END_OF_TERRACE),
    ("end terrace", PropertyType.END_OF_TERRACE),
    ("semi-detached house", PropertyType.SEMI_DETACHED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("semi-detached", PropertyType.SEMI_DETACHED),
    ("detached house", PropertyType.DETACHED),
    ("detached home", PropertyType.DETACHED),
    ("detached bungalow", PropertyType.BUNGALOW),
    ("detached", PropertyType.DETACHED),
    ("terraced house", PropertyType.TERRACED),
    ("terraced home", PropertyType.TERRACED),
    ("terrace", PropertyType.TERRACED),
    ("apartment", PropertyType.APARTMENT),
    ("maisonette", PropertyType.MAISONETTE),
    ("bungalow", PropertyType.BUNGALOW),
    ("cottage", PropertyType.COTTAGE),
    ("studio", PropertyType.STUDIO),
    ("park home", PropertyType.PARK_HOME),
    ("houseboat", PropertyType.HOUSEBOAT),
    ("land for sale", PropertyType.LAND),
    ("plot for sale", PropertyType.LAND),
    ("commercial", PropertyType.COMMERCIAL),
    ("flat", PropertyType.FLAT),
)

_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)(?:\s+([0-9][A-Z]{2}))?\b"
)

_FEATURE_TOKEN_MAP: Final[dict[str, ListingFeature]] = {
    "new home": ListingFeature.NEW_HOME,
    "new build": ListingFeature.NEW_HOME,
    "reduced": ListingFeature.REDUCED,
    "chain free": ListingFeature.CHAIN_FREE,
    "chain-free": ListingFeature.CHAIN_FREE,
    "no chain": ListingFeature.CHAIN_FREE,
    "retirement": ListingFeature.RETIREMENT,
    "shared ownership": ListingFeature.SHARED_OWNERSHIP,
    "part buy part rent": ListingFeature.PART_BUY_PART_RENT,
    "part-buy part-rent": ListingFeature.PART_BUY_PART_RENT,
    "auction": ListingFeature.AUCTION,
    "investment": ListingFeature.INVESTMENT,
    "tenanted": ListingFeature.TENANTED_INVESTMENT,
    "new listing": ListingFeature.NEW_LISTING,
    "just added": ListingFeature.NEW_LISTING,
    "featured": ListingFeature.FEATURED,
    "property of the week": ListingFeature.PROPERTY_OF_THE_WEEK,
    "premium": ListingFeature.PREMIUM,
    "sold stc": ListingFeature.SOLD_STC,
    "sold (stc)": ListingFeature.SOLD_STC,
    "under offer": ListingFeature.UNDER_OFFER,
    "open day": ListingFeature.OPEN_DAY,
    "open house": ListingFeature.OPEN_DAY,
    "video tour": ListingFeature.VIDEO_TOUR,
    "virtual tour": ListingFeature.VIRTUAL_TOUR,
    "3d tour": ListingFeature.VIRTUAL_TOUR,
}

_TENURE_TOKEN_MAP: Final[dict[str, Tenure]] = {
    "freehold": Tenure.FREEHOLD,
    "leasehold": Tenure.LEASEHOLD,
    "share of freehold": Tenure.SHARE_OF_FREEHOLD,
    "commonhold": Tenure.COMMONHOLD,
    "feuhold": Tenure.FEUHOLD,
}


# ── Public API ───────────────────────────────────────────────────────────────


def extract_listing_urls(html: str) -> list[str]:
    """Return a de-duplicated list of Zoopla listing-detail URLs found in the HTML.

    Accepts any Zoopla page (search results, area page, saved search, agent page).
    Only returns URLs matching the known detail-page patterns; filters out contact,
    agent, and navigational links.
    """
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []

    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if not any(pattern in href for pattern in _DETAIL_HREF_PATTERNS):
            continue
        if any(excluded in href for excluded in _EXCLUDED_HREF_SUBSTRINGS):
            continue
        canonical = _strip_query(_absolutize(href))
        if canonical in seen:
            continue
        seen.add(canonical)
        urls.append(canonical)
    return urls


def parse_search_results(
    html: str,
    *,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> list[Listing]:
    """Parse a Zoopla search-results page into a list of SEARCH_CARD listings.

    The ``transaction_type`` hint should reflect the search URL the HTML came
    from (``/for-sale/...`` or ``/to-rent/...``). When ``UNKNOWN``, each card's
    URL is used to infer it.

    Cards that fail :class:`Listing` validation (eg. a block-of-flats entry
    advertising 100+ bedrooms) are silently skipped so one bad card doesn't
    kill the whole page.
    """
    tree = HTMLParser(html)
    cards = _find_listing_cards(tree)
    listings: list[Listing] = []
    for card in cards:
        try:
            listing = _parse_search_card(card, hinted_type=transaction_type)
        except ValidationError:
            continue
        if listing is not None:
            listings.append(listing)
    return listings


def parse_detail_page(
    html: str,
    *,
    source_url: str | None = None,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> Listing | None:
    """Parse a Zoopla property-detail page into a single DETAIL Listing.

    Uses the embedded JSON-LD ``RealEstateListing`` block as the primary data
    source (Zoopla exposes a clean ``schema.org`` payload on every detail page),
    and falls back to CSS-selector extraction for fields JSON-LD omits
    (tenure, agent, full amenities, description).
    """
    tree = HTMLParser(html)
    ld = _find_realestate_jsonld(tree)
    if ld is None and source_url is None:
        return None

    url = _coerce_str(ld.get("mainEntityOfPage") if ld else None) or source_url
    if not url:
        return None
    source_id = _extract_listing_id(url)
    if source_id is None:
        return None

    tx = (
        transaction_type
        if transaction_type != TransactionType.UNKNOWN
        else _transaction_from_url(url)
    )

    title, address_raw = _parse_detail_title_address(tree)
    title = title or _coerce_str(ld.get("name") if ld else None)

    if not address_raw and ld:
        address_raw = _derive_address_from_ld_name(_coerce_str(ld.get("name")))

    if not address_raw:
        return None

    price_raw, price_qualifier_raw = _parse_detail_price(tree)
    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    if amount_pence is None and ld:
        offer = ld.get("offers") or {}
        ld_price = offer.get("price") if isinstance(offer, dict) else None
        if isinstance(ld_price, (int, float)):
            amount_pence = round(float(ld_price) * 100)

    sale_price, rent_price = _materialize_prices(
        raw=price_raw or "",
        qualifier_raw=price_qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    amenities = _parse_detail_amenities(tree)
    beds = amenities.get("beds")
    baths = amenities.get("baths")
    receptions = amenities.get("receptions")
    sqft = amenities.get("sqft")

    if beds is None and ld:
        beds = _ld_property_value_int(ld, "Bedrooms")
    if baths is None and ld:
        baths = _ld_property_value_int(ld, "Bathrooms")

    description = _parse_detail_description(tree)
    if not description and ld:
        description = _coerce_str(ld.get("description"))

    image_urls = _parse_detail_images(tree)
    if not image_urls and ld:
        img = _coerce_str(ld.get("image"))
        if img:
            image_urls = [Image(url=img)]  # type: ignore[arg-type]

    property_type_raw = _parse_detail_property_type(title or "")
    property_type = _infer_property_type(property_type_raw) if property_type_raw else PropertyType.UNKNOWN

    first_listed = None
    if ld:
        posted = _coerce_str(ld.get("datePosted"))
        if posted:
            first_listed = _parse_iso(posted)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    tenure = _parse_detail_tenure(tree)
    agent = _parse_detail_agent(tree)
    features = _parse_detail_features(tree)
    if features and rent_price is not None and ListingFeature.AUCTION in features:
        features.remove(ListingFeature.AUCTION)

    return Listing(
        source=Source.ZOOPLA,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        listing_type=ListingType.DETAIL,
        transaction_type=tx,
        sale_price=sale_price,
        rent_price=rent_price,
        property_type=property_type,
        property_type_raw=property_type_raw,
        bedrooms=beds,
        bathrooms=baths,
        reception_rooms=receptions,
        floor_area_sqft=sqft,
        tenure=tenure,
        address=address,
        title=title,
        summary=None,
        description=description,
        features=features,
        image_urls=image_urls,
        agent=agent,
        first_listed_at=first_listed,
        raw_site_fields={
            k: v
            for k, v in {
                "price": price_raw,
                "price_qualifier": price_qualifier_raw,
                "property_type_raw": property_type_raw,
            }.items()
            if v
        },
    )


# ── Search card discovery ───────────────────────────────────────────────────


def _find_listing_cards(tree: HTMLParser) -> list[Node]:
    """Locate each listing row.

    Zoopla wraps every search result in ``<div id="listing_{id}" class="Listings_listingRow__...">``
    inside ``[data-testid="regular-listings"]``. We target that ID prefix as the
    primary selector — it's been stable for years. If Zoopla ships a new shape,
    we fall back to any element carrying the card-content testid, then to any
    anchor pointing at a detail URL.
    """
    primary = tree.css('div[id^="listing_"]')
    if primary:
        return primary

    fallback_cards = tree.css('[data-testid="listing-card-content"]')
    if fallback_cards:
        return fallback_cards

    synthetic: list[Node] = []
    seen_hrefs: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href", "") or ""
        if not any(p in href for p in _DETAIL_HREF_PATTERNS):
            continue
        if any(e in href for e in _EXCLUDED_HREF_SUBSTRINGS):
            continue
        canonical = _strip_query(_absolutize(href))
        if canonical in seen_hrefs:
            continue
        seen_hrefs.add(canonical)
        synthetic.append(anchor)
    return synthetic


def _parse_search_card(card: Node, *, hinted_type: TransactionType) -> Listing | None:
    """Turn a single listing-row DOM node into a SEARCH_CARD Listing."""
    url = _find_detail_url(card)
    if url is None:
        return None

    source_id = _extract_source_id_from_row(card) or _extract_listing_id(url)
    if source_id is None:
        return None

    tx = hinted_type if hinted_type != TransactionType.UNKNOWN else _transaction_from_url(url)

    # Card anchor holds most of the content. Fall back to walking up from the
    # row if there's no anchor with the testid (rare — but keeps the parser
    # degrading gracefully).
    content = card.css_first('a[data-testid="listing-card-content"]') or card

    address_raw = _clean_whitespace(_first_text(content, ['address[class*="summary_address"]', "address"]))
    if not address_raw:
        address_raw = _clean_whitespace(_first_text(content, ["h2", "h3"]))
    if not address_raw:
        return None

    price_text = _clean_whitespace(_first_text(content, ['[class*="price_priceText"]', '[class*="PriceText"]']))
    price_qualifier_raw = _clean_whitespace(
        _first_text(content, ['[class*="price_priceTitle"]', '[class*="PriceTitle"]'])
    )

    amenity_list = content.css_first('[class*="amenities_amenityListSlim"]') or content.css_first(
        '[class*="amenities_amenityList"]'
    )
    amenities_raw = _parse_amenity_items(amenity_list)
    beds = amenities_raw.get("beds")
    baths = amenities_raw.get("baths")
    receptions = amenities_raw.get("receptions")
    sqft = amenities_raw.get("sqft")

    summary = _clean_whitespace(_first_text(content, ['p[class*="summary_summary"]']))

    badges_text = _collect_badges(card)
    status_badges_text = _collect_status_badges(card)
    tenure = _detect_tenure(" ".join(badges_text))

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_text,
                    price_qualifier_raw,
                    summary,
                    " ".join(badges_text),
                    " ".join(status_badges_text),
                ],
            )
        ),
        url=url,
    )

    amount_pence = _extract_price_pence(price_text) if price_text else None
    sale_price, rent_price = _materialize_prices(
        raw=price_text or "",
        qualifier_raw=price_qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    property_type_raw = _parse_detail_property_type(summary or "") or _parse_detail_property_type(
        address_raw
    )
    property_type = _infer_property_type(property_type_raw) if property_type_raw else PropertyType.UNKNOWN

    images, image_count = _parse_card_images(card)
    agent = _parse_card_agent(card)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    return Listing(
        source=Source.ZOOPLA,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        listing_type=ListingType.SEARCH_CARD,
        transaction_type=tx,
        sale_price=sale_price,
        rent_price=rent_price,
        property_type=property_type,
        property_type_raw=property_type_raw,
        bedrooms=beds,
        bathrooms=baths,
        reception_rooms=receptions,
        floor_area_sqft=sqft,
        tenure=tenure,
        address=address,
        title=address_raw,
        summary=summary,
        features=features,
        image_urls=images,
        image_count=image_count,
        agent=agent,
        raw_site_fields=_build_raw_fields(
            price=price_text,
            price_qualifier=price_qualifier_raw,
            beds=amenities_raw.get("beds_raw"),
            baths=amenities_raw.get("baths_raw"),
            receptions=amenities_raw.get("receptions_raw"),
            sqft=amenities_raw.get("sqft_raw"),
            badges=" | ".join(badges_text) if badges_text else None,
            status_badges=" | ".join(status_badges_text) if status_badges_text else None,
            property_type=property_type_raw,
        ),
    )


# ── Field extractors ─────────────────────────────────────────────────────────


def _find_detail_url(card: Node) -> str | None:
    anchor = card.css_first('a[data-testid="listing-card-content"][href]')
    if anchor is not None:
        href = anchor.attributes.get("href", "") or ""
        if href and any(p in href for p in _DETAIL_HREF_PATTERNS):
            return _strip_query(_absolutize(href))

    for anchor in card.css("a[href]"):
        href = anchor.attributes.get("href", "") or ""
        if not any(p in href for p in _DETAIL_HREF_PATTERNS):
            continue
        if any(e in href for e in _EXCLUDED_HREF_SUBSTRINGS):
            continue
        return _strip_query(_absolutize(href))

    if card.tag == "a":
        href = card.attributes.get("href", "") or ""
        if href and any(p in href for p in _DETAIL_HREF_PATTERNS):
            return _strip_query(_absolutize(href))
    return None


def _extract_source_id_from_row(card: Node) -> str | None:
    identifier = card.attributes.get("id", "") or ""
    match = _LISTING_ID_FROM_ATTR_RE.match(identifier)
    return match.group(1) if match else None


def _extract_listing_id(url: str) -> str | None:
    match = _DETAIL_ID_RE.search(url)
    return match.group(1) if match else None


def _parse_amenity_items(amenity_list: Node | None) -> dict[str, int | str | None]:
    """Parse the slim amenity list like '6 beds · 3 baths · 2 receptions · ~1894 sq ft'."""
    out: dict[str, int | str | None] = {}
    if amenity_list is None:
        return out

    items = amenity_list.css('[class*="amenities_amenityItemSlim"]') or amenity_list.css(
        '[class*="amenities_amenity"]'
    )
    for node in items:
        text = _clean_whitespace(node.text(strip=True))
        if not text:
            continue
        lower = text.lower()
        if "bed" in lower and "beds" not in out:
            out["beds"] = _parse_int(text)
            out["beds_raw"] = text
        elif "bath" in lower and "baths" not in out:
            out["baths"] = _parse_int(text)
            out["baths_raw"] = text
        elif ("reception" in lower or "living" in lower) and "receptions" not in out:
            out["receptions"] = _parse_int(text)
            out["receptions_raw"] = text
        elif ("sq ft" in lower or "sq. ft" in lower or "sqft" in lower) and "sqft" not in out:
            out["sqft"] = _parse_int(text)
            out["sqft_raw"] = text
    return out


def _collect_badges(card: Node) -> list[str]:
    badges: list[str] = []
    for wrapper in card.css('[class*="badges_badgesListSlim"] li'):
        text = _clean_whitespace(wrapper.text(strip=True))
        if text:
            badges.append(text)
    return badges


def _collect_status_badges(card: Node) -> list[str]:
    badges: list[str] = []
    for wrapper in card.css('[class*="status_statusListSlim"] li'):
        text = _clean_whitespace(wrapper.text(strip=True))
        if text:
            badges.append(text)
    return badges


def _parse_card_images(card: Node) -> tuple[list[Image], int | None]:
    """Extract image URLs from the gallery + the total image count from pagination badge."""
    images: list[Image] = []
    seen: set[str] = set()

    primary_source = card.css_first(
        'picture source[srcset][type="image/jpeg"]'
    ) or card.css_first("picture source[srcset]")
    if primary_source is not None:
        url = _first_srcset_url(primary_source.attributes.get("srcset"))
        if url and url not in seen:
            seen.add(url)
            images.append(Image(url=url))  # type: ignore[arg-type]

    for img in card.css('img[class*="Listings_additionalImage"]'):
        src = img.attributes.get("src") or ""
        if src and src not in seen and src.startswith("http"):
            seen.add(src)
            images.append(Image(url=src))  # type: ignore[arg-type]

    if not images:
        for img in card.css("picture img, img[src]"):
            src = img.attributes.get("src") or ""
            if src.startswith("http") and src not in seen:
                seen.add(src)
                images.append(Image(url=src))  # type: ignore[arg-type]
                break

    pagination = card.css_first('[data-testid="pagination-count"]')
    image_count: int | None = None
    if pagination is not None:
        match = _IMAGE_COUNT_RE.search(pagination.text(strip=True))
        if match:
            image_count = int(match.group(2))
    if image_count is None:
        gallery_items = card.css('ol[role="list"] > li[data-slide]')
        if gallery_items:
            image_count = len(gallery_items)

    return images, image_count


def _parse_card_agent(card: Node) -> Agent | None:
    logo = card.css_first('img[class*="agent-logo_agentLogoImage"]') or card.css_first(
        'img[alt][src*="static_agent_logo"]'
    )
    if logo is None:
        return None
    alt = logo.attributes.get("alt") or ""
    src = logo.attributes.get("src") or None
    name: str | None
    branch: str | None
    if " - " in alt:
        name, branch = (seg.strip() for seg in alt.split(" - ", 1))
    else:
        name = alt.strip() or None
        branch = None
    return Agent(
        name=name,
        branch=branch,
        logo_url=src if src and src.startswith("http") else None,  # type: ignore[arg-type]
    )


def _first_srcset_url(srcset: str | None) -> str | None:
    if not srcset:
        return None
    candidates = [seg.strip() for seg in srcset.split(",") if seg.strip()]
    if not candidates:
        return None
    url_part = candidates[0].split(" ")[0]
    return _normalize_image_url(url_part)


def _normalize_image_url(url: str) -> str | None:
    if not url:
        return None
    if url.endswith(":p"):
        url = url[:-2]
    return url if url.startswith("http") else None


# ── Detail page helpers ─────────────────────────────────────────────────────


def _find_realestate_jsonld(tree: HTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(strip=False).strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "RealEstateListing":
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and item.get("@type") == "RealEstateListing":
                    return item
    return None


def _parse_detail_title_address(tree: HTMLParser) -> tuple[str | None, str | None]:
    h1 = tree.css_first('h1[class*="page_titleWrapper"]') or tree.css_first("h1")
    if h1 is None:
        return None, None
    address_node = h1.css_first('address[class*="page_address"]') or h1.css_first("address")
    address_raw = _clean_whitespace(address_node.text(strip=True)) if address_node else None

    if address_node is not None:
        address_node.decompose()
    title = _clean_whitespace(h1.text(strip=True)) or None
    return title, address_raw


def _parse_detail_price(tree: HTMLParser) -> tuple[str | None, str | None]:
    wrapper = tree.css_first('[class*="Price_priceWrapper"]')
    price_el = (wrapper.css_first('[class*="Price_price__"]') if wrapper else None) or tree.css_first(
        '[class*="Price_price__"]'
    )
    price_text = _clean_whitespace(price_el.text(strip=True)) if price_el else None

    qualifier_text: str | None = None
    if wrapper is not None:
        preceding = wrapper.parent
        if preceding is not None:
            p = preceding.css_first("p")
            if p is not None:
                qualifier_text = _clean_whitespace(p.text(strip=True))

    return price_text, qualifier_text


def _parse_detail_amenities(tree: HTMLParser) -> dict[str, int | None]:
    """Detail page amenity list is different from search — ul[class^='Amenities_amenitiesList']."""
    out: dict[str, int | None] = {}
    amenity_nodes = tree.css('ul[class*="Amenities_amenitiesList"] [class*="Amenities_amenity__"]')
    for node in amenity_nodes:
        text = _clean_whitespace(node.text(strip=True))
        if not text:
            continue
        lower = text.lower()
        if "bed" in lower and out.get("beds") is None:
            out["beds"] = _parse_int(text)
        elif "bath" in lower and out.get("baths") is None:
            out["baths"] = _parse_int(text)
        elif "reception" in lower and out.get("receptions") is None:
            out["receptions"] = _parse_int(text)
        elif ("sq ft" in lower or "sq. ft" in lower) and out.get("sqft") is None:
            out["sqft"] = _parse_int(text)
    out.setdefault("beds", None)
    out.setdefault("baths", None)
    out.setdefault("receptions", None)
    out.setdefault("sqft", None)
    return out


def _parse_detail_description(tree: HTMLParser) -> str | None:
    node = tree.css_first('[class*="DetailedDescription_detailedDescription"]')
    if node is None:
        return None
    # Strip scripts/styles within — just in case.
    for junk in node.css("script, style"):
        junk.decompose()
    return _clean_whitespace(node.text(separator="\n"))


def _parse_detail_images(tree: HTMLParser) -> list[Image]:
    images: list[Image] = []
    seen: set[str] = set()
    for source in tree.css('picture source[srcset]'):
        url = _first_srcset_url(source.attributes.get("srcset"))
        if url and url not in seen:
            seen.add(url)
            images.append(Image(url=url))  # type: ignore[arg-type]
        if len(images) >= 30:
            break
    return images


def _parse_detail_property_type(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for phrase, _ in _PROPERTY_TYPE_HINTS:
        if phrase in lowered:
            return phrase
    return None


def _parse_detail_tenure(tree: HTMLParser) -> Tenure:
    text = " ".join(
        _clean_whitespace(node.text(strip=True)) or ""
        for node in tree.css('[class*="DetailedDescription"], [class*="KeyInformation"], dt, dd')
    ).lower()
    return _detect_tenure(text)


def _parse_detail_agent(tree: HTMLParser) -> Agent | None:
    logo = tree.css_first('img[class*="BranchSummary"][alt]') or tree.css_first(
        'img[class*="agent-logo"][alt]'
    )
    if logo is None:
        return None
    alt = logo.attributes.get("alt") or ""
    src = logo.attributes.get("src") or None
    if " - " in alt:
        name, branch = (seg.strip() for seg in alt.split(" - ", 1))
    else:
        name, branch = (alt.strip() or None), None
    return Agent(
        name=name,
        branch=branch,
        logo_url=src if src and src.startswith("http") else None,  # type: ignore[arg-type]
    )


def _parse_detail_features(tree: HTMLParser) -> list[ListingFeature]:
    blob = " ".join(
        _clean_whitespace(node.text(strip=True)) or ""
        for node in tree.css(
            '[class*="DetailedDescription"], [class*="Features"], [class*="KeyInformation"], li'
        )
    )
    return _detect_features(blob=blob, url=None)


# ── Value detection ─────────────────────────────────────────────────────────


def _transaction_from_url(url: str) -> TransactionType:
    for pattern, tx in _TRANSACTION_FROM_URL.items():
        if pattern in url:
            return tx
    return TransactionType.UNKNOWN


def _materialize_prices(
    *,
    raw: str,
    qualifier_raw: str | None,
    amount_pence: int | None,
    transaction_type: TransactionType,
) -> tuple[Price | None, RentPrice | None]:
    if not raw and amount_pence is None:
        return None, None

    qualifier_source = " ".join(filter(None, [raw, qualifier_raw])).lower()
    qualifier = _detect_qualifier(qualifier_source)

    if transaction_type == TransactionType.RENT or any(k in raw.lower() for k in _RENT_PERIOD_MAP):
        period = _detect_rent_period(raw.lower())
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw or (qualifier_raw or ""),
            period=period,
        )

    if transaction_type == TransactionType.SHARED_OWNERSHIP or "shared ownership" in qualifier_source:
        qualifier = PriceQualifier.SHARED_OWNERSHIP_FROM if qualifier == PriceQualifier.UNKNOWN else qualifier

    return (
        Price(amount_pence=amount_pence, qualifier=qualifier, raw=raw or (qualifier_raw or "")),
        None,
    )


def _detect_qualifier(lowered: str) -> PriceQualifier:
    for phrase, qualifier in sorted(_QUALIFIER_MAP.items(), key=lambda kv: -len(kv[0])):
        if phrase in lowered:
            return qualifier
    return PriceQualifier.UNKNOWN


def _detect_rent_period(lowered: str) -> RentPeriod:
    for phrase, period in _RENT_PERIOD_MAP.items():
        if phrase in lowered:
            return period
    return RentPeriod.UNKNOWN


def _detect_tenure(blob_lower: str) -> Tenure:
    lowered = blob_lower.lower()
    for phrase, tenure in sorted(
        _TENURE_TOKEN_MAP.items(), key=lambda kv: -len(kv[0])
    ):
        if phrase in lowered:
            return tenure
    return Tenure.UNKNOWN


def _detect_features(*, blob: str, url: str | None) -> list[ListingFeature]:
    lowered = blob.lower()
    features: list[ListingFeature] = []
    if url and "/new-homes/" in url:
        features.append(ListingFeature.NEW_HOME)
    for token, feature in _FEATURE_TOKEN_MAP.items():
        if token in lowered and feature not in features:
            features.append(feature)
    return list(dict.fromkeys(features))


def _infer_property_type(raw: str) -> PropertyType:
    lowered = raw.lower()
    for phrase, ptype in _PROPERTY_TYPE_HINTS:
        if phrase in lowered:
            return ptype
    return PropertyType.OTHER


def _extract_full_postcode(text: str) -> str | None:
    match = _POSTCODE_RE.search(text.upper())
    if match and match.group(2):
        return f"{match.group(1)} {match.group(2)}"
    return None


def _extract_postcode_outcode(text: str) -> str | None:
    match = _POSTCODE_RE.search(text.upper())
    return match.group(1) if match else None


def _extract_price_pence(raw: str) -> int | None:
    match = _PRICE_AMOUNT_RE.search(raw)
    if not match:
        return None
    cleaned = match.group(1).replace(",", "")
    try:
        amount_pounds = float(cleaned)
    except ValueError:
        return None
    return round(amount_pounds * 100)


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "")
    match = _INT_RE.search(cleaned)
    return int(match.group(1)) if match else None


def _first_text(root: Node, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = root.css_first(selector)
        if node is None:
            continue
        text = node.text(strip=True)
        if text:
            return text
    return None


def _clean_whitespace(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _absolutize(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _ZOOPLA_ORIGIN + href
    return href


def _strip_query(url: str) -> str:
    if "?" in url:
        url = url.split("?", 1)[0]
    if "#" in url:
        url = url.split("#", 1)[0]
    if not url.endswith("/"):
        url += "/"
    return url


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s).astimezone() if "+" in s or "Z" in s else datetime.fromisoformat(s)
    except ValueError:
        return None


def _ld_property_value_int(ld: dict, name: str) -> int | None:
    props = ld.get("additionalProperty") or []
    if not isinstance(props, list):
        return None
    for prop in props:
        if not isinstance(prop, dict):
            continue
        if prop.get("name") == name:
            value = prop.get("value")
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                return _parse_int(value)
    return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _derive_address_from_ld_name(name: str | None) -> str | None:
    """JSON-LD ``name`` is '<beds> bed <type> for sale <address>' — split off address."""
    if not name:
        return None
    lowered = name.lower()
    for marker in (" for sale ", " to rent ", " for rent "):
        idx = lowered.find(marker)
        if idx != -1:
            return name[idx + len(marker) :].strip() or None
    return None


def _build_raw_fields(**fields: str | int | None) -> dict[str, str]:
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}
