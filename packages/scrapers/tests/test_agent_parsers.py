"""Cross-portal tests for agent-branch parsers.

Each portal keeps its agent branch page in a different spot in the Next.js
hydration payload, but the :class:`AgentProfile` / :class:`AgentStockSummary`
contract is the same. These tests:

* Lock in minimum-viable field coverage per portal (name, phone, address,
  bio, stock summary, image/logo URLs).
* Assert the listing-card adapter produces well-formed :class:`Listing`
  instances pointing back at the right portal origin.
* Guard against silent regressions by pinning the agent id + canonical
  URL scheme. Hash-style CSS module changes don't break these tests
  because parsing is driven by the structured JSON, not CSS selectors.
"""

from __future__ import annotations

from uk_property_scrapers.onthemarket import (
    parse_branch_page as otm_parse_branch_page,
    parse_branch_stock as otm_parse_branch_stock,
)
from uk_property_scrapers.rightmove import (
    parse_branch_page as rm_parse_branch_page,
    parse_branch_stock as rm_parse_branch_stock,
)
from uk_property_scrapers.schema import (
    AgentProfile,
    ListingFeature,
    ListingType,
    Source,
    TransactionType,
)
from uk_property_scrapers.zoopla import (
    parse_branch_page as zp_parse_branch_page,
    parse_branch_stock as zp_parse_branch_stock,
)


# ── Zoopla ───────────────────────────────────────────────────────────────────


