"""Tests for ``ONSClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.ons import ONSClient


@pytest.mark.asyncio
@respx.mock
async def test_dataset_version() -> None:
    respx.get(
        re.compile(
            r"https://api\.beta\.ons\.gov\.uk/v1/datasets/wellbeing-local-authority/editions/time-series/versions/1$",
        ),
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "edition": "time-series",
                "version": 1,
                "dimensions": [{"id": "geography", "label": "Geography"}],
            },
        ),
    )
    async with ONSClient() as client:
        meta = await client.dataset_version("wellbeing-local-authority", edition="time-series", version=1)
    assert meta.edition == "time-series"
    assert meta.dimensions[0].id == "geography"


@pytest.mark.asyncio
@respx.mock
async def test_observations_happy() -> None:
    respx.get(
        re.compile(
            r"https://api\.beta\.ons\.gov\.uk/v1/datasets/wellbeing-local-authority/editions/time-series/versions/1/observations\?.*",
        ),
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [{"observation": "3.1", "metadata": {}}],
                "dimensions": {},
                "total_observations": 1,
            },
        ),
    )
    async with ONSClient() as client:
        obs = await client.observations(
            "wellbeing-local-authority",
            edition="time-series",
            version=1,
            dimension_filters={
                "time": "2019-20",
                "geography": "E09000001",
                "measureofwellbeing": "anxiety",
                "estimate": "average-mean",
            },
        )
    assert obs.observations[0]["observation"] == "3.1"


@pytest.mark.asyncio
@respx.mock
async def test_observations_404() -> None:
    respx.get(re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/missing/.*")).mock(
        return_value=httpx.Response(404, json={}),
    )
    async with ONSClient() as client:
        with pytest.raises(NotFoundError):
            await client.observations("missing", edition="time-series", version=1, dimension_filters={"x": "y"})


@pytest.mark.asyncio
@respx.mock
async def test_population_by_lsoa_uses_ts039_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS039/editions/2021/versions/1/observations\?.*")).mock(
        return_value=httpx.Response(200, json={"observations": [], "dimensions": {}, "total_observations": 0}),
    )
    monkeypatch.delenv("ONS_POPULATION_LSOA_DATASET", raising=False)
    async with ONSClient() as client:
        out = await client.population_by_lsoa("E09000014")
    assert out.total_observations == 0


@pytest.mark.asyncio
@respx.mock
async def test_median_income_by_msoa(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS039/editions/2021/versions/1/observations\?.*")).mock(
        return_value=httpx.Response(200, json={"observations": [], "dimensions": {}, "total_observations": 0}),
    )
    monkeypatch.delenv("ONS_INCOME_MSOA_DATASET", raising=False)
    async with ONSClient() as client:
        out = await client.median_income_by_msoa("E02000001")
    assert out.observations == []


@pytest.mark.asyncio
@respx.mock
async def test_unemployment_by_local_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(
        re.compile(
            r"https://api\.beta\.ons\.gov\.uk/v1/datasets/wellbeing-local-authority/editions/time-series/versions/4/observations\?.*",
        ),
    ).mock(return_value=httpx.Response(200, json={"observations": [], "dimensions": {}, "total_observations": 0}))
    monkeypatch.delenv("ONS_UNEMPLOYMENT_LA_DATASET", raising=False)
    async with ONSClient() as client:
        out = await client.unemployment_by_local_authority("E09000001")
    assert out.total_observations == 0
