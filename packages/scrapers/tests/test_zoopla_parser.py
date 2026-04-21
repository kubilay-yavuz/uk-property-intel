"""Tests for the Zoopla parser.

Selectors were derived from live DOM captures; these tests assert against the
real HTML fixtures to catch regressions when Zoopla ships a new release.
"""

from __future__ import annotations

import pytest
from uk_property_scrapers.schema import (
    BroadbandTier,
    Listing,
    ListingFeature,
    ListingType,
    PriceQualifier,
    PropertyTimelineEventKind,
    PropertyType,
    RentPeriod,
    Source,
    Tenure,
    TransactionType,
)
from uk_property_scrapers.zoopla import (
    extract_listing_urls,
    parse_detail_page,
    parse_search_results,
)

# ── URL extraction ──────────────────────────────────────────────────────────


class TestExtractListingUrls:
    def test_finds_all_sale_detail_urls(self, zoopla_search_html: str) -> None:
        urls = extract_listing_urls(zoopla_search_html)
        assert len(urls) >= 20
        for url in urls:
            assert url.startswith("https://www.zoopla.co.uk/")
            assert "/details/" in url

    def test_urls_are_deduplicated(self, zoopla_search_html: str) -> None:
        urls = extract_listing_urls(zoopla_search_html)
        assert len(urls) == len(set(urls))

    def test_no_contact_urls(self, zoopla_search_html: str) -> None:
        urls = extract_listing_urls(zoopla_search_html)
        for url in urls:
            assert "/contact/" not in url
            assert "/enquiry/" not in url

    def test_absolutizes_relative_hrefs(self) -> None:
        html = """
        <html><body>
            <a href="/for-sale/details/123/">A</a>
            <a href="/to-rent/details/456/">B</a>
            <a href="https://www.zoopla.co.uk/for-sale/details/789/">C</a>
        </body></html>
        """
        assert extract_listing_urls(html) == [
            "https://www.zoopla.co.uk/for-sale/details/123/",
            "https://www.zoopla.co.uk/to-rent/details/456/",
            "https://www.zoopla.co.uk/for-sale/details/789/",
        ]

    def test_strips_querystring(self) -> None:
        html = (
            '<a href="/for-sale/details/42/?utm_source=newsletter&page=1">Listing</a>'
        )
        assert extract_listing_urls(html) == [
            "https://www.zoopla.co.uk/for-sale/details/42/",
        ]

    def test_returns_empty_on_non_matching_html(self) -> None:
        assert extract_listing_urls("<html><body><p>hello</p></body></html>") == []


# ── Search-card parsing ────────────────────────────────────────────────────