class TestZooplaAgent:
    def test_profile_parsed(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert isinstance(profile, AgentProfile)
        assert profile.source == Source.ZOOPLA
        assert profile.source_id == "1855"
        assert str(profile.source_url).rstrip("/").endswith(
            "find-agents/branch/connells-cambourne-cambridge-1855"
        )

    def test_display_name_split(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert profile.name == "Connells - Cambourne"
        assert profile.group_name == "Connells"
        assert profile.branch == "Cambourne"

    def test_contact_details(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert profile.phone == "01954 716003"
        assert profile.address is not None and "Cambourne" in profile.address
        assert profile.logo_url is not None

    def test_bio_stripped(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert profile.bio is not None
        assert "<p" not in profile.bio
        assert "Connells" in profile.bio

    def test_trade_bodies_from_memberships(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert "ARLA Propertymark Protected" in profile.trade_bodies
        assert "The Property Ombudsman" in profile.trade_bodies

    def test_socials_populated(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert profile.socials.get("facebook") is not None

    def test_stock_summary(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert profile.stock is not None
        assert profile.stock.for_sale == 75
        assert profile.stock.median_price_pence == 33_442_300

    def test_raw_site_fields_flag_types(self, zoopla_agent_html: str) -> None:
        profile = zp_parse_branch_page(zoopla_agent_html)
        assert profile is not None
        assert profile.raw_site_fields.get("branch_type_sales") == "true"
        assert profile.raw_site_fields.get("branch_type_lettings") == "true"
        assert profile.raw_site_fields.get("latlng", "").startswith("52.")

    def test_stock_returns_listings(self, zoopla_agent_html: str) -> None:
        stock = zp_parse_branch_stock(zoopla_agent_html)
        assert len(stock) >= 5
        for listing in stock:
            assert listing.source == Source.ZOOPLA
            assert listing.listing_type == ListingType.SEARCH_CARD
            assert str(listing.source_url).startswith("https://www.zoopla.co.uk/")
            assert listing.agent is not None
            assert listing.agent.source_id == "1855"


# ── Rightmove ────────────────────────────────────────────────────────────────


class TestRightmoveAgent:
    def test_profile_parsed(self, rightmove_agent_html: str) -> None:
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert isinstance(profile, AgentProfile)
        assert profile.source == Source.RIGHTMOVE
        assert profile.source_id == "211166"
        assert "/estate-agents/agent/" in str(profile.source_url)

    def test_display_name_split(self, rightmove_agent_html: str) -> None:
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert profile is not None
        assert profile.name == "Hockeys, Cambridge"
        assert profile.group_name == "Hockeys"
        assert profile.branch == "Cambridge"

    def test_address_flattened(self, rightmove_agent_html: str) -> None:
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert profile is not None
        assert profile.address == "10 Mill Road, Cambridge, CB1 2AD"

    def test_sales_phone_preferred_over_switchboard(
        self, rightmove_agent_html: str
    ) -> None:
        # Hockeys ships both: sales ``01223 972878`` + main ``01223 356054``.
        # The sales number is what the branch actually answers for buy-side
        # inquiries generated from this MCP, so we must surface that one.
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert profile is not None
        assert profile.phone == "01223 972878"

    def test_bio_text(self, rightmove_agent_html: str) -> None:
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert profile is not None
        assert profile.bio is not None
        assert "Hockey" in profile.bio

    def test_logo_url_absolutised(self, rightmove_agent_html: str) -> None:
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert profile is not None
        assert profile.logo_url is not None
        assert str(profile.logo_url).startswith("https://media.rightmove.co.uk/")

    def test_stock_summary_live_and_sold(self, rightmove_agent_html: str) -> None:
        profile = rm_parse_branch_page(rightmove_agent_html)
        assert profile is not None
        stock = profile.stock
        assert stock is not None
        assert stock.for_sale == 160
        assert stock.to_rent is None
        assert stock.sold_stc == 8
        assert stock.sold_in_last_12m == 8
        assert stock.median_price_pence is not None
        assert stock.median_price_pence > 10_000_000

    def test_stock_returns_listings(self, rightmove_agent_html: str) -> None:
        stock = rm_parse_branch_stock(rightmove_agent_html)
        assert len(stock) >= 10
        live_cards = [l for l in stock if ListingFeature.SOLD_STC not in l.features]
        sstc_cards = [l for l in stock if ListingFeature.SOLD_STC in l.features]
        assert live_cards, "expected at least one live listing in stock"
        assert sstc_cards, "expected sstc-tagged previous-sold listings in stock"
        for listing in stock:
            assert listing.source == Source.RIGHTMOVE
            assert listing.listing_type == ListingType.SEARCH_CARD
            assert listing.transaction_type == TransactionType.SALE
            assert listing.agent is not None
            assert listing.agent.source_id == "211166"


# ── OnTheMarket ──────────────────────────────────────────────────────────────


class TestOnTheMarketAgent:
    def test_profile_parsed(self, onthemarket_agent_html: str) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert isinstance(profile, AgentProfile)
        assert profile.source == Source.ONTHEMARKET
        assert profile.source_id == "73259"
        assert str(profile.source_url).endswith("/agents/branch/abbotts-cambridge/")

    def test_display_name_split(self, onthemarket_agent_html: str) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert profile.name == "Abbotts - Cambridge"
        assert profile.group_name == "Abbotts"
        assert profile.branch == "Cambridge"

    def test_address_and_phone(self, onthemarket_agent_html: str) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert profile.address == "60 Regent Street, Cambridge, CB2 1DP"
        assert profile.phone == "01223 784074"

    def test_bio_preserves_descriptive_copy(
        self, onthemarket_agent_html: str
    ) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert profile.bio is not None
        assert "trusted name" in profile.bio.lower()
        # ``<br />`` and ``\xa0`` should both be stripped.
        assert "<br" not in profile.bio
        assert "\xa0" not in profile.bio

    def test_website_redirect_captured(self, onthemarket_agent_html: str) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert profile.website is not None
        assert "agents/website-redirect" in str(profile.website)

    def test_logo_url(self, onthemarket_agent_html: str) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert profile.logo_url is not None
        assert str(profile.logo_url).startswith("https://media.onthemarket.com/")

    def test_stock_summary_sale_and_rent(self, onthemarket_agent_html: str) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert profile.stock is not None
        assert profile.stock.for_sale == 6
        assert profile.stock.to_rent == 6
        assert profile.stock.median_price_pence is not None
        assert profile.stock.median_rent_pence_per_month is not None

    def test_raw_site_fields_expose_search_urls(
        self, onthemarket_agent_html: str
    ) -> None:
        profile = otm_parse_branch_page(onthemarket_agent_html)
        assert profile is not None
        assert "search_for_sale_url" in profile.raw_site_fields
        assert "search_to_rent_url" in profile.raw_site_fields
        assert profile.raw_site_fields.get("latlng", "").startswith("52.")

    def test_stock_listings_mix_sale_and_rent(
        self, onthemarket_agent_html: str
    ) -> None:
        stock = otm_parse_branch_stock(onthemarket_agent_html)
        sale = [l for l in stock if l.transaction_type == TransactionType.SALE]
        rent = [l for l in stock if l.transaction_type == TransactionType.RENT]
        assert sale and rent
        for listing in stock:
            assert listing.source == Source.ONTHEMARKET
            assert listing.listing_type == ListingType.SEARCH_CARD
            assert listing.agent is not None
            assert listing.agent.source_id == "73259"


# ── Cross-portal invariants ─────────────────────────────────────────────────


def test_all_three_portals_emit_agent_profile(
    zoopla_agent_html: str,
    rightmove_agent_html: str,
    onthemarket_agent_html: str,
) -> None:
    """All three portals must produce a non-null profile with the core fields
    we advertise in the MCP tool schema. This is a contract test — if a
    portal's markup drifts and the parser starts returning ``None``, the
    corresponding MCP tool will silently emit zero results, and we want
    this test to fail loudly instead.
    """
    for html, parse_fn in (
        (zoopla_agent_html, zp_parse_branch_page),
        (rightmove_agent_html, rm_parse_branch_page),
        (onthemarket_agent_html, otm_parse_branch_page),
    ):
        profile = parse_fn(html)
        assert profile is not None
        assert profile.source_id
        assert profile.name
        assert profile.address
        assert profile.phone
        assert profile.bio
