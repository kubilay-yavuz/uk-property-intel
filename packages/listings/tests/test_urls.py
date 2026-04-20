"""URL builders for each portal."""

from __future__ import annotations

from uk_property_listings import (
    SearchQuery,
    build_onthemarket_search_url,
    build_rightmove_search_url,
    build_zoopla_search_url,
    build_zoopla_search_url_fallback,
)


class TestZooplaSearchUrl:
    def test_minimum_sale(self) -> None:
        url = build_zoopla_search_url(SearchQuery(location="Cambridge"))
        assert url.startswith("https://www.zoopla.co.uk/for-sale/property/cambridge/")
        assert "q=Cambridge" in url
        assert "search_source=for-sale" in url

    def test_rent_uses_to_rent_path(self) -> None:
        url = build_zoopla_search_url(SearchQuery(location="London", transaction="rent"))
        assert "/to-rent/property/" in url
        assert "search_source=to-rent" in url

    def test_price_and_bed_filters(self) -> None:
        url = build_zoopla_search_url(
            SearchQuery(
                location="Cambridge",
                min_price=200_000,
                max_price=400_000,
                min_beds=2,
                max_beds=3,
            )
        )
        assert "price_min=200000" in url
        assert "price_max=400000" in url
        assert "beds_min=2" in url
        assert "beds_max=3" in url

    def test_pagination(self) -> None:
        query = SearchQuery(location="Cambridge")
        page1 = build_zoopla_search_url(query, page=1)
        page3 = build_zoopla_search_url(query, page=3)
        assert "pn=" not in page1
        assert "pn=3" in page3

    def test_slugifies_multiword_location(self) -> None:
        url = build_zoopla_search_url(SearchQuery(location="Milton Keynes"))
        assert "/milton-keynes/" in url


class TestZooplaSearchUrlFallback:
    def test_drops_slug_keeps_q(self) -> None:
        url = build_zoopla_search_url_fallback(SearchQuery(location="Tiny Hamlet"))
        assert url.startswith("https://www.zoopla.co.uk/for-sale/?")
        assert "q=Tiny%20Hamlet" in url
        assert "/property/" not in url
        assert "search_source=for-sale" in url

    def test_rent_path(self) -> None:
        url = build_zoopla_search_url_fallback(
            SearchQuery(location="Somewhere", transaction="rent")
        )
        assert url.startswith("https://www.zoopla.co.uk/to-rent/?")
        assert "search_source=to-rent" in url

    def test_pagination(self) -> None:
        query = SearchQuery(location="Somewhere")
        assert "pn=" not in build_zoopla_search_url_fallback(query, page=1)
        assert "pn=4" in build_zoopla_search_url_fallback(query, page=4)

    def test_filters_are_preserved(self) -> None:
        url = build_zoopla_search_url_fallback(
            SearchQuery(
                location="Somewhere",
                min_price=250_000,
                max_price=450_000,
                min_beds=2,
                max_beds=4,
            )
        )
        assert "price_min=250000" in url
        assert "price_max=450000" in url
        assert "beds_min=2" in url
        assert "beds_max=4" in url


class TestRightmoveSearchUrl:
    def test_sale_uses_property_for_sale(self) -> None:
        url = build_rightmove_search_url(SearchQuery(location="Cambridge"))
        assert url == "https://www.rightmove.co.uk/property-for-sale/Cambridge.html"

    def test_rent_uses_property_to_rent(self) -> None:
        url = build_rightmove_search_url(SearchQuery(location="Oxford", transaction="rent"))
        assert url == "https://www.rightmove.co.uk/property-to-rent/Oxford.html"

    def test_price_and_bed_filters(self) -> None:
        url = build_rightmove_search_url(
            SearchQuery(location="Cambridge", min_price=200_000, max_price=500_000, min_beds=3)
        )
        assert url.startswith("https://www.rightmove.co.uk/property-for-sale/Cambridge.html?")
        assert "minPrice=200000" in url
        assert "maxPrice=500000" in url
        assert "minBedrooms=3" in url

    def test_pagination_uses_24_step_index(self) -> None:
        query = SearchQuery(location="Cambridge")
        page1 = build_rightmove_search_url(query, page=1)
        page2 = build_rightmove_search_url(query, page=2)
        page4 = build_rightmove_search_url(query, page=4)
        assert "index=" not in page1
        assert "?index=24" in page2
        assert "?index=72" in page4

    def test_multiword_location_is_title_cased(self) -> None:
        url = build_rightmove_search_url(SearchQuery(location="Milton Keynes"))
        assert url == "https://www.rightmove.co.uk/property-for-sale/Milton-Keynes.html"


class TestOnTheMarketSearchUrl:
    def test_sale_path_and_slug(self) -> None:
        url = build_onthemarket_search_url(SearchQuery(location="Cambridge"))
        assert url.startswith("https://www.onthemarket.com/for-sale/property/cambridge/")

    def test_rent_path(self) -> None:
        url = build_onthemarket_search_url(SearchQuery(location="Bristol", transaction="rent"))
        assert url.startswith("https://www.onthemarket.com/to-rent/property/bristol/")

    def test_filters(self) -> None:
        url = build_onthemarket_search_url(
            SearchQuery(location="York", min_price=150_000, max_beds=4)
        )
        assert "min-price=150000" in url
        assert "max-bedrooms=4" in url

    def test_pagination(self) -> None:
        query = SearchQuery(location="York")
        page1 = build_onthemarket_search_url(query, page=1)
        page2 = build_onthemarket_search_url(query, page=2)
        assert "page=" not in page1
        assert "page=2" in page2
