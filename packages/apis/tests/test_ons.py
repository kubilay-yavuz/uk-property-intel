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


@pytest.mark.asyncio
@respx.mock
async def test_census_table_happy() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS001/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [199943, 4293],
                "dimensions": [
                    {"dimension_name": "ltla", "options_count": 1},
                    {"dimension_name": "residence_type", "options_count": 2},
                ],
                "total_observations": 2,
            },
        ),
    )
    async with ONSClient() as client:
        out = await client.census_table("TS001", "E09000001")
    assert out.total_observations == 2
    assert out.observations == [199943, 4293]


@pytest.mark.asyncio
@respx.mock
async def test_population_happy() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS001/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [], "dimensions": [], "total_observations": 0},
        ),
    )
    async with ONSClient() as client:
        out = await client.population("E01000001")
    assert out.observations == []


@pytest.mark.asyncio
@respx.mock
async def test_household_composition_happy() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS003/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [120], "dimensions": [], "total_observations": 1},
        ),
    )
    async with ONSClient() as client:
        out = await client.household_composition("E02000001")
    assert out.observations == [120]


@pytest.mark.asyncio
@respx.mock
async def test_housing_tenure_happy() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS044/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [], "dimensions": [], "total_observations": 0},
        ),
    )
    async with ONSClient() as client:
        out = await client.housing_tenure("E09000014")
    assert out.total_observations == 0


@pytest.mark.asyncio
@respx.mock
async def test_ethnic_group_happy() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS021/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [], "dimensions": [], "total_observations": 0},
        ),
    )
    async with ONSClient() as client:
        out = await client.ethnic_group("E09000001")
    assert out.total_observations == 0


@pytest.mark.asyncio
@respx.mock
async def test_qualifications_happy() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS067/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [], "dimensions": [], "total_observations": 0},
        ),
    )
    async with ONSClient() as client:
        out = await client.qualifications("E09000001")
    assert out.total_observations == 0


@pytest.mark.asyncio
@respx.mock
async def test_census_table_custom_edition_version() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS001/editions/2022/versions/2/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [], "dimensions": [], "total_observations": 0},
        ),
    )
    async with ONSClient() as client:
        out = await client.census_table("TS001", "E09000001", edition="2022", version=2)
    assert out.observations == []


@pytest.mark.asyncio
@respx.mock
async def test_census_table_404() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS999/.*"),
    ).mock(return_value=httpx.Response(404, json={}))
    async with ONSClient() as client:
        with pytest.raises(NotFoundError):
            await client.census_table("TS999", "E09000001")


@pytest.mark.asyncio
@respx.mock
async def test_population_multiple_observations() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS001/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "observations": [1000, 2000],
                "dimensions": [{"dimension_name": "ltla", "options_count": 1}],
                "total_observations": 2,
            },
        ),
    )
    async with ONSClient() as client:
        out = await client.population("E01000001")
    assert out.total_observations == 2
    assert len(out.observations) == 2


@pytest.mark.asyncio
@respx.mock
async def test_census_table_area_type_override() -> None:
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS044/editions/2021/versions/1/json\?.*"),
    ).mock(
        return_value=httpx.Response(
            200,
            json={"observations": [42], "dimensions": [], "total_observations": 1},
        ),
    )
    async with ONSClient() as client:
        out = await client.census_table("TS044", "E92000001", area_type="ctry")
    assert out.total_observations == 1


@pytest.mark.asyncio
@respx.mock
async def test_census_table_server_error() -> None:
    from uk_property_apis._core.exceptions import ServerError
    respx.get(
        re.compile(r"https://api\.beta\.ons\.gov\.uk/v1/datasets/TS001/.*"),
    ).mock(return_value=httpx.Response(500, json={}))
    async with ONSClient() as client:
        with pytest.raises(ServerError):
            await client.census_table("TS001", "E09000001")
