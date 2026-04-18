"""Parser for OnTheMarket search-results and detail HTML.

Selectors were verified against live captures (April 2026 fixtures under
``tests/fixtures/onthemarket/``). OnTheMarket is a Next.js site with Tailwind
utility classes; stable hooks are ``data-component`` attributes, microdata
``itemprop`` values, and the ``result-{id}`` / ``result-{id}-spotlight`` list
item ids on search pages.

All functions are pure: they accept an HTML ``str`` and return Pydantic models.
"""

from __future__ import annotations

import re
from typing import Final

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

_OTM_ORIGIN: Final = "https://www.onthemarket.com"

_DETAIL_PATH_RE: Final = re.compile(r"/details/(\d+)(?:/|[?#]|$)")

_TRANSACTION_FROM_URL: Final[dict[str, TransactionType]] = {
    "/for-sale/": TransactionType.SALE,
    "/to-rent/": TransactionType.RENT,
    "/new-homes/": TransactionType.SALE,
}

# ── Text-level patterns (aligned with Zoopla parser helpers) ────────────────

_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_INT_RE: Final = re.compile(r"(\d+)")

_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)(?:\s+([0-9][A-Z]{2}))?\b"
)

_ARTICLE_TITLE_RE: Final = re.compile(
    r"^View the details for\s+(.+?)\s+-\s+(\d+)\s+bedroom\s+(.+?)\s+for\s+(sale|rent|let)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_H1_SUMMARY_RE: Final = re.compile(
    r"^(\d+)\s+bedroom\s+(.+?)\s+for\s+(sale|rent|let)\s*$",
    re.IGNORECASE,
)

_TITLE_TAG_ADDRESS_RE: Final = re.compile(
    r"\s+in\s+(.+?)\s*\|\s*OnTheMarket\s*$",
    re.IGNORECASE,
)

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
    "shared ownership": PriceQualifier.SHARED_OWNERSHIP_FROM,
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

_TENURE_TOKEN_MAP: Final[dict[str, Tenure]] = {
    "freehold": Tenure.FREEHOLD,
    "leasehold": Tenure.LEASEHOLD,
    "share of freehold": Tenure.SHARE_OF_FREEHOLD,
    "commonhold": Tenure.COMMONHOLD,
    "feuhold": Tenure.FEUHOLD,
}

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
    "new today": ListingFeature.NEW_LISTING,
    "just added": ListingFeature.NEW_LISTING,
    "featured": ListingFeature.FEATURED,
    "spotlight property": ListingFeature.PREMIUM,
    "spotlight": ListingFeature.PREMIUM,
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


# ── Public API ───────────────────────────────────────────────────────────────


def extract_listing_urls(html: str) -> list[str]:
    """Return de-duplicated OnTheMarket listing detail URLs found in the HTML."""
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []

    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        canonical = _canonical_detail_url_from_href(href)
        if canonical is None:
            continue
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
    """Parse an OnTheMarket search-results page into SEARCH_CARD listings."""
    tree = HTMLParser(html)
    cards = _find_listing_cards(tree)
    listings: list[Listing] = []
    for card in cards:
        listing = _parse_search_card(card, hinted_type=transaction_type)
        if listing is not None:
            listings.append(listing)
    return listings


