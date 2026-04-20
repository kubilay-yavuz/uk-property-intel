"""Tests for :class:`DefraAirQualityClient`.

Covers:

* ``canonical_pollutant`` vocabulary — including the tricky
  ``Trimethylbenzene`` / ``Benzene`` disambiguation.
* ``_split_label`` — site names with internal hyphens and pollutant
  names with internal hyphens (``Non-volatile PM2.5``).
* ``list_stations`` parses the ``/timeseries/`` JSON shape.
* ``stations_near`` applies haversine distance + pollutant filters.
* ``latest_value`` returns the latest non-null pair within the window.
* ``latest_nearby`` merges nearby stations with their latest readings
  and silently skips timeseries whose window is empty.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from uk_property_apis.airquality import (
    AirQualityStation,
    DefraAirQualityClient,
    canonical_pollutant,
)
from uk_property_apis.airquality.models import _split_label

_TIMESERIES_URL = "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/"


def _timeseries_feature(
    *,
    ts_id: int,
    station_id: int,
    label: str,
    lat: float,
    lng: float,
    uom: str = "ug.m-3",
) -> dict:
    """Return a minimal /timeseries/ record shaped like DEFRA's live feed."""

    return {
        "id": ts_id,
        "label": f"{ts_id} - {label}",
        "uom": uom,
        "station": {
            "properties": {"id": station_id, "label": label},
            "geometry": {"coordinates": [lat, lng, "NaN"], "type": "Point"},
            "type": "Feature",
        },
    }


def _timeseries_list() -> list[dict]:
    """Return a compact fake list that covers all the tricky cases."""

    return [
        _timeseries_feature(
            ts_id=1,
            station_id=100,
            label="London Westminster-Nitrogen dioxide (air)",
            lat=51.4946,
            lng=-0.1312,
        ),
        _timeseries_feature(
            ts_id=2,
            station_id=100,
            label="London Westminster-Ozone (air)",
            lat=51.4946,
            lng=-0.1312,
        ),
        _timeseries_feature(
            ts_id=3,
            station_id=200,
            label="Stoke-on-Trent Centre-Carbon monoxide (air)",
            lat=53.0,
            lng=-2.2,
            uom="mg.m-3",
        ),
        _timeseries_feature(
            ts_id=4,
            station_id=300,
            label="Aberdeen-Non-volatile PM10",
            lat=57.14,
            lng=-2.1,
        ),
        _timeseries_feature(
            ts_id=5,
            station_id=400,
            label="Industrial Estate-1,2,3-Trimethylbenzene (air)",
            lat=53.5,
            lng=-1.5,
        ),
    ]


def test_canonical_pollutant_basic() -> None:
    assert canonical_pollutant("Nitrogen dioxide (air)") == "no2"
    assert canonical_pollutant("Nitrogen monoxide (air)") == "nom"
    assert canonical_pollutant("Nitrogen oxides (air)") == "nox"
    assert canonical_pollutant("Ozone (air)") == "o3"
    assert canonical_pollutant("Sulphur dioxide (air)") == "so2"
    assert canonical_pollutant("Sulfur dioxide (air)") == "so2"
    assert canonical_pollutant("Carbon monoxide (air)") == "co"
    assert canonical_pollutant("Particulate matter less than 2.5 micro m") == "pm25"
    assert canonical_pollutant("PM2.5") == "pm25"
    assert canonical_pollutant("Particulate matter less than 10 micro m") == "pm10"
    assert canonical_pollutant("PM10") == "pm10"
    assert canonical_pollutant("Benzene (air)") == "benzene"
    assert canonical_pollutant("") == "other"
    assert canonical_pollutant(None) == "other"


def test_canonical_pollutant_distinguishes_trimethylbenzene() -> None:
    """Trimethylbenzene is a speciated VOC — not the regulated headline benzene."""

    assert canonical_pollutant("1,2,3-Trimethylbenzene (air)") == "other"
    assert canonical_pollutant("Trimethylbenzene") == "other"


