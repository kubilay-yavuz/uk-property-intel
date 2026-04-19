"""Tests for :class:`BGSClient` after the upstream REST withdrawal.

BGS retired the ``BGS_Hazards/GeoHazardsEngland`` ArcGIS REST endpoints
in 2024 — the six subsidence hazards (shrink-swell, ground dissolution,
compressible ground, landslide, collapsible deposits, running sand) are
now only published as WMS raster tiles or behind a paid BGS Data
subscription. Until the replacement client lands, ``BGSClient`` raises
:class:`RuntimeError` with a clear migration pointer so downstream
consumers (the climate-risk actor) surface the state transparently via
``partial_errors`` instead of returning silently empty grades.

These tests pin that behaviour. The previous happy-path coverage is
preserved in git history and will be re-introduced once the replacement
data feed (GeoClimate Basic WMS shim / BGS Data licensed feed) is
wired up.
"""

from __future__ import annotations

import pytest
from uk_property_apis.bgs import BGSClient

_MIGRATION_FRAGMENT = "BGS_Hazards/GeoHazardsEngland REST layers withdrawn"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    [
        "shrink_swell",
        "ground_dissolution",
        "compressible_ground",
        "landslide",
        "collapsible_deposits",
        "running_sand",
    ],
)
async def test_individual_hazard_methods_raise_migration_error(method_name: str) -> None:
    """Each single-layer query raises a documented migration error."""
    async with BGSClient() as client:
        with pytest.raises(RuntimeError) as exc_info:
            await getattr(client, method_name)(51.5, -0.1)
    assert _MIGRATION_FRAGMENT in str(exc_info.value)


@pytest.mark.asyncio
async def test_geohazards_at_raises_migration_error() -> None:
    """The aggregate fan-out raises the same migration error."""
    async with BGSClient() as client:
        with pytest.raises(RuntimeError) as exc_info:
            await client.geohazards_at(51.5, -0.1)
    assert _MIGRATION_FRAGMENT in str(exc_info.value)