def parse_detail_page(
    html: str,
    *,
    source_url: str | None = None,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> Listing | None:
    """Parse an OnTheMarket property detail page into a single DETAIL Listing."""
    tree = HTMLParser(html)
    url = source_url or _extract_canonical_detail_url(tree)
    if not url:
        return None

    source_id = _extract_listing_id(url)
    if source_id is None:
        return None

    tx = (
        transaction_type
        if transaction_type != TransactionType.UNKNOWN
        else _transaction_from_detail_page(tree, url)
    )

    address_raw = _parse_detail_address(tree)
    if not address_raw:
        address_raw = _parse_detail_address_from_document_title(tree)
    if not address_raw:
        return None

    h1 = tree.css_first("h1")
    h1_text = _clean_whitespace(h1.text(strip=True)) if h1 else None

    beds, baths, property_type_raw_from_h1, tx_from_h1 = _parse_h1_summary(h1_text)
    if tx == TransactionType.UNKNOWN and tx_from_h1 != TransactionType.UNKNOWN:
        tx = tx_from_h1

    price_raw, qualifier_raw = _parse_detail_price(tree)
    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    sale_price, rent_price = _materialize_prices(
        raw=price_raw or "",
        qualifier_raw=qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    beds_micro = _parse_int(_first_text(tree, ['[itemprop="numberOfBedrooms"]']))
    if beds_micro is not None:
        beds = beds_micro
    if baths is None:
        baths = _parse_detail_bathrooms(tree)

    if not property_type_raw_from_h1 and h1_text:
        property_type_raw_from_h1 = _parse_property_type_phrase_from_h1(h1_text)

    property_type = (
        _infer_property_type(property_type_raw_from_h1)
        if property_type_raw_from_h1
        else PropertyType.UNKNOWN
    )

    description = _parse_detail_description(tree)
    summary = description[:280] + "…" if description and len(description) > 280 else description

    image_urls = _parse_detail_images(tree)
    tenure = _parse_detail_tenure(tree)
    agent = _parse_detail_agent(tree)
    features = _parse_detail_features(tree)
    if features and rent_price is not None and ListingFeature.AUCTION in features:
        features.remove(ListingFeature.AUCTION)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    return Listing(
        source=Source.ONTHEMARKET,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        listing_type=ListingType.DETAIL,
        transaction_type=tx,
        sale_price=sale_price,
        rent_price=rent_price,
        property_type=property_type,
        property_type_raw=property_type_raw_from_h1,
        bedrooms=beds,
        bathrooms=baths,
        tenure=tenure,
        address=address,
        title=h1_text,
        summary=summary,
        description=description,
        features=features,
        image_urls=image_urls,
        agent=agent,
        raw_site_fields={
            k: v
            for k, v in {
                "price": price_raw,
                "price_qualifier": qualifier_raw,
                "property_type_raw": property_type_raw_from_h1,
            }.items()
            if v
        },
    )


# ── Search card discovery ───────────────────────────────────────────────────


def _find_listing_cards(tree: HTMLParser) -> list[Node]:
    primary = tree.css('ul#maincontent > li[id^="result-"]')
    if primary:
        return [node for node in primary if node.css_first('a[href^="/details/"]')]

    fallback: list[Node] = []
    for node in tree.css('li[id^="result-"]'):
        if node.css_first('article[data-component="search-result-property-card"]'):
            fallback.append(node)
    return fallback


def _parse_search_card(card: Node, *, hinted_type: TransactionType) -> Listing | None:
    url = _find_detail_url(card)
    if url is None:
        return None

    source_id = _source_id_from_li(card) or _extract_listing_id(url)
    if source_id is None:
        return None

    article = card.css_first('article[data-component="search-result-property-card"]') or card
    title_attr = _clean_whitespace(article.attributes.get("title"))
    parsed_title = _parse_article_title_attr(title_attr)

    address_raw = None
    beds: int | None = None
    baths: int | None = None
    property_type_raw: str | None = None
    tx_from_title = TransactionType.UNKNOWN

    if parsed_title:
        address_raw = parsed_title["address"]
        beds = parsed_title["bedrooms"]
        property_type_raw = parsed_title["property_type_raw"]
        tx_from_title = parsed_title["transaction_type"]

    if not address_raw:
        addr_node = card.css_first('address[itemprop="address"] span') or card.css_first(
            'address[itemprop="address"]'
        )
        address_raw = _clean_whitespace(addr_node.text(strip=True)) if addr_node else None

    if not address_raw:
        return None

    tx = hinted_type if hinted_type != TransactionType.UNKNOWN else tx_from_title
    if tx == TransactionType.UNKNOWN:
        tx = _transaction_from_url(url)

    price_el = card.css_first('[data-component="price-title"]')
    price_text = _clean_whitespace(price_el.text(strip=True)) if price_el else None
    price_qualifier_raw: str | None = None
    if price_el is not None:
        inner_div = price_el.css_first("div")
        if inner_div is not None:
            price_qualifier_raw = _clean_whitespace(inner_div.text(strip=True))

    if beds is None or baths is None:
        bb = card.css_first('[data-component="BedBathCounts"]')
        if bb is not None:
            spans = bb.css("span")
            if beds is None and spans:
                beds = _parse_int(spans[0].text(strip=True))
            if baths is None and len(spans) > 1:
                baths = _parse_int(spans[1].text(strip=True))

    if not property_type_raw:
        summary_line = card.css_first("div.text-sm.text-denim")
        raw_line = _clean_whitespace(summary_line.text(strip=True)) if summary_line else None
        if raw_line:
            property_type_raw = _parse_property_type_from_summary_line(raw_line)

    if not property_type_raw:
        property_type_raw = _parse_property_type_phrase_from_h1(address_raw)

    property_type = (
        _infer_property_type(property_type_raw) if property_type_raw else PropertyType.UNKNOWN
    )

    summary_line = card.css_first("div.text-sm.text-denim")
    summary = _clean_whitespace(summary_line.text(strip=True)) if summary_line else None

    tenure = _parse_card_tenure(card)
    pills_text = [p.text(strip=True) for p in card.css('[data-component="pill"]')]
    extra_spans = " ".join(
        _clean_whitespace(s.text(strip=True)) or ""
        for s in card.css(".text-sm.leading-relaxed.text-slate span")
    )
    tenure = tenure if tenure != Tenure.UNKNOWN else _detect_tenure(extra_spans)

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_text or "",
                    summary or "",
                    " ".join(pills_text),
                    extra_spans,
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

    images: list[Image] = []
    img = card.css_first('img[src^="https://media.onthemarket.com/properties/"]')
    if img is not None:
        src = img.attributes.get("src") or ""
        if src.startswith("http"):
            images.append(Image(url=src))  # type: ignore[arg-type]

    agent = _parse_card_agent(card)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    return Listing(
        source=Source.ONTHEMARKET,
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
        agent=agent,
        raw_site_fields=_build_raw_fields(
            price=price_text,
            price_qualifier=price_qualifier_raw,
            property_type=property_type_raw,
            pills=" | ".join(pills_text) if pills_text else None,
        ),
    )


# ── Field extractors ─────────────────────────────────────────────────────────


def _find_detail_url(card: Node) -> str | None:
    article = card.css_first('article[data-component="search-result-property-card"]') or card
    for anchor in article.css('a[href^="/details/"]'):
        href = anchor.attributes.get("href", "") or ""
        canonical = _canonical_detail_url_from_href(href)
        if canonical:
            return canonical
    return None


def _source_id_from_li(card: Node) -> str | None:
    lid = card.attributes.get("id", "") or ""
    if not lid.startswith("result-"):
        return None
    rest = lid[len("result-") :]
    if rest.endswith("-spotlight"):
        rest = rest[: -len("-spotlight")]
    match = _INT_RE.match(rest)
    return match.group(1) if match else None


def _extract_listing_id(url: str) -> str | None:
    match = _DETAIL_PATH_RE.search(url)
    return match.group(1) if match else None


def _extract_canonical_detail_url(tree: HTMLParser) -> str | None:
    for anchor in tree.css('a[href^="/details/"]'):
        href = anchor.attributes.get("href") or ""
        canonical = _canonical_detail_url_from_href(href)
        if canonical and "/photos/" not in canonical:
            return canonical
    return None


def _canonical_detail_url_from_href(href: str) -> str | None:
    if "/details/" not in href:
        return None
    if href.startswith("http") and "onthemarket.com" not in href:
        return None
    match = _DETAIL_PATH_RE.search(href)
    if not match:
        return None
    listing_id = match.group(1)
    return _strip_query(f"{_OTM_ORIGIN}/details/{listing_id}/")


def _parse_article_title_attr(title: str | None) -> dict[str, object] | None:
    if not title:
        return None
    m = _ARTICLE_TITLE_RE.match(title.strip())
    if not m:
        return None
    addr, beds_s, ptype, tx_word = m.group(1), m.group(2), m.group(3), m.group(4).lower()
    tx = TransactionType.RENT if tx_word in {"rent", "let"} else TransactionType.SALE
    return {
        "address": _clean_whitespace(addr),
        "bedrooms": int(beds_s),
        "property_type_raw": ptype.strip().lower(),
        "transaction_type": tx,
    }


def _parse_card_tenure(card: Node) -> Tenure:
    blob = " ".join(
        _clean_whitespace(s.text(strip=True)) or ""
        for s in card.css(".text-sm.leading-relaxed.text-slate span")
    )
    return _detect_tenure(blob)


def _parse_card_agent(card: Node | None) -> Agent | None:
    if card is None:
        return None
    if card.attributes.get("data-component") == "agent-panel":
        panel = card
    else:
        panel = card.css_first('[data-component="agent-panel"]')
    if panel is None:
        return None
    logo = panel.css_first("img[alt]")
    alt = logo.attributes.get("alt") if logo else ""
    alt = alt.strip() if alt else ""
    if not alt:
        return None
    src = logo.attributes.get("src") if logo else None
    name: str | None
    branch: str | None
    if " - " in alt:
        name, branch = (seg.strip() for seg in alt.split(" - ", 1))
    else:
        name, branch = alt or None, None
    phone_el = panel.css_first('a[itemprop="telephone"][href^="tel:"]')
    phone = None
    if phone_el is not None:
        href = phone_el.attributes.get("href") or ""
        if href.lower().startswith("tel:"):
            phone = href[4:].strip() or None
    return Agent(
        name=name,
        branch=branch,
        phone=phone,
        logo_url=src if src and src.startswith("http") else None,  # type: ignore[arg-type]
    )


def _parse_detail_address(tree: HTMLParser) -> str | None:
    prop = tree.css_first('[data-component="property"]')
    if prop is None:
        return None
    for div in prop.css("div.text-slate.text-body2"):
        text = _clean_whitespace(div.text(strip=True))
        if text and _POSTCODE_RE.search(text.upper()):
            return text
    return None


def _parse_detail_address_from_document_title(tree: HTMLParser) -> str | None:
    tnode = tree.css_first("title")
    raw = _clean_whitespace(tnode.text(strip=True)) if tnode else None
    if not raw:
        return None
    m = _TITLE_TAG_ADDRESS_RE.search(raw)
    if m:
        return _clean_whitespace(m.group(1))
    # e.g. "Pepys Court, Cambridge... 2 bed flat for sale - £425,000"
    if "..." in raw:
        return _clean_whitespace(raw.split("...", 1)[0])
    return None


def _parse_h1_summary(h1: str | None) -> tuple[int | None, int | None, str | None, TransactionType]:
    if not h1:
        return None, None, None, TransactionType.UNKNOWN
    m = _H1_SUMMARY_RE.match(h1.strip())
    if not m:
        return None, None, None, TransactionType.UNKNOWN
    beds = int(m.group(1))
    ptype = m.group(2).strip().lower()
    tx_word = m.group(3).lower()
    tx = TransactionType.RENT if tx_word in {"rent", "let"} else TransactionType.SALE
    return beds, None, ptype, tx


def _parse_property_type_phrase_from_h1(text: str) -> str | None:
    m = _H1_SUMMARY_RE.match(text.strip())
    if m:
        return m.group(2).strip().lower()
    return None


def _parse_property_type_from_summary_line(line: str) -> str | None:
    lowered = line.lower()
    for marker in (" for sale", " to rent", " for rent"):
        if marker in lowered:
            before = lowered.split(marker, 1)[0].strip()
            parts = before.split()
            if len(parts) >= 2 and parts[0].isdigit():
                return " ".join(parts[1:])
            return before
    return None


def _parse_detail_price(tree: HTMLParser) -> tuple[str | None, str | None]:
    prop = tree.css_first('[data-component="property"]')
    if prop is not None:
        raw_html = prop.html
        cut = raw_html.find("Key information")
        snippet_html = raw_html if cut == -1 else raw_html[:cut]
        snippet_tree = HTMLParser(snippet_html)
        head = snippet_tree.text(separator=" ", strip=True)
        qual, amt = _split_price_qualifier_amount(head)
        if amt:
            return amt, qual

    title_node = tree.css_first("title")
    title_text = title_node.text(strip=True) if title_node else ""
    if "£" in title_text:
        m = _PRICE_AMOUNT_RE.search(title_text)
        if m:
            raw = _clean_whitespace(m.group(0))
            return raw, None

    blob = " ".join(
        _clean_whitespace(n.text(strip=True)) or ""
        for n in tree.css('h1, h2, meta[name="description"]')
    )
    qual, amt = _split_price_qualifier_amount(blob)
    if amt:
        return amt, qual

    lowered = tree.html.lower()
    for phrase in ("asking price", "guide price", "offers in excess of", "fixed price"):
        idx = lowered.find(phrase)
        if idx == -1:
            continue
        window = tree.html[idx : idx + 120]
        m = _PRICE_AMOUNT_RE.search(window)
        if m:
            return m.group(0).replace(" ", ""), phrase.title()

    return None, None


def _split_price_qualifier_amount(blob: str) -> tuple[str | None, str | None]:
    if not blob:
        return None, None
    lowered = blob.lower()
    qualifier: str | None = None
    for phrase in sorted(_QUALIFIER_MAP.keys(), key=len, reverse=True):
        if phrase in lowered:
            qualifier = phrase.title() if phrase != phrase.lower() else phrase
            break
    m = _PRICE_AMOUNT_RE.search(blob)
    if not m:
        return qualifier, None
    raw = _clean_whitespace(m.group(0))
    return qualifier, raw


def _parse_detail_bathrooms(tree: HTMLParser) -> int | None:
    prop = tree.css_first('[data-component="property"]')
    if prop is None:
        return None
    text = prop.text(separator=" ", strip=True)
    m = re.search(r"(\d+)\s*beds?\s*(\d+)\s*baths?", text, re.IGNORECASE)
    if m:
        return int(m.group(2))
    return None


def _parse_detail_description(tree: HTMLParser) -> str | None:
    node = tree.css_first('[itemprop="description"]')
    if node is None:
        return None
    for junk in node.css("script, style"):
        junk.decompose()
    return _clean_whitespace(node.text(separator="\n"))


def _parse_detail_images(tree: HTMLParser) -> list[Image]:
    images: list[Image] = []
    for img in tree.css('img[src^="https://media.onthemarket.com/properties/"]'):
        src = img.attributes.get("src") or ""
        if src.startswith("http"):
            images.append(Image(url=src))  # type: ignore[arg-type]
            break
    return images


def _parse_detail_tenure(tree: HTMLParser) -> Tenure:
    prop = tree.css_first('[data-component="property"]')
    chunks: list[str] = []
    if prop is not None:
        chunks.append(prop.text(separator=" ", strip=True).lower())
    chunks.append(tree.html.lower())
    return _detect_tenure(" ".join(chunks))


def _parse_detail_agent(tree: HTMLParser) -> Agent | None:
    panel = tree.css_first('[data-component="agent-panel"]')
    if panel is None:
        return None
    return _parse_card_agent(panel)


def _parse_detail_features(tree: HTMLParser) -> list[ListingFeature]:
    prop = tree.css_first('[data-component="property"]')
    blob = prop.text(separator=" ", strip=True) if prop else ""
    return _detect_features(blob=blob, url=None)


def _transaction_from_url(url: str) -> TransactionType:
    lowered = url.lower()
    for pattern, tx in _TRANSACTION_FROM_URL.items():
        if pattern in lowered:
            return tx
    return TransactionType.UNKNOWN


def _transaction_from_detail_page(tree: HTMLParser, url: str) -> TransactionType:
    tx = _transaction_from_url(url)
    if tx != TransactionType.UNKNOWN:
        return tx
    h1 = tree.css_first("h1")
    h1_text = _clean_whitespace(h1.text(strip=True)) if h1 else None
    _, _, _, tx_h1 = _parse_h1_summary(h1_text)
    if tx_h1 != TransactionType.UNKNOWN:
        return tx_h1
    title_node = tree.css_first("title")
    title_text = (title_node.text(strip=True) if title_node else "").lower()
    if "to rent" in title_text or " to let" in title_text:
        return TransactionType.RENT
    if "for sale" in title_text:
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

    if transaction_type == TransactionType.RENT or any(
        k in raw.lower() for k in _RENT_PERIOD_MAP
    ):
        period = _detect_rent_period(raw.lower())
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


def _first_text(root: Node | None, selectors: list[str]) -> str | None:
    if root is None:
        return None
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


def _strip_query(url: str) -> str:
    if "?" in url:
        url = url.split("?", 1)[0]
    if "#" in url:
        url = url.split("#", 1)[0]
    if not url.endswith("/"):
        url += "/"
    return url


def _build_raw_fields(**fields: str | int | None) -> dict[str, str]:
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}
