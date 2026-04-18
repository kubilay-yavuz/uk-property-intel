"""Tests for :mod:`uk_property_geo.overpass`."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from uk_property_geo.overpass import (
    AmenityCategory,
    AmenityHit,
    OverpassClient,
    OverpassError,
    build_query,
    parse_elements,
)


class TestBuildQuery:
    def test_single_category(self) -> None:
        q = build_query(
            lat=51.5,
            lng=-0.12,
            radius_m=500.0,
            categories=[AmenityCategory.SCHOOL],
        )
        assert q.startswith("[out:json][timeout:25];")
        assert 'node["amenity"="school"](around:500,51.5,-0.12);' in q
        assert 'way["amenity"="school"](around:500,51.5,-0.12);' in q
        assert 'relation["amenity"="school"](around:500,51.5,-0.12);' in q
        assert q.rstrip().endswith("out center tags;")

    def test_multi_category_filter_for_gp(self) -> None:
        # GP maps to two tag filters (amenity=doctors + healthcare=doctor).
        q = build_query(
            lat=52.2,
            lng=0.12,
            radius_m=300.0,
            categories=[AmenityCategory.GP],
        )
        assert 'node["amenity"="doctors"](around:300,52.2,0.12);' in q
        assert 'node["healthcare"="doctor"](around:300,52.2,0.12);' in q

    def test_multiple_categories_emitted(self) -> None:
        q = build_query(
            lat=51.5,
            lng=-0.1,
            radius_m=800.0,
            categories=[AmenityCategory.SCHOOL, AmenityCategory.SUPERMARKET],
        )
        assert 'node["amenity"="school"]' in q
        assert 'node["shop"="supermarket"]' in q

    def test_empty_categories_raises(self) -> None:
        with pytest.raises(ValueError):
            build_query(lat=0.0, lng=0.0, radius_m=100, categories=[])

    def test_zero_radius_raises(self) -> None:
        with pytest.raises(ValueError):
            build_query(
                lat=0.0,
                lng=0.0,
                radius_m=0.0,
                categories=[AmenityCategory.SCHOOL],
            )

    def test_custom_server_timeout(self) -> None:
        q = build_query(
            lat=0.0,
            lng=0.0,
            radius_m=100,
            categories=[AmenityCategory.CAFE],
            server_timeout_s=10,
        )
        assert q.startswith("[out:json][timeout:10];")


class TestParseElements:
    def test_node_with_tags(self) -> None:
        elements: list[dict[str, Any]] = [
            {
                "type": "node",
                "id": 101,
                "lat": 51.5074,
                "lon": -0.1278,
                "tags": {"amenity": "school", "name": "Westminster Primary"},
            }
        ]
        hits = parse_elements(elements, origin=(51.5074, -0.1278))
        assert len(hits) == 1
        assert hits[0].id == 101
        assert hits[0].category is AmenityCategory.SCHOOL
        assert hits[0].name == "Westminster Primary"
        assert hits[0].osm_type == "node"
        assert hits[0].distance_m == pytest.approx(0.0, abs=1e-6)

    def test_way_uses_center(self) -> None:
        elements: list[dict[str, Any]] = [
            {
                "type": "way",
                "id": 42,
                "center": {"lat": 51.5, "lon": -0.12},
                "tags": {"leisure": "park", "name": "Green Park"},
            }
        ]
        hits = parse_elements(elements, origin=(51.5, -0.12))
        assert len(hits) == 1
        assert hits[0].osm_type == "way"
        assert hits[0].category is AmenityCategory.PARK
        assert hits[0].lat == 51.5

    def test_element_without_coord_dropped(self) -> None:
        elements: list[dict[str, Any]] = [
            {"type": "node", "id": 1, "tags": {"amenity": "school"}},  # no coord
            {
                "type": "node",
                "id": 2,
                "lat": 51.5,
                "lon": -0.1,
                "tags": {"amenity": "school"},
            },
        ]
        hits = parse_elements(elements, origin=(51.5, -0.1))
        assert [h.id for h in hits] == [2]

    def test_element_without_matching_tag_dropped(self) -> None:
        elements: list[dict[str, Any]] = [
            {
                "type": "node",
                "id": 1,
                "lat": 51.5,
                "lon": -0.1,
                "tags": {"amenity": "bench"},  # not in our enum
            },
        ]
        assert parse_elements(elements, origin=(51.5, -0.1)) == []

    def test_sorted_by_distance(self) -> None:
        elements: list[dict[str, Any]] = [
            {
                "type": "node",
                "id": 1,
                "lat": 51.5100,
                "lon": -0.1278,
                "tags": {"amenity": "school", "name": "Far"},
            },
            {
                "type": "node",
                "id": 2,
                "lat": 51.5080,
                "lon": -0.1278,
                "tags": {"amenity": "school", "name": "Near"},
            },
        ]
        hits = parse_elements(elements, origin=(51.5074, -0.1278))
        assert [h.name for h in hits] == ["Near", "Far"]
        assert hits[0].distance_m is not None
        assert hits[1].distance_m is not None
        assert hits[0].distance_m < hits[1].distance_m

    def test_no_origin_leaves_distance_none(self) -> None:
        elements: list[dict[str, Any]] = [
            {
                "type": "node",
                "id": 1,
                "lat": 51.5,
                "lon": -0.1,
                "tags": {"amenity": "pub"},
            },
        ]
        hits = parse_elements(elements, origin=None)
        assert hits[0].distance_m is None

    def test_category_priority_is_enum_order(self) -> None:
        # An element tagged as both a pub and cafe maps to whichever category
        # appears first in the filter dict - PUB vs CAFE order depends on the
        # enum.  This test documents the behaviour so future refactors notice.
        elements: list[dict[str, Any]] = [
            {
                "type": "node",
                "id": 1,
                "lat": 51.5,
                "lon": -0.1,
                "tags": {"amenity": "pub", "name": "Weird Cafe"},
            },
        ]
        hits = parse_elements(elements)
        assert hits[0].category is AmenityCategory.PUB


@pytest.mark.asyncio
class TestOverpassClient:
    async def test_amenities_near_success(self) -> None:
        with respx.mock(base_url="https://overpass-api.de/api") as mock:
            mock.post("/interpreter").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "elements": [
                            {
                                "type": "node",
                                "id": 1,
                                "lat": 51.5080,
                                "lon": -0.1278,
                                "tags": {
                                    "amenity": "school",
                                    "name": "Westminster Primary",
                                },
                            },
                            {
                                "type": "way",
                                "id": 2,
                                "center": {"lat": 51.5100, "lon": -0.1250},
                                "tags": {"amenity": "school", "name": "Far School"},
                            },
                        ]
                    },
                )
            )
            async with OverpassClient() as client:
                hits = await client.amenities_near(
                    51.5074,
                    -0.1278,
                    radius_m=500,
                    categories=[AmenityCategory.SCHOOL],
                )
        assert [h.name for h in hits] == ["Westminster Primary", "Far School"]
        assert all(isinstance(h, AmenityHit) for h in hits)
        assert hits[0].distance_m is not None

    async def test_request_body_is_overpass_ql(self) -> None:
        captured_body: list[str] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured_body.append(request.content.decode())
            return httpx.Response(200, json={"elements": []})

        with respx.mock(base_url="https://overpass-api.de/api") as mock:
            mock.post("/interpreter").mock(side_effect=capture)
            async with OverpassClient() as client:
                await client.amenities_near(
                    51.5074,
                    -0.1278,
                    radius_m=500,
                    categories=[AmenityCategory.RAIL_STATION],
                )

        assert len(captured_body) == 1
        body = captured_body[0]
        assert body.startswith("[out:json]")
        assert 'node["railway"="station"](around:500,51.5074,-0.1278);' in body

    async def test_non_json_raises_overpass_error(self) -> None:
        with respx.mock(base_url="https://overpass-api.de/api") as mock:
            mock.post("/interpreter").mock(
                return_value=httpx.Response(200, text="not json at all")
            )
            async with OverpassClient() as client:
                with pytest.raises(OverpassError):
                    await client.amenities_near(
                        51.5,
                        -0.1,
                        radius_m=500,
                        categories=[AmenityCategory.CAFE],
                    )

    async def test_http_400_raises_overpass_error(self) -> None:
        with respx.mock(base_url="https://overpass-api.de/api") as mock:
            mock.post("/interpreter").mock(
                return_value=httpx.Response(400, text="bad query syntax")
            )
            async with OverpassClient() as client:
                with pytest.raises(OverpassError):
                    await client.amenities_near(
                        51.5,
                        -0.1,
                        radius_m=500,
                        categories=[AmenityCategory.CAFE],
                    )

    async def test_retries_on_503_then_succeeds(self) -> None:
        with respx.mock(base_url="https://overpass-api.de/api") as mock:
            mock.post("/interpreter").mock(
                side_effect=[
                    httpx.Response(503, text="busy"),
                    httpx.Response(200, json={"elements": []}),
                ]
            )
            async with OverpassClient() as client:
                hits = await client.amenities_near(
                    51.5,
                    -0.1,
                    radius_m=500,
                    categories=[AmenityCategory.CAFE],
                )
        assert hits == []

    async def test_missing_elements_raises(self) -> None:
        with respx.mock(base_url="https://overpass-api.de/api") as mock:
            mock.post("/interpreter").mock(
                return_value=httpx.Response(200, json={"version": 0.6})
            )
            async with OverpassClient() as client:
                with pytest.raises(OverpassError):
                    await client.amenities_near(
                        51.5,
                        -0.1,
                        radius_m=500,
                        categories=[AmenityCategory.CAFE],
                    )