def test_canonical_pollutant_distinguishes_ethyl_benzene() -> None:
    """Ethyl benzene (a distinct VOC) must not be tagged as regulated benzene."""

    assert canonical_pollutant("Ethyl benzene (air)") == "other"
    assert canonical_pollutant("Ethylbenzene (air)") == "other"


def test_canonical_pollutant_matches_plain_benzene() -> None:
    """Plain ``Benzene (air)`` / ``Benzene`` is the regulated headline compound."""

    assert canonical_pollutant("Benzene (air)") == "benzene"
    assert canonical_pollutant("benzene") == "benzene"


def test_split_label_simple() -> None:
    site, pollutant = _split_label("London Westminster-Nitrogen dioxide (air)")
    assert site == "London Westminster"
    assert pollutant == "Nitrogen dioxide (air)"


def test_split_label_site_with_internal_hyphen() -> None:
    """Reverse-walk picks the rightmost ``-`` whose tail canonicalises."""

    site, pollutant = _split_label("Stoke-on-Trent Centre-Carbon monoxide (air)")
    assert site == "Stoke-on-Trent Centre"
    assert pollutant == "Carbon monoxide (air)"


def test_split_label_pollutant_with_internal_hyphen() -> None:
    """``Non-volatile PM10`` keeps its ``Non-`` prefix because the tail starts
    with uppercase (whereas ``volatile`` is lowercase and rejected)."""

    site, pollutant = _split_label("Aberdeen-Non-volatile PM10")
    assert site == "Aberdeen"
    assert pollutant == "Non-volatile PM10"


def test_split_label_voc_speciation_falls_back_to_first_hyphen() -> None:
    """Untracked VOCs like 1,2,3-Trimethylbenzene (canonical='other') fall
    back to the leftmost split so the raw pollutant string is preserved."""

    site, pollutant = _split_label(
        "Industrial Estate-1,2,3-Trimethylbenzene (air)"
    )
    assert site == "Industrial Estate"
    assert pollutant == "1,2,3-Trimethylbenzene (air)"


def test_split_label_no_hyphen() -> None:
    site, pollutant = _split_label("Plain Site Name")
    assert site == "Plain Site Name"
    assert pollutant is None


def test_station_from_timeseries_feature_happy() -> None:
    feature = _timeseries_feature(
        ts_id=123,
        station_id=456,
        label="London Westminster-Nitrogen dioxide (air)",
        lat=51.4946,
        lng=-0.1312,
    )
    station = AirQualityStation.from_timeseries_feature(feature)
    assert station is not None
    assert station.timeseries_id == "123"
    assert station.station_id == "456"
    assert station.site_name == "London Westminster"
    assert station.pollutant == "no2"
    assert station.pollutant_raw == "Nitrogen dioxide (air)"
    assert station.unit == "ug.m-3"
    assert station.lat == pytest.approx(51.4946)
    assert station.lng == pytest.approx(-0.1312)


def test_station_skips_missing_id_or_label() -> None:
    """Malformed records return ``None`` (dropped silently) — never crash."""

    assert AirQualityStation.from_timeseries_feature({}) is None
    assert (
        AirQualityStation.from_timeseries_feature(
            {"id": 1, "station": {"properties": {"label": ""}}}
        )
        is None
    )


@pytest.mark.asyncio
@respx.mock
async def test_list_stations_caches_and_filters_malformed() -> None:
    raw = [
        *_timeseries_list(),
        {"id": 999, "station": {"properties": {"label": ""}}},
    ]
    route = respx.get(_TIMESERIES_URL).mock(
        return_value=httpx.Response(200, json=raw)
    )
    async with DefraAirQualityClient() as client:
        first = await client.list_stations()
        second = await client.list_stations()
    assert len(first) == 5
    assert first is second
    assert route.call_count == 1
    assert all(s.lat is not None and s.lng is not None for s in first)


