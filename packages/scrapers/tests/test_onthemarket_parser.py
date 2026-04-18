"""Tests for the OnTheMarket parser.

Selectors were verified against real HTML fixtures captured live in April 2026.
Rental-specific assertions are omitted because no OTM rental HTML fixture exists yet.
"""

from __future__ import annotations

import pytest
from uk_property_scrapers.onthemarket import (
    extract_listing_urls,
    parse_detail_page,
    parse_search_results,
)
from uk_property_scrapers.schema import (
    Listing,
    ListingFeature,
    ListingType,
    PriceQualifier,
    PropertyType,
    Source,
    Tenure,
    TransactionType,
)


class TestExtractListingUrls:
    def test_finds_all_sale_detail_urls(self, onthemarket_search_html: str) -> None:
        urls = extract_listing_urls(onthemarket_search_html)
        assert len(urls) == 28
        for url in urls:
            assert url.startswith("https://www.onthemarket.com/")
            assert "/details/" in url

    def test_urls_are_deduplicated(self, onthemarket_search_html: str) -> None:
        urls = extract_listing_urls(onthemarket_search_html)
        assert len(urls) == len(set(urls))

    def test_absolutizes_relative_hrefs(self) -> None:
        html = """
        <html><body>
            <a href="/details/123/">A</a>
            <a href="/details/456/?modal=sign-in">B</a>
            <a href="https://www.onthemarket.com/details/789/">C</a>
        </body></html>
        """
        assert extract_listing_urls(html) == [
            "https://www.onthemarket.com/details/123/",
            "https://www.onthemarket.com/details/456/",
            "https://www.onthemarket.com/details/789/",
        ]

    def test_strips_querystring(self) -> None:
        html = '<a href="/details/42/?utm_source=newsletter&page=1">Listing</a>'
        assert extract_listing_urls(html) == [
            "https://www.onthemarket.com/details/42/",
        ]

    def test_returns_empty_on_non_matching_html(self) -> None:
        assert extract_listing_urls("<html><body><p>hello</p></body></html>") == []


