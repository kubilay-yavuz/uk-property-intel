"""Canonical cross-source schema for UK property listings.

Every parser (Zoopla, Rightmove, OnTheMarket, auctions) returns instances of these
models, so downstream consumers (the agent, MCPs, Apify actors, SaaS API) work
with one unified type regardless of where the data came from.

Design principles:
    * Currency amounts are integers in *pence* (GBP * 100) to avoid floating-point
      drift. Floor areas are integers in square feet.
    * All free-text fields keep the raw site-specific wording alongside a
      normalized enum when parseable (e.g. ``property_type_raw`` + ``property_type``).
    * Optional fields are ``None`` when the site doesn't expose them, never empty strings.
    * Lat/lng are WGS84 decimal degrees.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Source(StrEnum):
    """Which site the listing was scraped from."""

    ZOOPLA = "zoopla"
    RIGHTMOVE = "rightmove"
    ONTHEMARKET = "onthemarket"


class TransactionType(StrEnum):
    """Buy vs rent vs auction vs shared ownership."""

    SALE = "sale"
    RENT = "rent"
    AUCTION = "auction"
    SHARED_OWNERSHIP = "shared_ownership"
    RETIREMENT = "retirement"
    UNKNOWN = "unknown"


class PropertyType(StrEnum):
    """Normalized property type enum used across all sources."""

    DETACHED = "detached"
    SEMI_DETACHED = "semi_detached"
    TERRACED = "terraced"
    END_OF_TERRACE = "end_of_terrace"
    FLAT = "flat"
    APARTMENT = "apartment"
    MAISONETTE = "maisonette"
    BUNGALOW = "bungalow"
    COTTAGE = "cottage"
    LAND = "land"
    COMMERCIAL = "commercial"
    PARK_HOME = "park_home"
    HOUSEBOAT = "houseboat"
    STUDIO = "studio"
    OTHER = "other"
    UNKNOWN = "unknown"


class Tenure(StrEnum):
    """Ownership tenure — England/Wales freehold/leasehold, Scotland commonhold etc."""

    FREEHOLD = "freehold"
    LEASEHOLD = "leasehold"
    SHARE_OF_FREEHOLD = "share_of_freehold"
    COMMONHOLD = "commonhold"
    FEUHOLD = "feuhold"
    UNKNOWN = "unknown"


class ListingType(StrEnum):
    """What kind of listing result this is — summary card vs full detail page."""

    SEARCH_CARD = "search_card"
    DETAIL = "detail"


class ListingFeature(StrEnum):
    """Common feature flags surfaced on listing cards."""

    NEW_HOME = "new_home"
    REDUCED = "reduced"
    CHAIN_FREE = "chain_free"
    RETIREMENT = "retirement"
    SHARED_OWNERSHIP = "shared_ownership"
    AUCTION = "auction"
    INVESTMENT = "investment"
    PART_BUY_PART_RENT = "part_buy_part_rent"
    NEW_LISTING = "new_listing"
    FEATURED = "featured"
    PROPERTY_OF_THE_WEEK = "property_of_the_week"
    PREMIUM = "premium"
    SOLD_STC = "sold_stc"
    UNDER_OFFER = "under_offer"
    TENANTED_INVESTMENT = "tenanted_investment"
    OPEN_DAY = "open_day"
    VIDEO_TOUR = "video_tour"
    VIRTUAL_TOUR = "virtual_tour"


class LatLng(BaseModel):
    """WGS84 decimal degrees coordinate."""

    model_config = ConfigDict(frozen=True)

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class Address(BaseModel):
    """Address as surfaced by the source site.

    Listing portals are deliberately vague with addresses pre-sale; we capture the
    raw display string and a postcode when visible. Geocoding to full OS-grade
    address happens in the enricher layer, not here.
    """

    raw: str = Field(..., description="Address string as displayed on the listing.")
    postcode_outcode: str | None = Field(
        None, description="Postcode outcode only if full postcode not shown, e.g. 'CB1'."
    )
    postcode: str | None = Field(
        None, description="Full postcode if available, e.g. 'CB1 2QA'."
    )


class PriceQualifier(StrEnum):
    """How the listed price should be interpreted."""

    GUIDE_PRICE = "guide_price"
    OFFERS_OVER = "offers_over"
    OFFERS_IN_EXCESS_OF = "offers_in_excess_of"
    OFFERS_IN_REGION = "offers_in_region"
    FIXED_PRICE = "fixed_price"
    FROM = "from"
    POA = "poa"
    SHARED_OWNERSHIP_FROM = "shared_ownership_from"
    ASKING_PRICE = "asking_price"
    UNKNOWN = "unknown"


class Price(BaseModel):
    """Price expressed in pence, with optional qualifier."""

    amount_pence: int | None = Field(
        None, description="Amount in pence (GBP * 100). None if POA or not parseable."
    )
    qualifier: PriceQualifier = PriceQualifier.UNKNOWN
    raw: str = Field(..., description="Original price string from the listing, e.g. 'OIEO £450,000'.")


class RentPeriod(StrEnum):
    """Per-period rent cadence."""

    PER_MONTH = "per_month"
    PER_WEEK = "per_week"
    PER_DAY = "per_day"
    PER_YEAR = "per_year"
    UNKNOWN = "unknown"


class RentPrice(Price):
    """Rental price has a cadence as well as an amount."""

    period: RentPeriod = RentPeriod.UNKNOWN


class Agent(BaseModel):
    """Estate/lettings agent associated with the listing."""

    name: str | None = None
    phone: str | None = None
    branch: str | None = None
    url: HttpUrl | None = None
    logo_url: HttpUrl | None = None


class Image(BaseModel):
    """Listing photo."""

    url: HttpUrl
    caption: str | None = None


class Listing(BaseModel):
    """Canonical normalized listing — the shape every downstream consumer sees."""

    model_config = ConfigDict(extra="forbid", ser_json_bytes="utf8")

    # Provenance
    source: Source
    source_id: str = Field(..., description="Listing ID as assigned by the source site.")
    source_url: HttpUrl
    listing_type: ListingType
    scraped_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())

    # Transaction
    transaction_type: TransactionType = TransactionType.UNKNOWN

    # Price — exactly one of these is populated based on transaction_type
    sale_price: Price | None = None
    rent_price: RentPrice | None = None

    # Physical attributes
    property_type: PropertyType = PropertyType.UNKNOWN
    property_type_raw: str | None = Field(
        None, description="Original type string from the listing, e.g. 'Terraced house'."
    )
    bedrooms: int | None = Field(None, ge=0, le=100)
    bathrooms: int | None = Field(None, ge=0, le=100)
    reception_rooms: int | None = Field(None, ge=0, le=100)
    floor_area_sqft: int | None = Field(None, ge=0, le=100_000)
    tenure: Tenure = Tenure.UNKNOWN

    # Location
    address: Address
    coords: LatLng | None = None

    # Descriptive
    title: str | None = None
    summary: str | None = None
    description: str | None = Field(
        None, description="Full listing description. Only populated for DETAIL listings."
    )
    features: list[ListingFeature] = Field(default_factory=list)
    image_urls: list[Image] = Field(default_factory=list)
    image_count: int | None = Field(
        None, ge=0, description="Total images available on the source, even if we only captured the first."
    )

    # Commercial
    agent: Agent | None = None

    # Timestamps on the source site (when available)
    first_listed_at: datetime | None = None
    last_updated_at: datetime | None = None

    # Raw passthrough — always keep what the site gave us, for debugging and future re-parses
    raw_site_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Site-specific raw values not yet normalized. Keys are snake_case.",
    )


# ---------------------------------------------------------------------------
# Auction lots
# ---------------------------------------------------------------------------
#
# Auctions are their own animal: the primary key is (auction house, catalogue
# date, lot number), not a single listing ID; prices are almost always a guide
# range rather than a point; the sale method (traditional / modern / online /
# conditional) materially changes the bidding mechanics and completion timeline.
# Rather than overload :class:`Listing` with fields that are meaningless for
# non-auction listings, auctions get their own top-level model.


class AuctionHouse(StrEnum):
    """Which auctioneer the lot was scraped from."""

    ALLSOP = "allsop"
    AUCTION_HOUSE_UK = "auction_house_uk"
    SAVILLS_AUCTIONS = "savills_auctions"
    IAMSOLD = "iamsold"


class AuctionSaleMethod(StrEnum):
    """How the lot is being sold.

    ``TRADITIONAL`` is the classic "hammer falls, 10% exchange on the day, 28
    days to complete" English auction. ``MODERN`` (aka Modern Method of
    Auction) gives buyers 56 days and usually a reservation agreement. Online
    timed auctions share the traditional completion window but have no live
    floor. ``CONDITIONAL`` is Savills' term for an auction with a contractual
    condition such as planning.
    """

    TRADITIONAL = "traditional"
    MODERN = "modern"
    ONLINE_TIMED = "online_timed"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class AuctionLotStatus(StrEnum):
    """Lifecycle state as surfaced on the catalogue page."""

    AVAILABLE = "available"
    UNDER_OFFER = "under_offer"
    SOLD_PRIOR = "sold_prior"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    POSTPONED = "postponed"
    UNSOLD = "unsold"
    UNKNOWN = "unknown"


class AuctionGuidePrice(BaseModel):
    """Guide price as published in the catalogue.

    Guide prices are commonly a *range* (``"£250,000 - £275,000"``) or an
    "excess" (``"In excess of £500,000"``) rather than a single number.
    We capture both endpoints when present; ``low_pence`` alone is populated
    for single-value guides and ``high_pence`` stays ``None``.
    """

    low_pence: int | None = Field(
        None, ge=0, description="Lower end of the guide range, in pence."
    )
    high_pence: int | None = Field(
        None, ge=0, description="Upper end of the guide range, in pence."
    )
    qualifier: PriceQualifier = PriceQualifier.UNKNOWN
    raw: str = Field(..., description="Original guide-price string from the catalogue.")


class AuctionLot(BaseModel):
    """Canonical normalized auction lot — the shape every auction parser emits."""

    model_config = ConfigDict(extra="forbid", ser_json_bytes="utf8")

    # Provenance
    auction_house: AuctionHouse
    source_id: str = Field(
        ...,
        description="Lot ID as assigned by the auction house. Often a catalogue slug or numeric ID.",
    )
    source_url: HttpUrl
    scraped_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())

    # Catalogue position
    catalogue_id: str | None = Field(
        None,
        description="Catalogue / sale identifier (e.g. 'March 2026 Residential').",
    )
    lot_number: str | None = Field(
        None,
        description="Printed lot number. String because some houses use alphanumeric ('12A').",
    )

    # Timing
    auction_date: date | None = Field(
        None, description="Scheduled auction date (local date)."
    )
    auction_end_at: datetime | None = Field(
        None,
        description="For timed online auctions, the close-of-bidding timestamp (UTC).",
    )
    sale_method: AuctionSaleMethod = AuctionSaleMethod.UNKNOWN

    # Status
    status: AuctionLotStatus = AuctionLotStatus.UNKNOWN
    sold_price_pence: int | None = Field(
        None, ge=0, description="Final hammer price, in pence, if published post-sale."
    )

    # Pricing
    guide_price: AuctionGuidePrice | None = None
    reserve_price_pence: int | None = Field(
        None,
        ge=0,
        description="Reserve price if explicitly published (rare — most houses keep this private).",
    )

    # Physical attributes — optional because many auction lots are land / commercial / mixed use
    property_type: PropertyType = PropertyType.UNKNOWN
    property_type_raw: str | None = None
    tenure: Tenure = Tenure.UNKNOWN
    bedrooms: int | None = Field(None, ge=0, le=100)
    bathrooms: int | None = Field(None, ge=0, le=100)
    floor_area_sqft: int | None = Field(None, ge=0, le=1_000_000)

    # Income (investment lots)
    annual_rent_pence: int | None = Field(
        None,
        ge=0,
        description="Annual rent roll in pence if the lot is tenanted/investment.",
    )
    is_vacant_possession: bool | None = Field(
        None,
        description="True if sold with vacant possession, False if tenanted, None if not stated.",
    )

    # Location
    address: Address
    coords: LatLng | None = None

    # Descriptive
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    image_urls: list[Image] = Field(default_factory=list)
    catalogue_pdf_url: HttpUrl | None = Field(
        None, description="Link to the full catalogue PDF if the site exposes one."
    )
    legal_pack_url: HttpUrl | None = Field(
        None,
        description="Link to the legal pack / special conditions (often gated behind login).",
    )

    # Raw passthrough — identical intent to :attr:`Listing.raw_site_fields`.
    raw_site_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Site-specific raw values not yet normalized. Keys are snake_case.",
    )
