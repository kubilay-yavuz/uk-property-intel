"""Tests for :class:`BGSRadonClient` against the Indicative Atlas ArcGIS REST layer.

The upstream service publishes one 1km grid feature per tile with a
``CLASS_MAX`` value between 1 and 6; we canonicalise that to a compact
``RadonBand`` + verbose ``description`` fragment. These tests mock the
ArcGIS JSON wire format so we're not bound to network access.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis.radon import BGSRadonClient, RadonPotential

_QUERY_URL = re.compile(
    r"https://map\.bgs\.ac\.uk/arcgis/rest/services/GeoIndex_Onshore/radon/MapServer/0/query.*"
)


def _arcgis_response(class_max: int | None, *, description: str | None = None) -> dict:
    """Return a minimal ArcGIS /query response with ``CLASS_MAX`` attribute."""

    attrs: dict[str, object] = {
        "OBJECTID": 999,
        "TILE": "AB1234",
        "VERSION": "Radon Indicative Atlas GB v3",
        "Shape_Length": 4000,
        "Shape_Area": 1_000_000,
    }
    if class_max is not None:
        attrs["CLASS_MAX"] = class_max
    if description is not None:
        attrs["Description"] = description
    return {
        "features": [{"attributes": attrs}] if class_max is not None else [],
    }


@pytest.mark.asyncio
@respx.mock
async def test_potential_very_low_band() -> None:
    """Class 1 collapses to 'very_low' with <1% affected housing."""
    respx.get(_QUERY_URL).mock(
        return_value=httpx.Response(
            200,
            json=_arcgis_response(
                1,
                description=(
                    "All parts of this 1km grid square are in the lowest band "
                    "of radon potential. Less than 1 % of homes above the "
                    "Action Level."
                ),
            ),
        )
    )
    async with BGSRadonClient() as client:
        out = await client.potential_at(51.5, -0.1)
    assert isinstance(out, RadonPotential)
    assert out.class_max == 1
    assert out.band == "very_low"
    assert out.affected_area is False
    assert out.max_pct == pytest.approx(1.0)
    assert out.tile == "AB1234"


@pytest.mark.asyncio
@respx.mock
async def test_potential_high_band() -> None:
    """Class 5 maps to ``moderate_high`` with ``affected_area=True`` and 10..30%."""
    respx.get(_QUERY_URL).mock(
        return_value=httpx.Response(
            200,
            json=_arcgis_response(5),
        )
    )
    async with BGSRadonClient() as client:
        out = await client.potential_at(53.0, -2.0)
    assert out.class_max == 5
    assert out.band == "moderate_high"
    assert out.affected_area is True
    assert out.min_pct == pytest.approx(10.0)
    assert out.max_pct == pytest.approx(30.0)


@pytest.mark.asyncio
@respx.mock
async def test_potential_top_band_has_open_max_pct() -> None:
    """Class 6 is the top band — ``max_pct`` is None (>=30% homes affected)."""
    respx.get(_QUERY_URL).mock(
        return_value=httpx.Response(200, json=_arcgis_response(6)),
    )
    async with BGSRadonClient() as client:
        out = await client.potential_at(50.4, -4.7)  # Bodmin-ish
    assert out.class_max == 6
    assert out.band == "high"
    assert out.affected_area is True
    assert out.min_pct == pytest.approx(30.0)
    assert out.max_pct is None


@pytest.mark.asyncio
@respx.mock
async def test_potential_off_grid_returns_unknown() -> None:
    """Empty feature list (offshore / no tile) collapses to unknown band."""
    respx.get(_QUERY_URL).mock(
        return_value=httpx.Response(200, json={"features": []})
    )
    async with BGSRadonClient() as client:
        out = await client.potential_at(60.0, -2.0)
    assert out.class_max is None
    assert out.band is None
    assert out.affected_area is False
