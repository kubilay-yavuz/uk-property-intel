"""Tests for the neighbourhood feature extractor."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from uk_property_avm import (
    NeighbourhoodFeatureExtractor,
    NeighbourhoodFeatures,
    Station,
    haversine_km,
)
from uk_property_avm.features import _DEFAULT_STATIONS

if TYPE_CHECKING:
    from collections.abc import Mapping


# Fixture locations (anchors for the tests).
KINGS_CROSS_LAT = 51.5308
KINGS_CROSS_LNG = -0.1238
EUSTON_LAT = 51.5282
EUSTON_LNG = -0.1337
CAMBRIDGE_LAT = 52.1942
CAMBRIDGE_LNG = 0.1371


class TestHaversineKm:
    """Sanity checks on the haversine helper."""

    def test_zero_distance(self) -> None:
        assert haversine_km(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0)

    def test_kings_cross_to_euston(self) -> None:
        # Real-world: ~0.8 km.
        dist = haversine_km(KINGS_CROSS_LAT, KINGS_CROSS_LNG, EUSTON_LAT, EUSTON_LNG)
        assert 0.5 < dist < 1.2

    def test_kings_cross_to_cambridge(self) -> None:
        # Real-world: ~72 km.
        dist = haversine_km(
            KINGS_CROSS_LAT, KINGS_CROSS_LNG, CAMBRIDGE_LAT, CAMBRIDGE_LNG
        )
        assert 65 < dist < 80

    def test_symmetric(self) -> None:
        a = haversine_km(51.5, -0.1, 53.4, -2.2)
        b = haversine_km(53.4, -2.2, 51.5, -0.1)
        assert a == pytest.approx(b, rel=1e-9)


class _FakeGeocoder:
    """Record each postcode lookup and optionally return a hard-coded pair."""

    def __init__(self, result: tuple[float, float] | None) -> None:
        self.result = result
        self.calls: list[str] = []
        self.raise_on_call = False

    async def __call__(self, postcode: str) -> tuple[float, float] | None:
        self.calls.append(postcode)
        if self.raise_on_call:
            msg = "geocoder failed"
            raise RuntimeError(msg)
        return self.result


class _FakeCrimeSource:
    def __init__(self, result: Mapping[str, int]) -> None:
        self.result = dict(result)
        self.calls: list[tuple[float, float, int]] = []
        self.raise_on_call = False

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        months_back: int = 12,
    ) -> Mapping[str, int]:
        self.calls.append((lat, lng, months_back))
        if self.raise_on_call:
            msg = "crime source failed"
            raise RuntimeError(msg)
        return self.result


class _FakeFloodSource:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[float, float, float]] = []
        self.raise_on_call = False

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float = 10.0,
    ) -> int:
        self.calls.append((lat, lng, distance_km))
        if self.raise_on_call:
            msg = "flood source failed"
            raise RuntimeError(msg)
        return self.result


class _FakeAmenitySource:
    def __init__(self, result: Mapping[str, int]) -> None:
        self.result = dict(result)
        self.calls: list[tuple[float, float, int]] = []
        self.raise_on_call = False

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        radius_m: int = 1000,
    ) -> Mapping[str, int]:
        self.calls.append((lat, lng, radius_m))
        if self.raise_on_call:
            msg = "amenity source failed"
            raise RuntimeError(msg)
        return self.result


class TestExtractorHappyPath:
    """End-to-end assembly with all sources wired in."""

    @pytest.mark.asyncio
    async def test_full_pipeline(self) -> None:
        geocoder = _FakeGeocoder((KINGS_CROSS_LAT, KINGS_CROSS_LNG))
        crime = _FakeCrimeSource(
            {
                "violent-crime": 120,
                "burglary": 30,
                "anti-social-behaviour": 200,
                "possession-of-weapons": 5,
            }
        )
        flood = _FakeFloodSource(2)
        amenity = _FakeAmenitySource({"school": 4, "restaurant": 40})

        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder,
            crime_source=crime,
            flood_source=flood,
            amenity_source=amenity,
        )
        features = await extractor.extract("N1C 4QP")

        assert features.postcode == "N1C 4QP"
        assert features.latitude == pytest.approx(KINGS_CROSS_LAT)
        assert features.longitude == pytest.approx(KINGS_CROSS_LNG)
        assert features.crimes_last_12mo == 355
        assert features.violent_crimes_last_12mo == 125
        assert features.burglary_last_12mo == 30
        assert features.crimes_by_category == crime.result
        assert features.active_flood_warnings == 2
        # King's Cross is in the bundled dataset, so nearest distance ~0 km.
        assert features.nearest_station_name == "London King's Cross"
        assert features.nearest_station_km == pytest.approx(0.0, abs=0.05)
        assert features.stations_within_1km is not None and features.stations_within_1km >= 2
        assert features.amenities_within_1km == {"school": 4, "restaurant": 40}

    @pytest.mark.asyncio
    async def test_postcode_is_forwarded_to_geocoder(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        extractor = NeighbourhoodFeatureExtractor(geocoder=geocoder)
        await extractor.extract("SW1A 1AA")
        assert geocoder.calls == ["SW1A 1AA"]


class TestExtractorWithMissingSources:
    """Optional sources that aren't wired in should yield ``None`` fields."""

    @pytest.mark.asyncio
    async def test_only_geocoder_configured(self) -> None:
        geocoder = _FakeGeocoder((KINGS_CROSS_LAT, KINGS_CROSS_LNG))
        extractor = NeighbourhoodFeatureExtractor(geocoder=geocoder)
        features = await extractor.extract("N1C 4QP")

        assert features.crimes_last_12mo is None
        assert features.violent_crimes_last_12mo is None
        assert features.burglary_last_12mo is None
        assert features.crimes_by_category == {}
        assert features.active_flood_warnings is None
        assert features.amenities_within_1km == {}
        # Station features still populated (local dataset).
        assert features.nearest_station_km is not None
        assert features.nearest_station_name is not None

    @pytest.mark.asyncio
    async def test_custom_station_dataset(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        custom_stations = [
            Station(name="Test Station", lat=51.5, lng=-0.1),
        ]
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, stations=custom_stations
        )
        features = await extractor.extract("TEST")
        assert features.nearest_station_name == "Test Station"
        assert features.nearest_station_km == pytest.approx(0.0, abs=0.01)
        assert features.stations_within_1km == 1

    @pytest.mark.asyncio
    async def test_empty_station_dataset(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, stations=[]
        )
        features = await extractor.extract("TEST")
        assert features.nearest_station_name is None
        assert features.nearest_station_km is None
        assert features.stations_within_1km is None


