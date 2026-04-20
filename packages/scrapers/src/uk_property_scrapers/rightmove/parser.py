"""Parser for Rightmove search and property-detail HTML.

Rightmove's React markup relies heavily on ``data-testid`` attributes, which are
stable across releases. Human-readable class names are often CSS-module hashes;
use ``[class*="PropertyInformation_propertyType"]``-style substring selectors
where class-based targeting is unavoidable.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import ValidationError
from selectolax.parser import HTMLParser, Node

from uk_property_scrapers._common import (
    FLOORPLAN_CAPTION,
    extract_uk_coords,
    is_floorplan_url,
)
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

_RIGHTMOVE_ORIGIN: Final = "https://www.rightmove.co.uk"

_PROPERTIES_PATH_RE: Final = re.compile(r"/properties/(\d+)")
_AGENT_PATH_RE: Final = re.compile(
    r"/estate-agents/agent/([^/]+)/(.+)-(\d+)\.html", re.IGNORECASE
)

_PROPERTY_CARD_TESTID_RE: Final = re.compile(r"^propertyCard-(\d+)$")

# ── Text-level patterns (aligned with Zoopla parser) ─────────────────────────

_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_INT_RE: Final = re.compile(r"(\d+)")

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
    # Note: avoid a bare ``" pa"`` token — it false-matches the word ``page`` on
    # Rightmove detail pages where tooltip copy sits inside the price wrapper.
    " pd": RentPeriod.PER_DAY,
    "per day": RentPeriod.PER_DAY,
}

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
    "featured property": ListingFeature.FEATURED,
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
    """Return de-duplicated canonical Rightmove ``/properties/{id}`` URLs."""
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if "/properties/" not in href:
            continue
        if _PROPERTIES_PATH_RE.search(href) is None:
            continue
        canonical = _canonical_property_url(href)
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
    """Parse a Rightmove search-results page into ``SEARCH_CARD`` listings.

    Individual cards that fail :class:`Listing` validation (eg. a future
    schema-drift quirk) are silently skipped so one bad card doesn't nuke
    the whole page — the counter-pressure on silent drop is the
    ``listings live`` / actor-level smoke, which will notice when a run
    consistently yields zero cards.
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


def _detail_headline_price_text(price_el: Node | None) -> str | None:
    """First ``span`` carrying ``£`` — avoids tooltip / glossary copy in the wrapper."""
    if price_el is None:
        return None
    for span in price_el.css("span"):
        t = _clean_whitespace(span.text(strip=True))
        if t and "£" in t:
            return t
    return _clean_whitespace(price_el.text(strip=True))


def parse_detail_page(
    html: str,
    *,
    source_url: str | None = None,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> Listing | None:
    """Parse a Rightmove property detail page into a single ``DETAIL`` listing."""
    tree = HTMLParser(html)
    url = _detail_canonical_url(tree, source_url)
    if not url:
        return None
    match = _PROPERTIES_PATH_RE.search(url)
    if not match:
        return None
    source_id = match.group(1)

    tx = (
        transaction_type
        if transaction_type != TransactionType.UNKNOWN
        else _transaction_from_url(url, None)
    )

    h1 = tree.css_first("h1")
    address_raw = _clean_whitespace(h1.text(strip=True)) if h1 else None
    if not address_raw:
        return None

    price_el = tree.css_first('[data-testid="primaryPrice"]')
    price_raw = _detail_headline_price_text(price_el)
    qual_el = tree.css_first('[data-testid="priceQualifier"]')
    qualifier_raw = _clean_whitespace(qual_el.text(strip=True)) if qual_el else None

    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    sale_price, rent_price = _materialize_prices(
        raw=price_raw or "",
        qualifier_raw=qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    type_el = tree.css_first('[data-testid="info-reel-PROPERTY_TYPE-text"]')
    property_type_raw = _clean_whitespace(type_el.text(strip=True)) if type_el else None
    property_type = (
        _infer_property_type(property_type_raw.lower())
        if property_type_raw
        else PropertyType.UNKNOWN
    )

    beds_el = tree.css_first('[data-testid="info-reel-BEDROOMS-text"]')
    baths_el = tree.css_first('[data-testid="info-reel-BATHROOMS-text"]')
    beds = _parse_int(_clean_whitespace(beds_el.text(strip=True)) if beds_el else None)
    baths = _parse_int(_clean_whitespace(baths_el.text(strip=True)) if baths_el else None)

    size_el = tree.css_first('[data-testid="info-reel-SIZE-text"]')
    size_raw = _clean_whitespace(size_el.text(strip=True)) if size_el else None
    sqft: int | None = None
    if size_raw and "ask agent" not in size_raw.lower():
        sqft = _parse_int(size_raw)

    tenure_el = tree.css_first('[data-testid="info-reel-tenure-button"]')
    tenure_text = _clean_whitespace(tenure_el.text(strip=True)) if tenure_el else ""
    tenure = _detect_tenure((tenure_text or "").lower())

    title = address_raw
    description = _parse_detail_description(tree)
    image_urls = _parse_detail_property_images(tree, html)
    agent = _parse_detail_agent(tree)
    coords = extract_uk_coords(html)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_raw,
                    qualifier_raw,
                    property_type_raw,
                    tenure_text,
                    description[:500] if description else None,
                ],
            )
        ),
        url=url,
    )

    return Listing(
        source=Source.RIGHTMOVE,
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
        floor_area_sqft=sqft,
        tenure=tenure,
        address=address,
        coords=coords,
        title=title,
        summary=None,
        description=description,
        features=features,
        image_urls=image_urls,
        agent=agent,
        raw_site_fields={
            k: v
            for k, v in {
                "price": price_raw,
                "price_qualifier": qualifier_raw,
                "property_type_raw": property_type_raw,
                "size": size_raw,
            }.items()
            if v
        },
    )


