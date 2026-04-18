"""Shared pytest fixtures for scraper tests.

Fixtures are **real HTML** captured live from each portal via the
``browser-use`` CLI. Filenames encode the region + year-month to make it easy
to refresh them on cadence.
"""

from __future__ import annotations

from pathlib import Path

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