class TestGeocodingFailures:
    """Unresolvable postcodes shouldn't break the pipeline."""

    @pytest.mark.asyncio
    async def test_unresolvable_postcode(self) -> None:
        geocoder = _FakeGeocoder(None)
        crime = _FakeCrimeSource({})
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, crime_source=crime
        )
        features = await extractor.extract("NOT A POSTCODE")

        assert features.postcode == "NOT A POSTCODE"
        assert features.latitude is None
        assert features.longitude is None
        # No upstream calls past the geocoder.
        assert crime.calls == []
        assert features.crimes_last_12mo is None
        assert features.nearest_station_km is None

    @pytest.mark.asyncio
    async def test_geocoder_exception_swallowed(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        geocoder.raise_on_call = True
        extractor = NeighbourhoodFeatureExtractor(geocoder=geocoder)
        features = await extractor.extract("X")
        assert features.postcode == "X"
        assert features.latitude is None
        assert features.longitude is None


class TestUpstreamFailures:
    """When an optional source raises we log ``None`` rather than propagate."""

    @pytest.mark.asyncio
    async def test_crime_source_exception_yields_none(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        crime = _FakeCrimeSource({})
        crime.raise_on_call = True
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, crime_source=crime
        )
        features = await extractor.extract("X")
        assert features.crimes_last_12mo is None
        assert features.violent_crimes_last_12mo is None
        # Geocoder still worked, so location is populated.
        assert features.latitude == pytest.approx(51.5)

    @pytest.mark.asyncio
    async def test_flood_source_exception_yields_none(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        flood = _FakeFloodSource(0)
        flood.raise_on_call = True
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, flood_source=flood
        )
        features = await extractor.extract("X")
        assert features.active_flood_warnings is None

    @pytest.mark.asyncio
    async def test_amenity_source_exception_yields_empty(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        amenity = _FakeAmenitySource({})
        amenity.raise_on_call = True
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, amenity_source=amenity
        )
        features = await extractor.extract("X")
        assert features.amenities_within_1km == {}


class TestViolentCrimeCategorisation:
    """Violent subset should be conservative about which categories qualify."""

    @pytest.mark.asyncio
    async def test_only_canonical_violent_categories_count(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        crime = _FakeCrimeSource(
            {
                "violent-crime": 10,
                "robbery": 3,
                "possession-of-weapons": 1,
                "anti-social-behaviour": 100,
                "public-order": 50,
                "bicycle-theft": 20,
            }
        )
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, crime_source=crime
        )
        features = await extractor.extract("X")
        assert features.violent_crimes_last_12mo == 14  # 10 + 3 + 1
        assert features.crimes_last_12mo == 184


class TestKeywordParameters:
    """Constructor-level knobs propagate to the source calls."""

    @pytest.mark.asyncio
    async def test_crime_months_back_is_forwarded(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        crime = _FakeCrimeSource({})
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, crime_source=crime, crime_months_back=24
        )
        await extractor.extract("X")
        assert crime.calls == [(51.5, -0.1, 24)]

    @pytest.mark.asyncio
    async def test_flood_distance_km_is_forwarded(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        flood = _FakeFloodSource(0)
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, flood_source=flood, flood_distance_km=25.0
        )
        await extractor.extract("X")
        assert flood.calls == [(51.5, -0.1, 25.0)]

    @pytest.mark.asyncio
    async def test_amenity_radius_m_is_forwarded(self) -> None:
        geocoder = _FakeGeocoder((51.5, -0.1))
        amenity = _FakeAmenitySource({})
        extractor = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, amenity_source=amenity, amenity_radius_m=2500
        )
        await extractor.extract("X")
        assert amenity.calls == [(51.5, -0.1, 2500)]

    @pytest.mark.asyncio
    async def test_station_radius_km_controls_within_count(self) -> None:
        geocoder = _FakeGeocoder((KINGS_CROSS_LAT, KINGS_CROSS_LNG))
        tight = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, station_radius_km=0.1
        )
        wide = NeighbourhoodFeatureExtractor(
            geocoder=geocoder, station_radius_km=3.0
        )
        tight_features = await tight.extract("N1C 4QP")
        wide_features = await wide.extract("N1C 4QP")
        assert tight_features.stations_within_1km is not None
        assert wide_features.stations_within_1km is not None
        assert wide_features.stations_within_1km >= tight_features.stations_within_1km