# ── Search card discovery ───────────────────────────────────────────────────


def _find_listing_cards(tree: HTMLParser) -> list[Node]:
    """Prefer ``data-testid="propertyCard-N"`` rows (excludes ``propertyCard-vrt-N``)."""
    cards: list[Node] = []
    for node in tree.css('[data-testid^="propertyCard-"]'):
        tid = node.attributes.get("data-testid") or ""
        if _PROPERTY_CARD_TESTID_RE.match(tid):
            cards.append(node)
    if cards:
        return cards

    synthetic: list[Node] = []
    seen: set[str] = set()
    for anchor in tree.css('a[href*="/properties/"]'):
        href = anchor.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href) is None:
            continue
        canonical = _canonical_property_url(href)
        if canonical in seen:
            continue
        seen.add(canonical)
        synthetic.append(anchor)
    return synthetic


def _parse_search_card(card: Node, *, hinted_type: TransactionType) -> Listing | None:
    url, raw_href = _find_detail_url_and_raw(card)
    if url is None:
        return None

    source_id_match = _PROPERTIES_PATH_RE.search(url)
    if not source_id_match:
        return None
    source_id = source_id_match.group(1)

    tx = (
        hinted_type
        if hinted_type != TransactionType.UNKNOWN
        else _transaction_from_url(url, raw_href)
    )

    price_node = card.css_first('[data-testid="property-price"]')
    price_blob = _clean_whitespace(price_node.text(strip=True)) if price_node else None
    amount_pence, qualifier_tail = _primary_price_and_qualifier_tail(price_blob)

    qualifier_raw = qualifier_tail
    sale_price, rent_price = _materialize_prices(
        raw=price_blob or "",
        qualifier_raw=qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    addr_node = card.css_first('[data-testid="property-address"]')
    address_raw = _clean_whitespace(addr_node.text(strip=True)) if addr_node else None
    if not address_raw:
        return None

    info = card.css_first('[data-testid="property-information"]')
    property_type_raw: str | None = None
    beds: int | None = None
    baths: int | None = None
    if info is not None:
        pt = info.css_first('[class*="PropertyInformation_propertyType"]')
        property_type_raw = _clean_whitespace(pt.text(strip=True)) if pt else None
        bed_el = info.css_first('[class*="PropertyInformation_bedroomsCount"]')
        beds = _parse_int(_clean_whitespace(bed_el.text(strip=True)) if bed_el else None)
        bath_wrap = info.css_first('[class*="PropertyInformation_bathContainer"]')
        if bath_wrap is not None:
            bath_span = bath_wrap.css_first("span")
            baths = _parse_int(
                _clean_whitespace(bath_span.text(strip=True)) if bath_span else None
            )
    # Large counts (>100) are development-block cards ("197 studios available")
    # rather than single properties; keep them as null so the card still emits
    # without tripping the per-listing schema bounds.
    if beds is not None and beds > 100:
        beds = None
    if baths is not None and baths > 100:
        baths = None

    property_type = (
        _infer_property_type(property_type_raw.lower())
        if property_type_raw
        else PropertyType.UNKNOWN
    )

    desc_node = card.css_first('[data-testid="property-description"]')
    summary = _clean_whitespace(desc_node.text(strip=True)) if desc_node else None

    marketed_raw = _clean_whitespace(
        marketed.text(strip=True) if (marketed := card.css_first('[data-testid="marketed-by-text"]')) else None
    )
    status_clean, agent_from_line = _parse_marketed_by_line(marketed_raw)
    link_agent = _parse_agent_from_card(card)
    if agent_from_line and agent_from_line.name:
        if link_agent is not None and link_agent.url is not None:
            agent = Agent(
                name=agent_from_line.name,
                branch=agent_from_line.branch or link_agent.branch,
                url=link_agent.url,
            )
        else:
            agent = agent_from_line
    else:
        agent = link_agent

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_blob,
                    summary,
                    marketed_raw,
                    status_clean,
                ],
            )
        ),
        url=url,
    )

    images, image_count = _parse_card_images(card)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    tenure_blob = " ".join(filter(None, [price_blob, summary, marketed_raw]))
    tenure = _detect_tenure(tenure_blob.lower())

    raw_fields = _build_raw_fields(
        price=price_blob,
        price_qualifier=qualifier_raw,
        marketed_by=marketed_raw,
        property_type=property_type_raw,
    )

    return Listing(
        source=Source.RIGHTMOVE,
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
        tenure=tenure,
        address=address,
        title=address_raw,
        summary=summary,
        features=features,
        image_urls=images,
        image_count=image_count,
        agent=agent,
        raw_site_fields=raw_fields,
    )


