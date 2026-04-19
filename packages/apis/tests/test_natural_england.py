"""Tests for ``NaturalEnglandClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from uk_property_apis._core.exceptions import ServerError
from uk_property_apis.natural_england import NaturalEnglandClient

_BASE_RE = re.compile(r"https://environment\.data\.gov\.uk/spatialdata/.*")

_EMPTY_FC = {"type": "FeatureCollection", "features": []}


def _feature_collection(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _sssi_feature(
    name: str = "Thursley Common SSSI",
    status: str = "Site of Special Scientific Interest (SSSI)",
) -> dict:
    # Post-2025 schema uses lowercase ``name`` / ``status``; the client
    # accepts both shapes so we exercise that here.
    return {"type": "Feature", "properties": {"name": name, "status": status}}


def _aonb_feature(name: str = "Surrey Hills AONB") -> dict:
    return {"type": "Feature", "properties": {"name": name}}


def _national_park_feature(name: str = "South Downs National Park") -> dict:
    return {"type": "Feature", "properties": {"name": name}}


def _ancient_woodland_feature(
    name: str = "Friston Forest",
    category: str = "Ancient Semi-Natural Woodland",
) -> dict:
    return {"type": "Feature", "properties": {"name": name, "category": category}}


# ---------------------------------------------------------------------------
# green_belt_at — deprecated since 2025 Defra migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_green_belt_at_raises_migration_error() -> None:
    async with NaturalEnglandClient() as client:
        with pytest.raises(RuntimeError, match="green-belt"):
            await client.green_belt_at(51.5, -0.1)


# ---------------------------------------------------------------------------
# sssi_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_sssi_at_happy() -> None:
    respx.get(_BASE_RE).mock(
        return_value=httpx.Response(200, json=_feature_collection(_sssi_feature()))
    )
    async with NaturalEnglandClient() as client:
        rows = await client.sssi_at(51.1, -0.7)
    assert len(rows) == 1
    assert rows[0].name == "Thursley Common SSSI"
    assert rows[0].status == "Site of Special Scientific Interest (SSSI)"


@pytest.mark.asyncio
@respx.mock
async def test_sssi_at_empty() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with NaturalEnglandClient() as client:
        rows = await client.sssi_at(53.0, -1.0)
    assert rows == []


@pytest.mark.asyncio
@respx.mock
async def test_sssi_accepts_uppercase_legacy_keys() -> None:
    """Pre-2025 GeoServer used uppercase property names — still parse."""

    legacy = {
        "type": "Feature",
        "properties": {"SSSI_NAME": "Burnham Beeches", "STATUS": "SSSI"},
    }
    respx.get(_BASE_RE).mock(
        return_value=httpx.Response(200, json=_feature_collection(legacy))
    )
    async with NaturalEnglandClient() as client:
        rows = await client.sssi_at(51.5, -0.6)
    assert rows[0].name == "Burnham Beeches"
    assert rows[0].status == "SSSI"


# ---------------------------------------------------------------------------
# aonb_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_aonb_at_happy() -> None:
    respx.get(_BASE_RE).mock(
        return_value=httpx.Response(200, json=_feature_collection(_aonb_feature()))
    )
    async with NaturalEnglandClient() as client:
        rows = await client.aonb_at(51.2, -0.5)
    assert len(rows) == 1
    assert rows[0].name == "Surrey Hills AONB"


@pytest.mark.asyncio
@respx.mock
async def test_aonb_at_empty() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with NaturalEnglandClient() as client:
        rows = await client.aonb_at(53.0, -1.0)
    assert rows == []


# ---------------------------------------------------------------------------
# national_park_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_national_park_at_happy() -> None:
    respx.get(_BASE_RE).mock(
        return_value=httpx.Response(
            200, json=_feature_collection(_national_park_feature())
        )
    )
    async with NaturalEnglandClient() as client:
        rows = await client.national_park_at(50.9, -0.8)
    assert len(rows) == 1
    assert rows[0].name == "South Downs National Park"


@pytest.mark.asyncio
@respx.mock
async def test_national_park_at_empty() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with NaturalEnglandClient() as client:
        rows = await client.national_park_at(51.5, -0.1)
    assert rows == []


# ---------------------------------------------------------------------------
# ancient_woodland_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_ancient_woodland_at_happy() -> None:
    respx.get(_BASE_RE).mock(
        return_value=httpx.Response(
            200, json=_feature_collection(_ancient_woodland_feature())
        )
    )
    async with NaturalEnglandClient() as client:
        rows = await client.ancient_woodland_at(50.8, 0.2)
    assert len(rows) == 1
    assert rows[0].name == "Friston Forest"
    assert rows[0].category == "Ancient Semi-Natural Woodland"


@pytest.mark.asyncio
@respx.mock
async def test_ancient_woodland_at_empty() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with NaturalEnglandClient() as client:
        rows = await client.ancient_woodland_at(53.0, -1.0)
    assert rows == []


# ---------------------------------------------------------------------------
# designations_at — aggregate (parallel fan-out)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_designations_at_mixed() -> None:
    """SSSI hits; AONB, NP, AW all miss. Green belt is always skipped now."""
    call_count = 0

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        url = str(request.url)
        # New URLs include the per-dataset slug. Match on that.
        if "sites-of-special-scientific-interest-england" in url:
            return httpx.Response(200, json=_feature_collection(_sssi_feature()))
        return httpx.Response(200, json=_EMPTY_FC)

    respx.get(_BASE_RE).mock(side_effect=_side_effect)
    async with NaturalEnglandClient() as client:
        d = await client.designations_at(51.5, -0.7)

    # 4 real datasets now — green belt was retired upstream.
    assert call_count == 4
    assert d.is_green_belt is False
    assert d.is_sssi is True
    assert d.is_aonb is False
    assert d.is_national_park is False
    assert d.is_ancient_woodland is False
    assert d.green_belt == []
    assert len(d.sssi) == 1
    assert d.aonb == []
    assert d.national_parks == []
    assert d.ancient_woodland == []


@pytest.mark.asyncio
@respx.mock
async def test_designations_at_all_empty() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_FC))
    async with NaturalEnglandClient() as client:
        d = await client.designations_at(51.5, -0.1)
    assert d.is_green_belt is False
    assert d.is_sssi is False
    assert d.is_aonb is False
    assert d.is_national_park is False
    assert d.is_ancient_woodland is False


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_wfs_server_error_raises_server_error() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(500, json={}))
    async with NaturalEnglandClient() as client:
        with pytest.raises(ServerError):
            await client.sssi_at(51.5, -0.1)


@pytest.mark.asyncio
@respx.mock
async def test_invalid_geojson_returns_empty() -> None:
    """A response with no 'features' key should be treated as empty."""
    respx.get(_BASE_RE).mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )
    async with NaturalEnglandClient() as client:
        rows = await client.sssi_at(51.5, -0.1)
    assert rows == []
