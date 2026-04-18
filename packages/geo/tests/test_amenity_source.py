"""Tests for :class:`uk_property_geo.amenity_source.OverpassAmenitySource`."""

from __future__ import annotations

import httpx
import pytest
import respx
from uk_property_geo import (
    AmenityCategory,
    OverpassAmenitySource,
    OverpassClient,
)


def _respx_mock() -> respx.MockRouter:
    return respx.mock(base_url="https://overpass-api.de/api", assert_all_called=False)


def _elements_for(
    *categories: AmenityCategory,
    start_id: int = 100,
) -> list[dict[str, object]]:
    """Return a minimal Overpass ``elements`` list with one hit per category.

    Each hit is a bare-bones node with matching tags so ``parse_elements``
    classifies it. Coordinates are placed around a shared origin so the
    distance field resolves, but the exact values don't matter for the
    aggregation tests.
    """

    out: list[dict[str, object]] = []
    for i, cat in enumerate(categories):
        tags = _primary_tag_for(cat)
        out.append(
            {
                "type": "node",
                "id": start_id + i,
                "lat": 51.5074 + 0.001 * i,
                "lon": -0.1278 + 0.001 * i,
                "tags": dict(tags) | {"name": f"{cat.value}-{i}"},
            }
        )
    return out


def _primary_tag_for(cat: AmenityCategory) -> tuple[tuple[str, str], ...]:
    mapping: dict[AmenityCategory, tuple[tuple[str, str], ...]] = {
        AmenityCategory.SCHOOL: (("amenity", "school"),),
        AmenityCategory.SUPERMARKET: (("shop", "supermarket"),),
        AmenityCategory.GP: (("amenity", "doctors"),),
        AmenityCategory.HOSPITAL: (("amenity", "hospital"),),
        AmenityCategory.PARK: (("leisure", "park"),),
        AmenityCategory.RAIL_STATION: (("railway", "station"),),
        AmenityCategory.TUBE_STATION: (("station", "subway"),),
        AmenityCategory.BUS_STOP: (("highway", "bus_stop"),),
        AmenityCategory.RESTAURANT: (("amenity", "restaurant"),),
        AmenityCategory.PUB: (("amenity", "pub"),),
        AmenityCategory.CAFE: (("amenity", "cafe"),),
        AmenityCategory.GYM: (("leisure", "fitness_centre"),),
        AmenityCategory.PHARMACY: (("amenity", "pharmacy"),),
    }
    return mapping[cat]


class TestConstruction:
    """Constructor input validation and default configuration."""

    async def test_defaults_to_every_category(self) -> None:
        async with OverpassAmenitySource() as source:
            assert source.categories == tuple(AmenityCategory)

    async def test_accepts_custom_category_list(self) -> None:
        async with OverpassAmenitySource(
            categories=[AmenityCategory.SCHOOL, AmenityCategory.GP],
        ) as source:
            assert source.categories == (
                AmenityCategory.SCHOOL,
                AmenityCategory.GP,
            )

    async def test_rejects_empty_category_list(self) -> None:
        with pytest.raises(ValueError, match="at least one category"):
            OverpassAmenitySource(categories=[])

    async def test_shared_client_is_not_owned(self) -> None:
        shared_client = OverpassClient()
        try:
            source = OverpassAmenitySource(client=shared_client)
            await source.aclose()
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(200, json={"elements": []})
                )
                hits = await source(51.5, -0.1)
            assert all(count == 0 for count in hits.values())
        finally:
            await shared_client.aclose()