class TestParseSearchResults:
    def test_finds_all_listings_in_real_fixture(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        assert 25 <= len(listings) <= 35, (
            f"expected ~28 real listings, got {len(listings)}"
        )

    def test_every_listing_is_typed_search_card(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        assert all(listing.listing_type == ListingType.SEARCH_CARD for listing in listings)

    def test_every_listing_has_source_zoopla(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        assert all(listing.source == Source.ZOOPLA for listing in listings)

    def test_every_listing_has_numeric_source_id(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            assert listing.source_id.isdigit(), f"non-numeric id: {listing.source_id}"
            assert int(listing.source_id) > 1_000_000

    def test_every_listing_has_address(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            assert listing.address.raw, f"empty address for {listing.source_id}"

    def test_sale_transaction_type_applied(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        assert all(
            listing.transaction_type == TransactionType.SALE for listing in listings
        )

    def test_sale_listings_have_price_not_rent(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        priced = [listing for listing in listings if listing.sale_price is not None]
        assert len(priced) >= len(listings) * 0.9, "at least 90% should be priced"
        for listing in priced:
            assert listing.rent_price is None

    def test_prices_are_in_pence_and_positive(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            if listing.sale_price and listing.sale_price.amount_pence is not None:
                assert listing.sale_price.amount_pence > 10_000 * 100, (
                    f"suspiciously cheap £{listing.sale_price.amount_pence / 100}"
                )
                assert listing.sale_price.amount_pence < 100_000_000 * 100

    def test_url_infers_transaction_when_unknown(self, zoopla_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.UNKNOWN
        )
        for listing in listings:
            if "/for-sale/" in str(listing.source_url) or "/new-homes/" in str(
                listing.source_url
            ):
                assert listing.transaction_type == TransactionType.SALE
            elif "/to-rent/" in str(listing.source_url):
                assert listing.transaction_type == TransactionType.RENT


class TestSpecificRealListing:
    """Tests on listing #72228361 — a known resale house captured live."""

    TARGET_ID = "72228361"

    @pytest.fixture(scope="class")
    def target(self, zoopla_search_html: str) -> Listing:
        listings = parse_search_results(
            zoopla_search_html, transaction_type=TransactionType.SALE
        )
        candidates = [lst for lst in listings if lst.source_id == self.TARGET_ID]
        assert candidates, (
            f"listing {self.TARGET_ID} missing — fixture captured {len(listings)} listings"
        )
        return candidates[0]

    def test_url_canonical(self, target: Listing) -> None:
        assert str(target.source_url) == (
            f"https://www.zoopla.co.uk/for-sale/details/{self.TARGET_ID}/"
        )

    def test_price_is_600k(self, target: Listing) -> None:
        assert target.sale_price is not None
        assert target.sale_price.amount_pence == 60_000_000
        assert target.sale_price.qualifier == PriceQualifier.GUIDE_PRICE
        assert "£600,000" in target.sale_price.raw

    def test_amenities(self, target: Listing) -> None:
        assert target.bedrooms == 6
        assert target.bathrooms == 3
        assert target.reception_rooms == 2
        assert target.floor_area_sqft == 1894

    def test_address_postcode(self, target: Listing) -> None:
        assert "Apley Way" in target.address.raw
        assert "Lower Cambourne" in target.address.raw
        assert target.address.postcode_outcode == "CB23"

    def test_features(self, target: Listing) -> None:
        assert ListingFeature.REDUCED in target.features
        assert ListingFeature.PROPERTY_OF_THE_WEEK in target.features

    def test_tenure(self, target: Listing) -> None:
        assert target.tenure == Tenure.FREEHOLD

    def test_agent(self, target: Listing) -> None:
        assert target.agent is not None
        assert target.agent.name == "Connells"
        assert target.agent.branch == "Cambourne"

    def test_has_summary(self, target: Listing) -> None:
        assert target.summary is not None
        assert len(target.summary) > 20

    def test_has_images(self, target: Listing) -> None:
        assert len(target.image_urls) >= 1
        for img in target.image_urls:
            assert str(img.url).startswith("https://")

    def test_image_count(self, target: Listing) -> None:
        assert target.image_count == 19


class TestRentalParsing:
    def test_rent_listings_use_rent_price(self, zoopla_rent_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_rent_search_html, transaction_type=TransactionType.RENT
        )
        assert len(listings) >= 10
        for listing in listings:
            assert listing.transaction_type == TransactionType.RENT
            assert listing.sale_price is None
            if listing.rent_price is not None:
                assert listing.rent_price.amount_pence is None or listing.rent_price.amount_pence > 0

    def test_at_least_one_rent_has_pcm_period(
        self, zoopla_rent_search_html: str
    ) -> None:
        listings = parse_search_results(
            zoopla_rent_search_html, transaction_type=TransactionType.RENT
        )
        pcm = [
            listing
            for listing in listings
            if listing.rent_price and listing.rent_price.period == RentPeriod.PER_MONTH
        ]
        assert pcm, "expected at least one rental with PCM period"

    def test_rent_price_amount_reasonable(self, zoopla_rent_search_html: str) -> None:
        listings = parse_search_results(
            zoopla_rent_search_html, transaction_type=TransactionType.RENT
        )
        priced = [
            listing
            for listing in listings
            if listing.rent_price and listing.rent_price.amount_pence
        ]
        assert priced, "expected at least one rental with a priced amount"
        for listing in priced:
            amount = listing.rent_price.amount_pence  # type: ignore[union-attr]
            assert 500 * 100 <= amount <= 100_000 * 100, f"suspicious rent: £{amount / 100}"


# ── Detail page parsing ────────────────────────────────────────────────────


class TestParseDetailPage:
    @pytest.fixture(scope="class")
    def detail(self, zoopla_detail_html: str) -> Listing:
        listing = parse_detail_page(zoopla_detail_html)
        assert listing is not None
        return listing

    def test_is_detail_type(self, detail: Listing) -> None:
        assert detail.listing_type == ListingType.DETAIL

    def test_source(self, detail: Listing) -> None:
        assert detail.source == Source.ZOOPLA
        assert detail.source_id == "72228361"

    def test_title(self, detail: Listing) -> None:
        assert detail.title is not None
        assert "6 bed" in detail.title.lower()
        assert "detached" in detail.title.lower()

    def test_address(self, detail: Listing) -> None:
        assert detail.address.raw is not None
        assert "Apley Way" in detail.address.raw
        assert detail.address.postcode_outcode == "CB23"

    def test_price(self, detail: Listing) -> None:
        assert detail.sale_price is not None
        assert detail.sale_price.amount_pence == 60_000_000

    def test_amenities(self, detail: Listing) -> None:
        assert detail.bedrooms == 6
        assert detail.bathrooms == 3
        assert detail.reception_rooms == 2
        assert detail.floor_area_sqft == 1894

    def test_property_type(self, detail: Listing) -> None:
        assert detail.property_type == PropertyType.DETACHED

    def test_description_non_trivial(self, detail: Listing) -> None:
        assert detail.description is not None
        assert len(detail.description) > 200

    def test_has_images(self, detail: Listing) -> None:
        assert len(detail.image_urls) >= 1

    def test_first_listed_at(self, detail: Listing) -> None:
        assert detail.first_listed_at is not None
        assert detail.first_listed_at.year >= 2020

    def test_tenure_resolved_from_nts_payload(self, detail: Listing) -> None:
        # Fixture listing is Freehold per the ntsInfo block — we must upgrade
        # the tenure past UNKNOWN even when the DOM fallback misses it.
        assert detail.tenure == Tenure.FREEHOLD

    def test_council_tax_band(self, detail: Listing) -> None:
        assert detail.council_tax_band == "F"

    def test_epc(self, detail: Listing) -> None:
        assert detail.epc is not None
        assert detail.epc.current == "C"
        assert "EPC Rating" in detail.epc.raw

    def test_material_information_bundle(self, detail: Listing) -> None:
        mi = detail.material_information
        assert mi is not None
        assert mi.council_tax_band == "F"
        assert mi.tenure == Tenure.FREEHOLD
        assert mi.epc is not None
        # Material Info should surface ancillary NTS fields verbatim
        assert mi.water_raw == "Mains"
        assert mi.electricity_raw == "Mains"
        assert mi.sewerage_raw == "Mains"

    def test_broadband(self, detail: Listing) -> None:
        assert detail.broadband is not None
        # The fixture discloses FTTP technology but no numeric speed
        assert detail.broadband.technology == "FTTP"
        assert detail.broadband.tier == BroadbandTier.ULTRAFAST

    def test_agent_enriched_from_contact_block(self, detail: Listing) -> None:
        assert detail.agent is not None
        assert detail.agent.name == "Connells - Cambourne"
        assert detail.agent.branch == "Cambourne"
        assert detail.agent.group_name == "Connells"
        assert detail.agent.phone == "01954 716003"
        assert detail.agent.source_id == "1855"
        assert detail.agent.url is not None
        assert "/find-agents/branch/" in str(detail.agent.url)

    def test_timeline_has_expected_events(self, detail: Listing) -> None:
        assert detail.timeline, "expected at least one timeline event"
        kinds = [event.kind for event in detail.timeline]
        assert PropertyTimelineEventKind.REDUCED in kinds
        assert PropertyTimelineEventKind.LISTED in kinds

    def test_timeline_price_change(self, detail: Listing) -> None:
        reductions = [
            event
            for event in detail.timeline
            if event.kind == PropertyTimelineEventKind.REDUCED
        ]
        assert reductions, "expected at least one reduction event"
        first_reduction = reductions[0]
        # Price at reduction: £600,000
        assert first_reduction.price_pence == 60_000_000
        # Change: -£50,000 (7.7%)
        assert first_reduction.change_pence == -50_000_00
        assert first_reduction.change_pct is not None
        assert first_reduction.change_pct < 0


# ── Edge cases / robustness ────────────────────────────────────────────────


class TestRobustness:
    def test_empty_html_returns_empty_list(self) -> None:
        assert parse_search_results("") == []
        assert parse_search_results("<html></html>") == []

    def test_non_zoopla_html_returns_empty(self) -> None:
        html = "<html><body><div>Hello</div></body></html>"
        assert parse_search_results(html) == []

    def test_synthetic_minimal_card_parses(self) -> None:
        html = """
        <html><body>
        <div data-testid="regular-listings">
            <div id="listing_999" class="Listings_listingRow__x">
                <a class="lib_detailsPageLink__abc" href="/for-sale/details/999/"
                   data-testid="listing-card-content">
                    <p class="price_priceText__x">£450,000</p>
                    <p class="price_priceTitle__x">Offers over</p>
                    <p class="amenities_amenityListSlim__x">
                        <span class="amenities_amenityItemSlim__x">3 beds</span>
                        <span class="amenities_amenityItemSlim__x">2 baths</span>
                    </p>
                    <address class="summary_address__x">12 Trumpington Road, Cambridge CB2 9AB</address>
                    <p class="summary_summary__x">Charming terraced home ...</p>
                </a>
            </div>
        </div>
        </body></html>
        """
        listings = parse_search_results(html, transaction_type=TransactionType.SALE)
        assert len(listings) == 1
        listing = listings[0]
        assert listing.source_id == "999"
        assert listing.sale_price is not None
        assert listing.sale_price.amount_pence == 45_000_000
        assert listing.sale_price.qualifier == PriceQualifier.OFFERS_OVER
        assert listing.bedrooms == 3
        assert listing.bathrooms == 2
        assert listing.address.postcode == "CB2 9AB"
        assert listing.address.postcode_outcode == "CB2"
        assert listing.property_type == PropertyType.TERRACED

    def test_shared_ownership_card(self) -> None:
        html = """
        <div id="listing_111">
            <a href="/new-homes/details/111/" data-testid="listing-card-content">
                <p class="price_priceText__x">£120,000</p>
                <p class="price_priceTitle__x">Shared ownership from</p>
                <address class="summary_address__x">Flat 7, Mill Road, Cambridge CB1</address>
            </a>
        </div>
        """
        listings = parse_search_results(html)
        assert len(listings) == 1
        listing = listings[0]
        assert listing.sale_price is not None
        assert listing.sale_price.qualifier == PriceQualifier.SHARED_OWNERSHIP_FROM
        assert ListingFeature.NEW_HOME in listing.features

    def test_poa_parses_cleanly(self) -> None:
        html = """
        <div id="listing_222">
            <a href="/for-sale/details/222/" data-testid="listing-card-content">
                <p class="price_priceText__x">POA</p>
                <address class="summary_address__x">Manor House, Grantchester CB3 9NF</address>
            </a>
        </div>
        """
        listings = parse_search_results(html)
        assert len(listings) == 1
        listing = listings[0]
        assert listing.sale_price is not None
        assert listing.sale_price.amount_pence is None
        assert listing.sale_price.qualifier == PriceQualifier.POA

    def test_auction_listing_flagged(self) -> None:
        html = """
        <div id="listing_333">
            <a href="/for-sale/details/333/" data-testid="listing-card-content">
                <p class="price_priceText__x">£175,000</p>
                <p class="price_priceTitle__x">Guide price</p>
                <address class="summary_address__x">Ely Road</address>
                <p class="summary_summary__x">Auction property, no chain</p>
            </a>
        </div>
        """
        listings = parse_search_results(html)
        assert len(listings) == 1
        listing = listings[0]
        assert ListingFeature.AUCTION in listing.features
        assert ListingFeature.CHAIN_FREE in listing.features
