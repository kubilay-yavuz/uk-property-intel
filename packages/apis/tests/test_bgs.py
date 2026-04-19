"""Tests for ``BGSClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.bgs import BGSClient

_BASE_RE = re.compile(r"https://map\.bgs\.ac\.uk/arcgis/rest/services/.*")


def _arcgis_response(grade: str = "B", description: str = "Low") -> dict:
    return {"features": [{"attributes": {"GRADE": grade, "DESCRIPTION": description}}]}


_EMPTY_ARCGIS = {"features": []}


# ---------------------------------------------------------------------------
# Individual hazard methods — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_shrink_swell_happy() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("B", "Low")))
    async with BGSClient() as client:
        rating = await client.shrink_swell(51.5, -0.1)
    assert rating.hazard_type == "shrink_swell"
    assert rating.grade == "B"
    assert rating.description == "Low"


@pytest.mark.asyncio
@respx.mock
async def test_ground_dissolution_happy() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("A", "Minimal")))
    async with BGSClient() as client:
        rating = await client.ground_dissolution(51.5, -0.1)
    assert rating.hazard_type == "ground_dissolution"
    assert rating.grade == "A"


@pytest.mark.asyncio
@respx.mock
async def test_compressible_ground_happy() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("C", "Moderate")))
    async with BGSClient() as client:
        rating = await client.compressible_ground(51.5, -0.1)
    assert rating.hazard_type == "compressible_ground"
    assert rating.grade == "C"


@pytest.mark.asyncio
@respx.mock
async def test_landslide_happy() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("D", "Significant")))
    async with BGSClient() as client:
        rating = await client.landslide(53.5, -2.5)
    assert rating.hazard_type == "landslide"
    assert rating.grade == "D"


@pytest.mark.asyncio
@respx.mock
async def test_collapsible_deposits_happy() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("B", "Low")))
    async with BGSClient() as client:
        rating = await client.collapsible_deposits(52.0, -1.5)
    assert rating.hazard_type == "collapsible_deposits"
    assert rating.grade == "B"


@pytest.mark.asyncio
@respx.mock
async def test_running_sand_happy() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("E", "Very significant")))
    async with BGSClient() as client:
        rating = await client.running_sand(53.0, 0.5)
    assert rating.hazard_type == "running_sand"
    assert rating.grade == "E"


# ---------------------------------------------------------------------------
# Missing features (no hazard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_shrink_swell_no_features() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_ARCGIS))
    async with BGSClient() as client:
        rating = await client.shrink_swell(51.5, -0.1)
    assert rating.grade is None
    assert rating.description is None


# ---------------------------------------------------------------------------
# geohazards_at — aggregate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_geohazards_at_aggregate() -> None:
    """All six layers return grade B; subsidence flag should be False."""
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_arcgis_response("B", "Low")))
    async with BGSClient() as client:
        assessment = await client.geohazards_at(51.5, -0.1)
    assert assessment.shrink_swell.grade == "B"
    assert assessment.ground_dissolution.grade == "B"
    assert assessment.compressible_ground.grade == "B"
    assert assessment.landslide.grade == "B"
    assert assessment.collapsible_deposits.grade == "B"
    assert assessment.running_sand.grade == "B"
    assert assessment.has_subsidence_risk is False


# ---------------------------------------------------------------------------
# Subsidence risk flag logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_subsidence_flag_grade_c_is_true() -> None:
    """Grade C on any layer should set has_subsidence_risk = True."""
    call_count = 0

    def _side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        url = str(request.url)
        # Layer 0 (shrink-swell) returns grade C; rest return grade A
        if "/MapServer/0/" in url:
            return httpx.Response(200, json=_arcgis_response("C", "Moderate"))
        return httpx.Response(200, json=_arcgis_response("A", "Minimal"))

    respx.get(_BASE_RE).mock(side_effect=_side_effect)
    async with BGSClient() as client:
        assessment = await client.geohazards_at(51.5, -0.1)

    assert assessment.has_subsidence_risk is True
    assert assessment.shrink_swell.grade == "C"


@pytest.mark.asyncio
@respx.mock
async def test_subsidence_flag_all_none_is_false() -> None:
    """No features at all → all grades None → has_subsidence_risk False."""
    respx.get(_BASE_RE).mock(return_value=httpx.Response(200, json=_EMPTY_ARCGIS))
    async with BGSClient() as client:
        assessment = await client.geohazards_at(51.5, -0.1)
    assert assessment.has_subsidence_risk is False


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_arcgis_404_raises_not_found() -> None:
    respx.get(_BASE_RE).mock(return_value=httpx.Response(404, json={}))
    async with BGSClient() as client:
        with pytest.raises(NotFoundError):
            await client.shrink_swell(51.5, -0.1)
