"""Tests for :class:`CoastalErosionClient` after the NCERM 2024 migration.

The Environment Agency retired the original NCERM WFS endpoint in early
2025 and moved the dataset to
``/spatialdata/ncern-national-2024/wfs`` with a new layer surface
(e.g. ``NCERM_SMP_2055_0CC`` / ``NCERM_SMP_2055_70CC`` etc.) and
switched the geometry column from WGS-84 to British National Grid
(EPSG:27700). The client is waiting on a full rewrite that
(a) discovers the new layers, (b) projects lat/lng into BNG before
querying, and (c) maps the new attribute schema into
``ErosionZone`` / ``ShorelinePrediction``.

Until that rewrite lands, both public methods raise
:class:`RuntimeError` with a clear migration pointer. The climate-risk
actor catches this and surfaces it via its per-source ``error`` /
``partial_errors`` channel, so downstream consumers see the state
transparently rather than getting silently-empty coastal blocks.

These tests pin that behaviour.
"""

from __future__ import annotations

import pytest
from uk_property_apis.coastal import CoastalErosionClient

_MIGRATION_FRAGMENT = "NCERM upstream migrated"


@pytest.mark.asyncio
async def test_erosion_risk_near_raises_migration_error() -> None:
    """``erosion_risk_near`` raises a documented migration error."""
    async with CoastalErosionClient() as client:
        with pytest.raises(RuntimeError) as exc_info:
            await client.erosion_risk_near(50.7, 1.6)
    assert _MIGRATION_FRAGMENT in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("epoch", [20, 50, 100])
async def test_shoreline_prediction_raises_migration_error(epoch: int) -> None:
    """``shoreline_prediction`` raises the same migration error at any epoch."""
    async with CoastalErosionClient() as client:
        with pytest.raises(RuntimeError) as exc_info:
            await client.shoreline_prediction(50.7, 1.6, epoch=epoch)
    assert _MIGRATION_FRAGMENT in str(exc_info.value)