# ── Field extractors ─────────────────────────────────────────────────────────


def _find_detail_url_and_raw(card: Node) -> tuple[str | None, str | None]:
    for anchor in card.css('a[href*="/properties/"]'):
        href = anchor.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href):
            return _canonical_property_url(href), href
    if card.tag == "a":
        href = card.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href):
            return _canonical_property_url(href), href
    return None, None


def _parse_card_images(card: Node) -> tuple[list[Image], int | None]:
    images: list[Image] = []
    seen: set[str] = set()
    for img in card.css('img[src^="https://media.rightmove.co.uk"]'):
        src = img.attributes.get("src") or ""
        if src and src not in seen:
            seen.add(src)
            images.append(Image(url=src))  # type: ignore[arg-type]
            break

    max_idx = 0
    for img_node in card.css('[data-testid^="property-image-"]'):
        tid = img_node.attributes.get("data-testid") or ""
        m = re.search(r"property-image-(\d+)$", tid)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    image_count = max_idx if max_idx > 0 else None
    if image_count is None and len(images) > 0:
        image_count = 1
    return images, image_count


def _parse_agent_from_card(card: Node) -> Agent | None:
    link = card.css_first('a[href*="/estate-agents/agent/"]')
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    name, branch, agent_url = _parse_agent_href(href)
    return Agent(
        name=name,
        branch=branch,
        url=agent_url,  # type: ignore[arg-type]
    )


def _parse_agent_href(href: str) -> tuple[str | None, str | None, str | None]:
    u = _strip_fragment_and_query(_absolutize(href))
    if not u.startswith("http"):
        return None, None, None
    path = u.replace(_RIGHTMOVE_ORIGIN, "", 1)
    m = _AGENT_PATH_RE.search(path)
    if not m:
        return None, None, u
    name = m.group(1).replace("-", " ").strip()
    branch_slug = m.group(2).replace("-", " ").strip()
    return name, branch_slug, u


def _parse_marketed_by_line(raw: str | None) -> tuple[str | None, Agent | None]:
    """Split ``Reduced on … by Hockeys, Cambridge`` and strip Rightmove's duplicated tail."""
    if not raw:
        return None, None
    s = raw.strip()
    if " by " not in s:
        return None, None
    status, agent_part = s.split(" by ", 1)
    status = status.strip()
    agent_part = agent_part.strip()
    idx = agent_part.lower().rfind(status.lower())
    if idx > 0:
        agent_part = agent_part[:idx].strip().rstrip(",")

    name: str | None = None
    branch: str | None = None
    if "," in agent_part:
        name, branch = (p.strip() for p in agent_part.split(",", 1))
    else:
        name = agent_part or None
    return status, Agent(name=name, branch=branch) if name else None


def _parse_detail_agent(tree: HTMLParser) -> Agent | None:
    link = tree.css_first('a[href*="/estate-agents/agent/"]')
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    name, branch, url = _parse_agent_href(href)
    return Agent(name=name, branch=branch, url=url)  # type: ignore[arg-type]


def _parse_detail_description(tree: HTMLParser) -> str | None:
    for h2 in tree.css("h2"):
        title = _clean_whitespace(h2.text(strip=True))
        if title and title.lower() == "description":
            container = h2.next
            if container is None or container.tag != "div":
                return None
            for junk in container.css("button"):
                junk.decompose()
            return _clean_whitespace(container.text(separator="\n"))
    return None