class TestParseSearchResults:
    def test_finds_all_listings_in_real_fixture(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        assert len(listings) == 28

    def test_every_listing_is_typed_search_card(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        assert all(listing.listing_type == ListingType.SEARCH_CARD for listing in listings)

    def test_every_listing_has_source_onthemarket(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        assert all(listing.source == Source.ONTHEMARKET for listing in listings)

    def test_every_listing_has_numeric_source_id(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            assert listing.source_id.isdigit(), f"non-numeric id: {listing.source_id}"
            assert int(listing.source_id) > 1_000_000

    def test_every_listing_has_address(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            assert listing.address.raw, f"empty address for {listing.source_id}"

    def test_sale_transaction_type_applied(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        assert all(
            listing.transaction_type == TransactionType.SALE for listing in listings
        )

    def test_sale_listings_have_price_not_rent(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        priced = [listing for listing in listings if listing.sale_price is not None]
        assert len(priced) >= len(listings) * 0.9, "at least 90% should be priced"
        for listing in priced:
            assert listing.rent_price is None

    def test_prices_are_in_pence_and_positive(self, onthemarket_search_html: str) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        for listing in listings:
            if listing.sale_price and listing.sale_price.amount_pence is not None:
                assert listing.sale_price.amount_pence > 10_000 * 100, (
                    f"suspiciously cheap £{listing.sale_price.amount_pence / 100}"
                )
                assert listing.sale_price.amount_pence < 100_000_000 * 100

    def test_transaction_inferred_from_card_when_hint_unknown(
        self, onthemarket_search_html: str
    ) -> None:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.UNKNOWN
        )
        assert all(listing.transaction_type == TransactionType.SALE for listing in listings)


class TestSpecificRealListing:
    """Listing #18999957 — Pepys Court flat captured on the Cambridge search page."""

    TARGET_ID = "18999957"

    @pytest.fixture(scope="class")
    def target(self, onthemarket_search_html: str) -> Listing:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        candidates = [lst for lst in listings if lst.source_id == self.TARGET_ID]
        assert candidates, (
            f"listing {self.TARGET_ID} missing — fixture captured {len(listings)} listings"
        )
        return candidates[0]

    def test_url_canonical(self, target: Listing) -> None:
        assert str(target.source_url) == (
            f"https://www.onthemarket.com/details/{self.TARGET_ID}/"
        )

    def test_price_is_425k(self, target: Listing) -> None:
        assert target.sale_price is not None
        assert target.sale_price.amount_pence == 42_500_000
        assert "£425,000" in target.sale_price.raw

    def test_bedrooms_bathrooms(self, target: Listing) -> None:
        assert target.bedrooms == 2
        assert target.bathrooms == 2

    def test_property_type_flat(self, target: Listing) -> None:
        assert target.property_type == PropertyType.FLAT

    def test_address(self, target: Listing) -> None:
        assert "Pepys Court" in target.address.raw
        assert "Cambridge" in target.address.raw
        assert target.address.postcode_outcode == "CB4"

    def test_tenure(self, target: Listing) -> None:
        assert target.tenure == Tenure.LEASEHOLD

    def test_agent(self, target: Listing) -> None:
        assert target.agent is not None
        assert target.agent.name == "Abbotts"
        assert target.agent.branch == "Cambridge"
        assert target.agent.phone == "01223 784074"

    def test_features_reduced(self, target: Listing) -> None:
        assert ListingFeature.REDUCED in target.features

    def test_has_primary_image(self, target: Listing) -> None:
        assert len(target.image_urls) >= 1
        assert "media.onthemarket.com/properties/" in str(target.image_urls[0].url)


class TestSpotlightListing:
    """Spotlight card uses ``result-{id}-spotlight`` and a guide-price layout."""

    @pytest.fixture(scope="class")
    def spotlight(self, onthemarket_search_html: str) -> Listing:
        listings = parse_search_results(
            onthemarket_search_html, transaction_type=TransactionType.SALE
        )
        found = [lst for lst in listings if lst.source_id == "18946831"]
        assert found
        return found[0]

    def test_spotlight_id_normalised(self, spotlight: Listing) -> None:
        assert spotlight.source_id == "18946831"

    def test_guide_price_qualifier(self, spotlight: Listing) -> None:
        assert spotlight.sale_price is not None
        assert spotlight.sale_price.qualifier == PriceQualifier.GUIDE_PRICE

    def test_spotlight_premium_flag(self, spotlight: Listing) -> None:
        assert ListingFeature.PREMIUM in spotlight.features


class TestParseDetailPage:
    @pytest.fixture(scope="class")
    def detail(self, onthemarket_detail_html: str) -> Listing:
        listing = parse_detail_page(
            onthemarket_detail_html,
            source_url="https://www.onthemarket.com/details/18999957/",
        )
        assert listing is not None
        return listing

    def test_is_detail_type(self, detail: Listing) -> None:
        assert detail.listing_type == ListingType.DETAIL

    def test_source(self, detail: Listing) -> None:
        assert detail.source == Source.ONTHEMARKET
        assert detail.source_id == "18999957"

    def test_title_h1(self, detail: Listing) -> None:
        assert detail.title is not None
        assert "2" in detail.title
        assert "flat" in detail.title.lower()

    def test_address(self, detail: Listing) -> None:
        assert "Pepys Court" in detail.address.raw
        assert detail.address.postcode_outcode == "CB4"

    def test_price(self, detail: Listing) -> None:
        assert detail.sale_price is not None
        assert detail.sale_price.amount_pence == 42_500_000

    def test_bedrooms_bathrooms(self, detail: Listing) -> None:
        assert detail.bedrooms == 2
        assert detail.bathrooms == 2

    def test_property_type(self, detail: Listing) -> None:
        assert detail.property_type == PropertyType.FLAT

    def test_tenure(self, detail: Listing) -> None:
        assert detail.tenure == Tenure.LEASEHOLD

    def test_description_non_trivial(self, detail: Listing) -> None:
        assert detail.description is not None
        assert len(detail.description) > 100

    def test_has_images(self, detail: Listing) -> None:
        assert len(detail.image_urls) >= 1

    def test_parses_without_explicit_source_url(self, onthemarket_detail_html: str) -> None:
        listing = parse_detail_page(onthemarket_detail_html)
        assert listing is not None
        assert listing.source_id == "18999957"


class TestRobustness:
    def test_empty_search_returns_empty_list(self) -> None:
        assert parse_search_results("") == []
        assert parse_search_results("<html></html>") == []

    def test_non_otm_html_returns_empty(self) -> None:
        html = "<html><body><div>Hello</div></body></html>"
        assert parse_search_results(html) == []

    def test_detail_empty_returns_none(self) -> None:
        assert parse_detail_page("") is None
        assert parse_detail_page("<html></html>") is None

    def test_synthetic_minimal_card_parses(self) -> None:
        html = """
        <html><body>
        <ul id="maincontent">
            <li id="result-999">
                <article data-component="search-result-property-card"
                    title="View the details for 12 High Street, Cambridge, CB1 2AB -
                    3 bedroom terraced house for sale">
                    <div data-component="price-title">
                        <div class="text-xs">Offers over</div>
                        <a href="/details/999/">£450,000</a>
                    </div>
                    <address itemprop="address" itemscope>
                        <span>12 High Street, Cambridge, CB1 2AB</span>
                    </address>
                    <div data-component="BedBathCounts">
                        <span itemprop="numberOfBedrooms">3</span>
                        <span>2</span>
                    </div>
                </article>
            </li>
        </ul>
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
        assert listing.address.postcode == "CB1 2AB"
        assert listing.address.postcode_outcode == "CB1"
        assert listing.property_type == PropertyType.TERRACED
