"""Shared pytest fixtures for scraper tests.

Fixtures are **real HTML** captured live from each portal via the
``browser-use`` CLI. Filenames encode the region + year-month to make it easy
to refresh them on cadence.

Allsop is a rare exception: the site is an Angular SPA whose data comes
from a clean JSON API, so those fixtures are real ``/api/search`` +
``/api/auctions/<uuid>`` payloads captured the same day.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def zoopla_search_html() -> str:
    """Live Zoopla for-sale search page (Cambridgeshire, April 2026)."""
    return (FIXTURES_DIR / "zoopla" / "search_cambridgeshire_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def zoopla_rent_search_html() -> str:
    """Live Zoopla to-rent search page (Cambridgeshire, April 2026)."""
    return (FIXTURES_DIR / "zoopla" / "torent_cambridgeshire_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def zoopla_detail_html() -> str:
    """Live Zoopla property detail page (listing #72228361, April 2026)."""
    return (FIXTURES_DIR / "zoopla" / "detail_72228361_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def rightmove_search_html() -> str:
    """Live Rightmove for-sale search page (Cambridge, April 2026)."""
    return (FIXTURES_DIR / "rightmove" / "search_cambridge_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def rightmove_rent_search_html() -> str:
    """Live Rightmove to-rent search page (Cambridge, April 2026)."""
    return (FIXTURES_DIR / "rightmove" / "torent_cambridge_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def rightmove_detail_html() -> str:
    """Live Rightmove property detail page (listing #173261858, April 2026)."""
    return (FIXTURES_DIR / "rightmove" / "detail_173261858_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def onthemarket_search_html() -> str:
    """Live OnTheMarket for-sale search page (Cambridge, April 2026)."""
    return (FIXTURES_DIR / "onthemarket" / "search_cambridge_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def onthemarket_detail_html() -> str:
    """Live OnTheMarket property detail page (listing #18999957, April 2026)."""
    return (FIXTURES_DIR / "onthemarket" / "detail_18999957_2026-04.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture(scope="session")
def allsop_search_payload() -> dict[str, Any]:
    """Live Allsop /api/search payload (April 2026 residential catalogue, 5 lots)."""
    return json.loads(
        (FIXTURES_DIR / "auctions" / "allsop" / "search_2026-04_slice5.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="session")
def allsop_auction_payload() -> dict[str, Any]:
    """Live Allsop /api/auctions/<uuid> payload (April 2026 residential catalogue)."""
    return json.loads(
        (FIXTURES_DIR / "auctions" / "allsop" / "auction_2026-04.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="session")
def allsop_range_payload() -> dict[str, Any]:
    """Live Allsop /api/search slice containing a range guide (e.g. '£550,000 - £600,000')."""
    return json.loads(
        (FIXTURES_DIR / "auctions" / "allsop" / "search_range_slice.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="session")
def allsop_lot_detail_payload() -> dict[str, Any]:
    """Live Allsop /api/lot/reference/<ref> payload (lot r260430-098, April 2026)."""
    return json.loads(
        (FIXTURES_DIR / "auctions" / "allsop" / "lot_detail_2026-04.json").read_text(
            encoding="utf-8"
        )
    )
