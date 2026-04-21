"""Tests for the Rightmove parser.

Assertions use real HTML fixtures captured from Rightmove in April 2026.
"""

from __future__ import annotations

import pytest
from uk_property_scrapers.rightmove import (
    extract_listing_urls,
    parse_detail_page,
    parse_search_results,
)
from uk_property_scrapers.schema import (
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


class TestExtractListingUrls:
    def test_finds_all_sale_detail_urls(self, rightmove_search_html: str) -> None:
        urls = extract_listing_urls(rightmove_search_html)
        assert len(urls) == 25
        for url in urls:
            assert url.startswith("https://www.rightmove.co.uk/properties/")
            assert url.count("/properties/") == 1

    def test_urls_are_deduplicated(self, rightmove_search_html: str) -> None:
        urls = extract_listing_urls(rightmove_search_html)
        assert len(urls) == len(set(urls))

    def test_absolutizes_relative_hrefs(self) -> None:
        html = """
        <html><body>
            <a href="/properties/123#/?channel=RES_BUY">A</a>
            <a href="https://www.rightmove.co.uk/properties/456">B</a>
        </body></html>
        """
        assert extract_listing_urls(html) == [
            "https://www.rightmove.co.uk/properties/123",
            "https://www.rightmove.co.uk/properties/456",
        ]

    def test_strips_fragment_and_query(self) -> None:
        html = (
            '<a href="/properties/42?utm_source=x#/?channel=RES_BUY">Listing</a>'
        )
        assert extract_listing_urls(html) == [
            "https://www.rightmove.co.uk/properties/42",
        ]

    def test_returns_empty_on_non_matching_html(self) -> None:
        assert extract_listing_urls("<html><body><p>hello</p></body></html>") == []


class TestParseSearchResults:
    def test_finds_all_listings_in_real_fixture(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        assert len(listings) == 25

    def test_every_listing_is_typed_search_card(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        assert all(listing.listing_type == ListingType.SEARCH_CARD for listing in listings)

    def test_every_listing_has_source_rightmove(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        assert all(listing.source == Source.RIGHTMOVE for listing in listings)

    def test_every_listing_has_numeric_source_id(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            assert listing.source_id.isdigit(), f"non-numeric id: {listing.source_id}"
            assert int(listing.source_id) > 1_000_000

    def test_every_listing_has_address(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            assert listing.address.raw, f"empty address for {listing.source_id}"

    def test_sale_transaction_type_applied(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        assert all(
            listing.transaction_type == TransactionType.SALE for listing in listings
        )

    def test_sale_listings_have_price_not_rent(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        priced = [listing for listing in listings if listing.sale_price is not None]
        assert len(priced) >= len(listings) * 0.9, "at least 90% should be priced"
        for listing in priced:
            assert listing.rent_price is None

    def test_prices_are_in_pence_and_positive(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            if listing.sale_price and listing.sale_price.amount_pence is not None:
                assert listing.sale_price.amount_pence > 10_000 * 100, (
                    f"suspiciously cheap £{listing.sale_price.amount_pence / 100}"
                )
                assert listing.sale_price.amount_pence < 100_000_000 * 100

    def test_url_infers_transaction_when_unknown(self, rightmove_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.UNKNOWN
        )
        for listing in listings:
            assert str(listing.source_url).endswith(listing.source_id)
            assert listing.transaction_type == TransactionType.SALE


class TestSpecificRealListing:
    """Featured card #0 — Harrison Drive flat (listing #173261858)."""

    TARGET_ID = "173261858"

    @pytest.fixture(scope="class")
    def target(self, rightmove_search_html: str) -> Listing:
        listings = parse_search_results(
            rightmove_search_html, transaction_type=TransactionType.SALE
        )
        candidates = [lst for lst in listings if lst.source_id == self.TARGET_ID]
        assert candidates, (
            f"listing {self.TARGET_ID} missing — fixture captured {len(listings)} listings"
        )
        return candidates[0]

    def test_url_canonical(self, target: Listing) -> None:
        assert str(target.source_url) == (
            f"https://www.rightmove.co.uk/properties/{self.TARGET_ID}"
        )

    def test_price_is_550k_oieo(self, target: Listing) -> None:
        assert target.sale_price is not None
        assert target.sale_price.amount_pence == 55_000_000
        assert target.sale_price.qualifier == PriceQualifier.OFFERS_IN_EXCESS_OF
        assert "£550,000" in target.sale_price.raw

    def test_amenities(self, target: Listing) -> None:
        assert target.bedrooms == 3
        assert target.bathrooms == 3

    def test_address_postcode(self, target: Listing) -> None:
        assert "Harrison Drive" in target.address.raw
        assert "Cambridge" in target.address.raw
        assert target.address.postcode_outcode == "CB2"

    def test_features(self, target: Listing) -> None:
        assert ListingFeature.FEATURED in target.features
        assert ListingFeature.VIDEO_TOUR in target.features
        assert ListingFeature.REDUCED in target.features

    def test_property_type(self, target: Listing) -> None:
        assert target.property_type == PropertyType.FLAT

    def test_agent(self, target: Listing) -> None:
        assert target.agent is not None
        assert target.agent.name == "Hockeys"
        assert target.agent.branch == "Cambridge"
        assert target.agent.url is not None
        assert "estate-agents/agent/Hockeys" in str(target.agent.url)

    def test_has_summary(self, target: Listing) -> None:
        assert target.summary is not None
        assert len(target.summary) > 20

    def test_has_images(self, target: Listing) -> None:
        assert len(target.image_urls) >= 1
        for img in target.image_urls:
            assert str(img.url).startswith("https://media.rightmove.co.uk")

    def test_image_count(self, target: Listing) -> None:
        assert target.image_count == 23


class TestRentalParsing:
    def test_rent_listings_use_rent_price(self, rightmove_rent_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_rent_search_html, transaction_type=TransactionType.RENT
        )
        assert len(listings) == 25
        for listing in listings:
            assert listing.transaction_type == TransactionType.RENT
            assert listing.sale_price is None
            if listing.rent_price is not None:
                assert listing.rent_price.amount_pence is None or listing.rent_price.amount_pence > 0

    def test_at_least_one_rent_has_pcm_period(
        self, rightmove_rent_search_html: str
    ) -> None:
        listings = parse_search_results(
            rightmove_rent_search_html, transaction_type=TransactionType.RENT
        )
        pcm = [
            listing
            for listing in listings
            if listing.rent_price and listing.rent_price.period == RentPeriod.PER_MONTH
        ]
        assert pcm, "expected at least one rental with PCM period"

    def test_rent_price_amount_reasonable(self, rightmove_rent_search_html: str) -> None:
        listings = parse_search_results(
            rightmove_rent_search_html, transaction_type=TransactionType.RENT
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


class TestParseDetailPage:
    @pytest.fixture(scope="class")
    def detail(self, rightmove_detail_html: str) -> Listing:
        listing = parse_detail_page(
            rightmove_detail_html, transaction_type=TransactionType.SALE
        )
        assert listing is not None
        return listing

    def test_is_detail_type(self, detail: Listing) -> None:
        assert detail.listing_type == ListingType.DETAIL

    def test_source(self, detail: Listing) -> None:
        assert detail.source == Source.RIGHTMOVE
        assert detail.source_id == "173261858"

    def test_title(self, detail: Listing) -> None:
        assert detail.title is not None
        assert "Harrison Drive" in detail.title
        assert "CB2" in detail.title

    def test_address(self, detail: Listing) -> None:
        assert detail.address.raw is not None
        assert "Harrison Drive" in detail.address.raw
        assert detail.address.postcode_outcode == "CB2"

    def test_price(self, detail: Listing) -> None:
        assert detail.sale_price is not None
        assert detail.sale_price.amount_pence == 55_000_000
        assert detail.sale_price.qualifier == PriceQualifier.OFFERS_IN_EXCESS_OF

    def test_amenities(self, detail: Listing) -> None:
        assert detail.bedrooms == 3
        assert detail.bathrooms == 3

    def test_property_type(self, detail: Listing) -> None:
        assert detail.property_type == PropertyType.FLAT

    def test_tenure(self, detail: Listing) -> None:
        assert detail.tenure == Tenure.LEASEHOLD

    def test_description_non_trivial(self, detail: Listing) -> None:
        assert detail.description is not None
        assert len(detail.description) > 200

    def test_has_images(self, detail: Listing) -> None:
        assert len(detail.image_urls) >= 1

    # ── Enrichment fields lifted from PAGE_MODEL ─────────────────────────

    def test_first_listed_from_analytics(self, detail: Listing) -> None:
        # analyticsProperty.added = "20260313" → 13 March 2026.
        assert detail.first_listed_at is not None
        assert detail.first_listed_at.year == 2026
        assert detail.first_listed_at.month == 3
        assert detail.first_listed_at.day == 13

    def test_council_tax_band(self, detail: Listing) -> None:
        assert detail.council_tax_band == "F"

    def test_timeline_captures_reduction_and_listing(
        self, detail: Listing
    ) -> None:
        kinds = [event.kind for event in detail.timeline]
        assert PropertyTimelineEventKind.REDUCED in kinds
        assert PropertyTimelineEventKind.LISTED in kinds
        reductions = [
            e
            for e in detail.timeline
            if e.kind == PropertyTimelineEventKind.REDUCED
        ]
        assert reductions, "expected at least one reduction"
        first_reduction = reductions[0]
        # "Reduced on 13/04/2026"
        assert first_reduction.occurred_at is not None
        assert first_reduction.occurred_at.year == 2026
        assert first_reduction.occurred_at.month == 4
        assert first_reduction.occurred_at.day == 13
        assert first_reduction.price_pence == 55_000_000

    def test_lease_from_page_model(self, detail: Listing) -> None:
        assert detail.lease is not None
        # Ground rent £450/year, service charge £3559/year in livingCosts.
        assert detail.lease.ground_rent_pence_per_year == 450 * 100
        assert detail.lease.service_charge_pence_per_year == 3559 * 100

    def test_epc_from_key_features(self, detail: Listing) -> None:
        assert detail.epc is not None
        assert detail.epc.current == "B"

    def test_agent_enriched_from_customer_block(self, detail: Listing) -> None:
        agent = detail.agent
        assert agent is not None
        assert agent.name == "Hockeys, Cambridge"
        assert agent.branch == "Cambridge"
        assert agent.group_name == "Hockeys"
        assert agent.phone == "01223 972878"
        assert agent.source_id == "211166"
        assert agent.address is not None
        assert "Mill Road" in agent.address
        assert agent.url is not None
        assert "/estate-agents/agent/Hockeys/Cambridge-211166.html" in str(
            agent.url
        )

    def test_material_information_bundle(self, detail: Listing) -> None:
        mi = detail.material_information
        assert mi is not None
        assert mi.council_tax_band == "F"
        assert mi.tenure == Tenure.LEASEHOLD
        assert mi.epc is not None
        assert mi.lease is not None
        assert mi.parking_raw == "Yes"


class TestRobustness:
    def test_empty_html_returns_empty_list(self) -> None:
        assert parse_search_results("") == []
        assert parse_search_results("<html></html>") == []

    def test_non_rightmove_html_returns_empty(self) -> None:
        html = "<html><body><div>Hello</div></body></html>"
        assert parse_search_results(html) == []

    def test_synthetic_minimal_card_parses(self) -> None:
        html = """
        <html><body>
        <div data-testid="propertyCard-0">
            <a href="/properties/999#/?channel=RES_BUY" class="propertyCard-link">
                <div data-testid="property-price">£450,000 Offers over</div>
                <div data-testid="property-address">
                    <address>12 Trumpington Road, Cambridge CB2 9AB</address>
                </div>
                <div data-testid="property-information">
                    <span class="PropertyInformation_propertyType__x" aria-label="House">Terraced</span>
                    <span class="PropertyInformation_bedroomsCount__x" aria-label="3 in property">3</span>
                    <div class="PropertyInformation_bathContainer__x"><span>2</span></div>
                </div>
                <img src="https://media.rightmove.co.uk/dir/x.jpeg" alt="" />
            </a>
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