_RM_FLOORPLAN_SRC_RE: Final = re.compile(
    r"https://media\.rightmove\.co\.uk/[^\"']*(?:FLP_|floorplan)[^\"']*",
    re.IGNORECASE,
)


def _parse_detail_property_images(tree: HTMLParser, html: str) -> list[Image]:
    """Collect photo + floorplan URLs off a Rightmove detail page.

    Photos are fetched from ``<img src="…/property-photo…">`` nodes in the
    DOM. Floorplans live inside a collapsed ``<details>`` panel that
    Rightmove only expands client-side, so their URLs sit in the raw HTML as
    inline JSON ``"floorplans":[{"url":"…FLP_00…"}]`` rather than in real
    ``<img>`` tags. We scan the raw HTML for those URLs and tag them.
    """
    images: list[Image] = []
    seen: set[str] = set()
    for img in tree.css('img[src*="property-photo"]'):
        src = img.attributes.get("src") or ""
        if src.startswith("http") and src not in seen:
            seen.add(src)
            caption = FLOORPLAN_CAPTION if is_floorplan_url(src) else None
            images.append(Image(url=src, caption=caption))  # type: ignore[arg-type]
        if len(images) >= 30:
            break

    for match in _RM_FLOORPLAN_SRC_RE.finditer(html):
        fp_url = match.group(0)
        if fp_url in seen:
            continue
        seen.add(fp_url)
        images.append(Image(url=fp_url, caption=FLOORPLAN_CAPTION))  # type: ignore[arg-type]
        if len(images) >= 30:
            break
    return images


def _detail_canonical_url(tree: HTMLParser, source_url: str | None) -> str | None:
    link = tree.css_first('link[rel="canonical"][href]')
    if link is not None:
        href = link.attributes.get("href") or ""
        if "/properties/" in href:
            return _canonical_property_url(href)
    meta = tree.css_first('meta[property="og:url"][content]')
    if meta is not None:
        content = meta.attributes.get("content") or ""
        if "/properties/" in content:
            return _canonical_property_url(content)
    if source_url and "/properties/" in source_url:
        return _canonical_property_url(source_url)
    for anchor in tree.css('a[href*="/properties/"]'):
        href = anchor.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href):
            return _canonical_property_url(href)
    return None


def _primary_price_and_qualifier_tail(price_blob: str | None) -> tuple[int | None, str | None]:
    if not price_blob:
        return None, None
    match = _PRICE_AMOUNT_RE.search(price_blob)
    if not match:
        return None, None
    amount = _extract_price_pence(match.group(0))
    tail = price_blob[match.end() :].strip()
    tail = tail or None
    return amount, tail


# ── Value detection (mirrors Zoopla) ─────────────────────────────────────────


def _transaction_from_url(url: str, raw_href: str | None = None) -> TransactionType:
    """Infer sale vs rent from a canonical URL and/or the original href (may contain ``channel=``)."""
    blob = f"{raw_href or ''} {url}".lower()
    if "channel=res_let" in blob or "/property-to-rent/" in blob:
        return TransactionType.RENT
    if (
        "channel=res_buy" in blob
        or "/property-for-sale/" in blob
        or "/new-homes-for-sale/" in blob
    ):
        return TransactionType.SALE
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

    lowered = raw.lower()
    if transaction_type == TransactionType.RENT or any(
        k in lowered for k in _RENT_PERIOD_MAP
    ):
        period = _detect_rent_period(lowered)
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw or (qualifier_raw or ""),
            period=period,
        )

    if transaction_type == TransactionType.SHARED_OWNERSHIP or "shared ownership" in qualifier_source:
        qualifier = (
            PriceQualifier.SHARED_OWNERSHIP_FROM
            if qualifier == PriceQualifier.UNKNOWN
            else qualifier
        )

    return (
        Price(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw or (qualifier_raw or ""),
        ),
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
    for phrase, tenure in sorted(_TENURE_TOKEN_MAP.items(), key=lambda kv: -len(kv[0])):
        if phrase in lowered:
            return tenure
    return Tenure.UNKNOWN


def _detect_features(*, blob: str, url: str | None) -> list[ListingFeature]:
    lowered = blob.lower()
    features: list[ListingFeature] = []
    if url and "/new-homes-for-sale/" in url:
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
    m = _INT_RE.search(cleaned)
    return int(m.group(1)) if m else None


def _clean_whitespace(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _strip_fragment_and_query(url: str) -> str:
    if "#" in url:
        url = url.split("#", 1)[0]
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _canonical_property_url(href: str) -> str:
    return _strip_fragment_and_query(_absolutize(href))


def _absolutize(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _RIGHTMOVE_ORIGIN + href
    return href


def _build_raw_fields(**fields: str | int | None) -> dict[str, str]:
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}