class TestAggregation:
    """End-to-end ``__call__`` behaviour against a mocked Overpass endpoint."""

    async def test_returns_zero_counts_when_no_hits(self) -> None:
        async with OverpassAmenitySource(
            categories=[AmenityCategory.SCHOOL, AmenityCategory.GP],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(200, json={"elements": []})
                )
                result = await source(51.5074, -0.1278, radius_m=500)
        assert result == {"school": 0, "gp": 0}

    async def test_aggregates_single_category(self) -> None:
        async with OverpassAmenitySource(
            categories=[AmenityCategory.SCHOOL],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "elements": _elements_for(
                                AmenityCategory.SCHOOL,
                                AmenityCategory.SCHOOL,
                                AmenityCategory.SCHOOL,
                            ),
                        },
                    )
                )
                result = await source(51.5, -0.12, radius_m=500)
        assert result == {"school": 3}

    async def test_aggregates_multiple_categories(self) -> None:
        async with OverpassAmenitySource(
            categories=[
                AmenityCategory.SCHOOL,
                AmenityCategory.GP,
                AmenityCategory.SUPERMARKET,
            ],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "elements": _elements_for(
                                AmenityCategory.SCHOOL,
                                AmenityCategory.SCHOOL,
                                AmenityCategory.GP,
                                AmenityCategory.SUPERMARKET,
                            ),
                        },
                    )
                )
                result = await source(51.5, -0.12, radius_m=500)
        assert result == {"school": 2, "gp": 1, "supermarket": 1}

    async def test_schema_stays_stable_when_category_missing(self) -> None:
        """Configured categories with zero hits must still appear with count 0."""

        async with OverpassAmenitySource(
            categories=[AmenityCategory.SCHOOL, AmenityCategory.GP],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "elements": _elements_for(AmenityCategory.SCHOOL),
                        },
                    )
                )
                result = await source(51.5, -0.12, radius_m=500)
        assert set(result) == {"school", "gp"}
        assert result["school"] == 1
        assert result["gp"] == 0

    async def test_radius_is_forwarded_to_overpass(self) -> None:
        captured_body: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured_body.append(request.content.decode())
            return httpx.Response(200, json={"elements": []})

        async with OverpassAmenitySource(
            categories=[AmenityCategory.CAFE],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(side_effect=capture)
                await source(51.5074, -0.1278, radius_m=750)

        assert len(captured_body) == 1
        assert 'around:750,51.5074,-0.1278' in captured_body[0]

    async def test_rejects_non_positive_radius(self) -> None:
        async with OverpassAmenitySource() as source:
            with pytest.raises(ValueError, match="must be positive"):
                await source(51.5, -0.1, radius_m=0)
            with pytest.raises(ValueError, match="must be positive"):
                await source(51.5, -0.1, radius_m=-10)

    async def test_propagates_transport_errors(self) -> None:
        """Upstream errors should reach the caller — NeighbourhoodFeatureExtractor's
        exception-swallowing wrapper is what decides to swallow them, not the
        source itself."""

        from uk_property_geo.overpass import OverpassError

        async with OverpassAmenitySource(
            categories=[AmenityCategory.SCHOOL],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(400, text="bad query")
                )
                with pytest.raises(OverpassError):
                    await source(51.5, -0.1, radius_m=500)


class TestProtocolSurfaceMatch:
    """Structural compatibility with ``uk_property_avm.AmenityDensitySource``."""

    async def test_satisfies_amenity_density_source_protocol(self) -> None:
        from uk_property_avm import AmenityDensitySource

        source = OverpassAmenitySource()
        try:
            assert isinstance(source, AmenityDensitySource)
        finally:
            await source.aclose()

    async def test_works_as_neighbourhood_extractor_hook(self) -> None:
        """Smoke: wire the source into NeighbourhoodFeatureExtractor and run."""

        from uk_property_avm import NeighbourhoodFeatureExtractor

        async def _geocoder(_pc: str) -> tuple[float, float] | None:
            return (51.5074, -0.1278)

        async with OverpassAmenitySource(
            categories=[AmenityCategory.SCHOOL, AmenityCategory.CAFE],
        ) as source:
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(
                        200,
                        json={
                            "elements": _elements_for(
                                AmenityCategory.SCHOOL,
                                AmenityCategory.CAFE,
                                AmenityCategory.CAFE,
                            ),
                        },
                    )
                )
                extractor = NeighbourhoodFeatureExtractor(
                    geocoder=_geocoder,
                    amenity_source=source,
                )
                features = await extractor.extract("SW2 5TN")

        assert features.amenities_within_1km == {"school": 1, "cafe": 2}


class TestLifecycle:
    """Open/close lifecycle semantics."""

    async def test_context_manager_closes_owned_client(self) -> None:
        async with OverpassAmenitySource() as source:
            inner = source._client
            assert inner._client is None  # not opened yet

        # After exiting the context manager the underlying client should be closed.
        # A closed OverpassClient has ``_client`` back to None (see aclose()).
        assert inner._client is None

    async def test_aclose_is_idempotent_when_client_injected(self) -> None:
        shared_client = OverpassClient()
        try:
            source = OverpassAmenitySource(client=shared_client)
            await source.aclose()
            await source.aclose()  # second close must be safe
            # Shared client must still be usable.
            with _respx_mock() as mock:
                mock.post("/interpreter").mock(
                    return_value=httpx.Response(200, json={"elements": []})
                )
                hits = await shared_client.amenities_near(
                    51.5, -0.1, radius_m=100, categories=[AmenityCategory.CAFE]
                )
            assert hits == []
        finally:
            await shared_client.aclose()