class TestBundledStationDataset:
    """Sanity checks on the default station dataset.

    The public seed is deliberately minimal (six London termini); the
    hosted A10 ``uk-avm`` actor substitutes the maintained national
    list from ``uk_property_apify_shared.avm_data.stations`` at runtime.
    These tests therefore assert the seed is wired correctly, not that
    it's comprehensive.
    """

    def test_seed_contains_london_termini(self) -> None:
        # The seed is the six London termini — enough to exercise the
        # nearest-station / within-radius lookup for demo purposes.
        assert 4 <= len(_DEFAULT_STATIONS) <= 10
        names = {s.name for s in _DEFAULT_STATIONS}
        assert "London King's Cross" in names
        assert "London Euston" in names

    def test_all_stations_in_uk_bbox(self) -> None:
        # Rough UK bounding box.
        for station in _DEFAULT_STATIONS:
            assert 49.0 <= station.lat <= 61.0, f"{station.name} lat out of UK range"
            assert -8.5 <= station.lng <= 2.0, f"{station.name} lng out of UK range"

    def test_seed_stations_cluster_near_central_london(self) -> None:
        # Every station in the public seed is a London terminus, so
        # they must all fall within 10 km of central London.
        for s in _DEFAULT_STATIONS:
            dist = haversine_km(s.lat, s.lng, 51.51, -0.12)
            assert dist < 10, f"{s.name} is not clustered near central London"


class TestNeighbourhoodFeaturesModel:
    """Pydantic model sanity — defaults and validation."""

    def test_all_optional_fields_default_to_none(self) -> None:
        features = NeighbourhoodFeatures(postcode="N1 1AA")
        assert features.latitude is None
        assert features.longitude is None
        assert features.crimes_last_12mo is None
        assert features.active_flood_warnings is None
        assert features.nearest_station_km is None
        assert features.crimes_by_category == {}
        assert features.amenities_within_1km == {}

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValueError, match="Extra"):
            NeighbourhoodFeatures(
                postcode="N1 1AA",
                not_a_real_field="boom",  # type: ignore[call-arg]
            )
