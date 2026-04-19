"""Tests for ``NomisClient``."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from uk_property_apis._core.exceptions import NotFoundError, ServerError
from uk_property_apis.ons_nomis import NomisClient

# ---------------------------------------------------------------------------
# Shared fixture data helpers
# ---------------------------------------------------------------------------

_BASE = r"https://www\.nomisweb\.co\.uk/api/v01"


def _claimant_obs_response() -> dict:
    return {
        "obs": [
            {
                "geography": {"geogcode": "E09000001", "description": "City of London"},
                "measures": {"id": "20207"},
                "obs_value": {"value": 150},
            },
            {
                "geography": {"geogcode": "E09000001", "description": "City of London"},
                "measures": {"id": "20209"},
                "obs_value": {"value": 2.3},
            },
        ]
    }


def _employment_obs_response() -> dict:
    return {
        "obs": [
            {
                "geography": {"geogcode": "E09000002", "description": "Barking and Dagenham"},
                "variable": {"id": "45"},
                "obs_value": {"value": 72.1},
            },
            {
                "geography": {"geogcode": "E09000002", "description": "Barking and Dagenham"},
                "variable": {"id": "18"},
                "obs_value": {"value": 5.4},
            },
            {
                "geography": {"geogcode": "E09000002", "description": "Barking and Dagenham"},
                "variable": {"id": "284"},
                "obs_value": {"value": 22.5},
            },
        ]
    }


def _wages_obs_response() -> dict:
    return {
        "obs": [
            {
                "geography": {"geogcode": "E06000001", "description": "Hartlepool"},
                "pay": {"id": "7"},
                "obs_value": {"value": 480.5},
            },
            {
                "geography": {"geogcode": "E06000001", "description": "Hartlepool"},
                "pay": {"id": "8"},
                "obs_value": {"value": 530.2},
            },
        ]
    }


def _population_obs_response() -> dict:
    return {
        "obs": [
            {
                "geography": {"geogcode": "E02000001", "description": "City of London 001"},
                "age": {"description": "0-4"},
                "obs_value": {"value": 200},
            },
            {
                "geography": {"geogcode": "E02000001", "description": "City of London 001"},
                "age": {"description": "5-9"},
                "obs_value": {"value": 180},
            },
        ]
    }


def _job_density_obs_response() -> dict:
    return {
        "obs": [
            {
                "geography": {"geogcode": "E08000003", "description": "Manchester"},
                "item": {"id": "3"},
                "obs_value": {"value": 1.15},
            },
        ]
    }


# ---------------------------------------------------------------------------
# claimant_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_claimant_count_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_162_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json=_claimant_obs_response()),
    )
    async with NomisClient() as client:
        result = await client.claimant_count("E09000001")
    assert result.geography_code == "E09000001"
    assert result.geography_name == "City of London"
    assert result.claimants == 150
    assert result.rate == pytest.approx(2.3)


@pytest.mark.asyncio
@respx.mock
async def test_claimant_count_empty_obs() -> None:
    """Returns a model with all-None fields when the API returns no observations."""
    respx.get(re.compile(rf"{_BASE}/dataset/NM_162_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json={"obs": []}),
    )
    async with NomisClient() as client:
        result = await client.claimant_count("E09000001")
    assert result.claimants is None
    assert result.rate is None


# ---------------------------------------------------------------------------
# employment_rate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_employment_rate_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_17_5\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json=_employment_obs_response()),
    )
    async with NomisClient() as client:
        result = await client.employment_rate("E09000002")
    assert result.geography_code == "E09000002"
    assert result.employment_rate == pytest.approx(72.1)
    assert result.unemployment_rate == pytest.approx(5.4)
    assert result.economic_inactivity_rate == pytest.approx(22.5)


@pytest.mark.asyncio
@respx.mock
async def test_employment_rate_partial_obs() -> None:
    """Missing variable IDs leave corresponding fields as None."""
    respx.get(re.compile(rf"{_BASE}/dataset/NM_17_5\.data\.json\?.*")).mock(
        return_value=httpx.Response(
            200,
            json={
                "obs": [
                    {
                        "geography": {"geogcode": "E09000002", "description": "Barking"},
                        "variable": {"id": "45"},
                        "obs_value": {"value": 70.0},
                    }
                ]
            },
        ),
    )
    async with NomisClient() as client:
        result = await client.employment_rate("E09000002")
    assert result.employment_rate == pytest.approx(70.0)
    assert result.unemployment_rate is None
    assert result.economic_inactivity_rate is None


# ---------------------------------------------------------------------------
# median_wages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_median_wages_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_99_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json=_wages_obs_response()),
    )
    async with NomisClient() as client:
        result = await client.median_wages("E06000001")
    assert result.geography_code == "E06000001"
    assert result.median_weekly_pay == pytest.approx(480.5)
    assert result.mean_weekly_pay == pytest.approx(530.2)


# ---------------------------------------------------------------------------
# population_by_age
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_population_by_age_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_2002_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json=_population_obs_response()),
    )
    async with NomisClient() as client:
        result = await client.population_by_age("E02000001")
    assert result.geography_code == "E02000001"
    assert result.age_bands == {"0-4": 200, "5-9": 180}
    assert result.total_population == 380


@pytest.mark.asyncio
@respx.mock
async def test_population_by_age_empty_returns_none_total() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_2002_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json={"obs": []}),
    )
    async with NomisClient() as client:
        result = await client.population_by_age("E02000001")
    assert result.total_population is None
    assert result.age_bands == {}


# ---------------------------------------------------------------------------
# job_density
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_job_density_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_57_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json=_job_density_obs_response()),
    )
    async with NomisClient() as client:
        result = await client.job_density("E08000003")
    assert result.geography_code == "E08000003"
    assert result.geography_name == "Manchester"
    assert result.job_density == pytest.approx(1.15)


# ---------------------------------------------------------------------------
# list_datasets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_datasets_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/def\.sdmx\.json$")).mock(
        return_value=httpx.Response(
            200,
            json={
                "structure": {
                    "keyfamilies": {
                        "keyfamily": [
                            {
                                "id": "NM_162_1",
                                "name": {"value": "Claimant Count"},
                                "description": {"value": "Monthly claimant count data"},
                            },
                            {
                                "id": "NM_17_5",
                                "name": {"value": "Annual Population Survey"},
                                "description": None,
                            },
                        ]
                    }
                }
            },
        ),
    )
    async with NomisClient() as client:
        datasets = await client.list_datasets()
    assert len(datasets) == 2
    assert datasets[0].id == "NM_162_1"
    assert datasets[0].name == "Claimant Count"
    assert datasets[0].description == "Monthly claimant count data"
    assert datasets[1].id == "NM_17_5"


@pytest.mark.asyncio
@respx.mock
async def test_list_datasets_empty_catalogue() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/def\.sdmx\.json$")).mock(
        return_value=httpx.Response(200, json={"structure": {"keyfamilies": {"keyfamily": []}}}),
    )
    async with NomisClient() as client:
        datasets = await client.list_datasets()
    assert datasets == []


# ---------------------------------------------------------------------------
# observations (generic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_observations_happy() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_162_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(200, json={"obs": [{"obs_value": {"value": 42}}]}),
    )
    async with NomisClient() as client:
        result = await client.observations("NM_162_1", "E09000001", measures="20207")
    assert result.dataset_id == "NM_162_1"
    assert result.geography == "E09000001"
    assert result.total_observations == 1
    assert result.observations[0]["obs_value"]["value"] == 42


@pytest.mark.asyncio
@respx.mock
async def test_observations_not_found() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_MISSING\.data\.json\?.*")).mock(
        return_value=httpx.Response(404, json={}),
    )
    async with NomisClient() as client:
        with pytest.raises(NotFoundError):
            await client.observations("NM_MISSING", "E09000001")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_server_error_raises_server_error() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/NM_162_1\.data\.json\?.*")).mock(
        return_value=httpx.Response(500, json={"message": "Internal error"}),
    )
    async with NomisClient() as client:
        with pytest.raises(ServerError):
            await client.claimant_count("E09000001")


@pytest.mark.asyncio
@respx.mock
async def test_list_datasets_server_error() -> None:
    respx.get(re.compile(rf"{_BASE}/dataset/def\.sdmx\.json$")).mock(
        return_value=httpx.Response(503, json={}),
    )
    async with NomisClient() as client:
        with pytest.raises(ServerError):
            await client.list_datasets()


# ---------------------------------------------------------------------------
# Geography parameter forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_geography_parameter_forwarded_correctly() -> None:
    """The geography code must appear verbatim in the request query string."""
    geo_code = "E01000001"
    captured: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"obs": []})

    respx.get(re.compile(rf"{_BASE}/dataset/NM_162_1\.data\.json\?.*")).mock(side_effect=_capture)
    async with NomisClient() as client:
        await client.claimant_count(geo_code)

    assert captured, "No request was made"
    assert geo_code in str(captured[0].url)