@pytest.mark.asyncio
@respx.mock
async def test_stations_near_haversine_and_filter() -> None:
    respx.get(_TIMESERIES_URL).mock(
        return_value=httpx.Response(200, json=_timeseries_list())
    )
    async with DefraAirQualityClient() as client:
        westminster = await client.stations_near(
            51.5014,
            -0.1419,
            radius_km=5.0,
            pollutants=["no2", "o3"],
        )
    assert [p.station.pollutant for p in westminster] == ["no2", "o3"]
    assert all(p.distance_km <= 5.0 for p in westminster)
    assert westminster == sorted(westminster, key=lambda p: p.distance_km)


@pytest.mark.asyncio
@respx.mock
async def test_stations_near_empty_out_of_radius() -> None:
    respx.get(_TIMESERIES_URL).mock(
        return_value=httpx.Response(200, json=_timeseries_list())
    )
    async with DefraAirQualityClient() as client:
        hits = await client.stations_near(51.5014, -0.1419, radius_km=0.1)
    assert hits == []


@pytest.mark.asyncio
@respx.mock
async def test_latest_value_returns_newest_pair() -> None:
    respx.get(
        "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/1/getData",
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {"timestamp": 1_700_000_000_000, "value": 12.3},
                    {"timestamp": 1_700_003_600_000, "value": 15.1},
                    {"timestamp": 1_700_007_200_000, "value": None},
                ]
            },
        ),
    )
    async with DefraAirQualityClient() as client:
        ts, v = await client.latest_value("1", window_hours=24)
    assert ts == 1_700_003_600_000
    assert v == pytest.approx(15.1)


@pytest.mark.asyncio
@respx.mock
async def test_latest_value_empty_window() -> None:
    respx.get(
        "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/2/getData",
    ).mock(return_value=httpx.Response(200, json={"values": []}))
    async with DefraAirQualityClient() as client:
        ts, v = await client.latest_value("2", window_hours=24)
    assert ts is None
    assert v is None


@pytest.mark.asyncio
@respx.mock
async def test_latest_nearby_merges_stations_and_readings() -> None:
    respx.get(_TIMESERIES_URL).mock(
        return_value=httpx.Response(200, json=_timeseries_list())
    )
    respx.get(
        "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/1/getData",
    ).mock(
        return_value=httpx.Response(
            200,
            json={"values": [{"timestamp": 1_700_003_600_000, "value": 11.2}]},
        )
    )
    respx.get(
        "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/2/getData",
    ).mock(
        return_value=httpx.Response(
            200,
            json={"values": [{"timestamp": 1_700_003_600_000, "value": 45.6}]},
        )
    )
    async with DefraAirQualityClient() as client:
        readings = await client.latest_nearby(
            51.5014,
            -0.1419,
            radius_km=3.0,
            pollutants=["no2", "o3"],
            max_stations=4,
            window_hours=24,
        )
    assert {r.pollutant for r in readings} == {"no2", "o3"}
    assert all(r.unit == "ug.m-3" for r in readings)
    values = {r.pollutant: r.value for r in readings}
    assert values["no2"] == pytest.approx(11.2)
    assert values["o3"] == pytest.approx(45.6)


@pytest.mark.asyncio
@respx.mock
async def test_latest_nearby_drops_empty_timeseries() -> None:
    respx.get(_TIMESERIES_URL).mock(
        return_value=httpx.Response(200, json=_timeseries_list())
    )
    respx.get(
        "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/1/getData",
    ).mock(return_value=httpx.Response(200, json={"values": []}))
    respx.get(
        "https://uk-air.defra.gov.uk/sos-ukair/api/v1/timeseries/2/getData",
    ).mock(
        return_value=httpx.Response(
            200,
            json={"values": [{"timestamp": 1_700_003_600_000, "value": 45.6}]},
        )
    )
    async with DefraAirQualityClient() as client:
        readings = await client.latest_nearby(
            51.5014,
            -0.1419,
            radius_km=3.0,
            pollutants=["no2", "o3"],
            max_stations=4,
            window_hours=24,
        )
    assert [r.pollutant for r in readings] == ["o3"]
