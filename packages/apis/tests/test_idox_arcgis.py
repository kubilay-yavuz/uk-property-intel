"""Tests for the IDOX ArcGIS Planning transport.

Fixtures under ``tests/fixtures/idox/arcgis/`` are real responses captured
from live council FeatureServers on 2026-04-18:

* ``lambeth_featureserver.json`` — ``FeatureServer?f=json`` metadata.
* ``lambeth_query_acre_lane.json`` — one paginated page (10 rows, ``exceededTransferLimit: true``) returned by
  ``query?where=ADDRESS LIKE '%Acre Lane%'&outFields=*&outSR=4326``.
* ``lambeth_query_recent.json`` — 4 rows returned for
  ``DATEMODIFIED > TIMESTAMP '…'``.
* ``lambeth_query_count.json`` — ``{count: 44226}`` confirming the overall
  application register size.
* ``barnet_featureserver.json`` — identical schema on a second council,
  proving the transport generalises.

Tests exercise:

* Pure feature → ``PlanningApplication`` mapping (no HTTP, no I/O).
* ``ArcGISPlanningClient`` happy-path and pagination flows using ``respx``
  to replay the real network calls.
* Error-envelope handling (Esri ``{"error": {...}}``).
* WHERE-clause construction (``visible_only`` predicate, ``TIMESTAMP``
  literal formatting, single-quote SQL escaping).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from uk_property_apis._core.exceptions import ValidationError
from uk_property_apis.idox import (
    APPLICATION_POINTS,
    KNOWN_COUNCILS,
    ArcGISPlanningClient,
    CouncilConfig,
    PlanningApplication,
    get_council,
)
from uk_property_apis.idox.arcgis_client import (
    _feature_to_application,
    _sql_escape,
    _timestamp_literal,
)
from uk_property_apis.idox.arcgis_models import (
    ArcGISFeature,
    ArcGISFeatureServerInfo,
    ArcGISQueryResult,
)
from uk_property_apis.idox.models import _epoch_ms_to_utc

FIXTURES = Path(__file__).parent / "fixtures" / "idox" / "arcgis"

LAMBETH_SERVICE_BASE = (
    "https://planning.lambeth.gov.uk/server/rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer"
)
LAMBETH_QUERY_URL = f"{LAMBETH_SERVICE_BASE}/{APPLICATION_POINTS}/query"


def _read_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _query_params(request: httpx.Request) -> dict[str, list[str]]:
    """Parse the query string of a captured respx request into a dict."""

    return parse_qs(urlsplit(str(request.url)).query)


# ── Pure helpers ────────────────────────────────────────────────────────────


class TestHelpers:
    def test_sql_escape_doubles_single_quotes(self) -> None:
        assert _sql_escape("O'Brien") == "O''Brien"
        assert _sql_escape("no quotes") == "no quotes"
        assert _sql_escape("") == ""

    def test_timestamp_literal_formats_utc(self) -> None:
        dt = datetime(2026, 4, 18, 7, 30, 15, tzinfo=UTC)
        assert _timestamp_literal(dt) == "TIMESTAMP '2026-04-18 07:30:15'"

    def test_timestamp_literal_naive_treated_as_utc(self) -> None:
        naive = datetime(2026, 4, 18, 7, 30, 15)
        assert _timestamp_literal(naive) == "TIMESTAMP '2026-04-18 07:30:15'"

    def test_epoch_ms_round_trip(self) -> None:
        dt = _epoch_ms_to_utc(1_775_779_200_000)
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026
        assert dt.month == 4

    def test_epoch_ms_none_and_blank(self) -> None:
        assert _epoch_ms_to_utc(None) is None
        assert _epoch_ms_to_utc("") is None
        assert _epoch_ms_to_utc(True) is None  # bool is not a valid timestamp


# ── Feature mapping (no HTTP) ───────────────────────────────────────────────


class TestFeatureMapping:
    def _council(self) -> CouncilConfig:
        return KNOWN_COUNCILS["lambeth"]

    def _features(self) -> list[dict[str, Any]]:
        return _read_json("lambeth_query_acre_lane.json")["features"]

    def test_maps_all_ten_rows(self) -> None:
        council = self._council()
        mapped = [
            _feature_to_application(
                ArcGISFeature.model_validate(raw), council=council
            )
            for raw in self._features()
        ]
        assert all(m is not None for m in mapped)
        assert len(mapped) == 10

    def test_reference_keyval_and_detail_url(self) -> None:
        council = self._council()
        first_raw = self._features()[0]
        app = _feature_to_application(
            ArcGISFeature.model_validate(first_raw), council=council
        )
        assert app is not None
        assert app.reference == "03/00318/ADV"
        assert app.key_val == "0300318ADV"
        assert app.council == "lambeth"
        assert app.detail_url == (
            "https://planning.lambeth.gov.uk/online-applications/applicationDetails.do"
            "?activeTab=summary&keyVal=0300318ADV"
        )
        # Address is preserved exactly as the FeatureServer returned it
        # (multi-line via \r), only trimmed of leading/trailing whitespace.
        assert app.address.startswith("Tesco")
        assert "\r" in app.address
        assert "Acre Lane" in app.address
        assert app.description.startswith("Display of car park")

    def test_coordinates_are_wgs84(self) -> None:
        council = self._council()
        first_raw = self._features()[0]
        app = _feature_to_application(
            ArcGISFeature.model_validate(first_raw), council=council
        )
        assert app is not None and app.coordinates is not None
        assert 51.4 < app.coordinates.lat < 51.5  # central south London
        assert -0.13 < app.coordinates.lon < -0.11

    def test_last_modified_is_utc_aware_datetime(self) -> None:
        council = self._council()
        app = _feature_to_application(
            ArcGISFeature.model_validate(self._features()[0]), council=council
        )
        assert app is not None and app.last_modified is not None
        assert app.last_modified.tzinfo is not None
        assert app.last_modified.year == 2003  # epoch 1053993600000 → 2003-05-27

    def test_missing_refval_returns_none(self) -> None:
        council = self._council()
        bad = ArcGISFeature.model_validate(
            {
                "attributes": {
                    "KEYVAL": "ABC",
                    "ADDRESS": "...",
                    "DESCRIPTION": "...",
                    "DATEMODIFIED": 0,
                },
                "geometry": {"x": 0.0, "y": 0.0},
            }
        )
        assert _feature_to_application(bad, council=council) is None

    def test_missing_keyval_returns_none(self) -> None:
        council = self._council()
        bad = ArcGISFeature.model_validate(
            {
                "attributes": {"REFVAL": "03/00318/ADV"},
                "geometry": None,
            }
        )
        assert _feature_to_application(bad, council=council) is None

    def test_null_geometry_yields_no_coordinates(self) -> None:
        council = self._council()
        feat = ArcGISFeature.model_validate(
            {
                "attributes": {
                    "REFVAL": "X/1",
                    "KEYVAL": "ABC",
                    "ADDRESS": "...",
                    "DESCRIPTION": "...",
                    "DATEMODIFIED": 1_700_000_000_000,
                },
                "geometry": None,
            }
        )
        app = _feature_to_application(feat, council=council)
        assert app is not None
        assert app.coordinates is None

    def test_out_of_bounds_placeholder_still_parses(self) -> None:
        """Real Lambeth data includes cross-border OBS rows with
        placeholder coordinates way out in Cornwall (-7.56, 49.77) — they
        are still valid WGS84 points and must not be dropped here; the
        aggregator layer is the right place to filter them."""

        council = self._council()
        recent = _read_json("lambeth_query_recent.json")
        out_of_london = next(
            raw
            for raw in recent["features"]
            if raw["attributes"]["REFVAL"] == "26/01159/OBS"
        )
        app = _feature_to_application(
            ArcGISFeature.model_validate(out_of_london), council=council
        )
        assert app is not None
        assert app.coordinates is not None
        assert app.coordinates.lat < 50  # well south of London


# ── Pydantic response models ────────────────────────────────────────────────


class TestResponseModels:
    def test_featureserver_info_parses_lambeth_shape(self) -> None:
        info = ArcGISFeatureServerInfo.model_validate(
            _read_json("lambeth_featureserver.json")
        )
        assert info.max_record_count == 2000
        layer_ids = {layer.id for layer in info.layers}
        # Standard IDOX LIVEUniformPA_Planning shape: 5 layers (pts 0/1, polys 2/3/4).
        assert layer_ids == {0, 1, 2, 3, 4}
        point_layer = next(layer for layer in info.layers if layer.id == 0)
        assert point_layer.geometry_type == "esriGeometryPoint"
        assert point_layer.name == "Application points"

    def test_featureserver_info_generalises_to_barnet(self) -> None:
        info = ArcGISFeatureServerInfo.model_validate(
            _read_json("barnet_featureserver.json")
        )
        assert info.max_record_count >= 1000
        assert {layer.id for layer in info.layers} >= {0, 2}

    def test_query_result_parses_pagination_flag(self) -> None:
        result = ArcGISQueryResult.model_validate(
            _read_json("lambeth_query_acre_lane.json")
        )
        assert result.exceeded_transfer_limit is True
        assert len(result.features) == 10
        assert result.object_id_field_name == "OBJECTID"

    def test_query_result_no_pagination_flag(self) -> None:
        result = ArcGISQueryResult.model_validate(
            _read_json("lambeth_query_recent.json")
        )
        assert result.exceeded_transfer_limit is False
        assert len(result.features) == 4


# ── Client smoke tests (no HTTP, just wiring) ───────────────────────────────


class TestClientConstruction:
    def test_rejects_council_without_arcgis_base_url(self) -> None:
        with pytest.raises(ValidationError):
            ArcGISPlanningClient(KNOWN_COUNCILS["westminster"])

    def test_binds_to_lambeth(self) -> None:
        client = ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"])
        assert client.council.slug == "lambeth"
        assert client._service_path == (
            "rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer"
        )


# ── Client flows (respx replay) ─────────────────────────────────────────────


def _mock_fs_info() -> None:
    respx.get(url__regex=rf"{LAMBETH_SERVICE_BASE}/?\?f=json").mock(
        return_value=httpx.Response(
            200,
            json=_read_json("lambeth_featureserver.json"),
        )
    )


def _mock_count(expected_count: int = 44226) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        params = _query_params(request)
        if params.get("returnCountOnly") != ["true"]:
            return httpx.Response(400, json={"error": {"message": "unexpected"}})
        return httpx.Response(200, json={"count": expected_count})

    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*returnCountOnly=true.*").mock(
        side_effect=responder
    )


@pytest.mark.asyncio
@respx.mock
async def test_get_service_info_happy() -> None:
    _mock_fs_info()
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        info = await client.get_service_info()
    assert info.max_record_count == 2000
    assert {layer.id for layer in info.layers} == {0, 1, 2, 3, 4}


@pytest.mark.asyncio
@respx.mock
async def test_count_returns_integer() -> None:
    _mock_count()
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        n = await client.count()
    assert n == 44226


@pytest.mark.asyncio
@respx.mock
async def test_count_raises_on_malformed_response() -> None:
    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(
        return_value=httpx.Response(200, json={"not_count": 1})
    )
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        with pytest.raises(ValidationError, match="count"):
            await client.count()


@pytest.mark.asyncio
@respx.mock
async def test_search_by_address_paginates_to_completion() -> None:
    """First page comes back with ``exceededTransferLimit=true``; the
    client must issue a second request at ``resultOffset=10``, terminate
    on empty, and surface all ten rows."""

    page_one = _read_json("lambeth_query_acre_lane.json")
    empty_page = {
        "objectIdFieldName": "OBJECTID",
        "features": [],
    }
    captured: list[dict[str, list[str]]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        params = _query_params(request)
        captured.append(params)
        offset = int((params.get("resultOffset") or ["0"])[0])
        if offset == 0:
            return httpx.Response(200, json=page_one)
        return httpx.Response(200, json=empty_page)

    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(side_effect=responder)

    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        rows = await client.search_by_address("Acre Lane")

    assert len(rows) == 10
    assert all(isinstance(r, PlanningApplication) for r in rows)
    assert rows[0].reference == "03/00318/ADV"

    # Two pages fetched, with the second request starting at offset 10.
    assert len(captured) == 2
    assert captured[0].get("resultOffset") == ["0"]
    assert captured[1].get("resultOffset") == ["10"]

    # Every query applied both the user's ADDRESS filter *and* the
    # default ``ISPAVISIBLE = 1`` visibility guard, wrapped in parens.
    wheres = [params["where"][0] for params in captured]
    assert all("ADDRESS LIKE '%Acre Lane%'" in w for w in wheres)
    assert all("ISPAVISIBLE = 1" in w for w in wheres)
    # And outSR=4326 is requested on every page so we always get WGS84.
    assert all(params["outSR"] == ["4326"] for params in captured)


@pytest.mark.asyncio
@respx.mock
async def test_search_by_address_respects_max_results() -> None:
    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(
        return_value=httpx.Response(
            200, json=_read_json("lambeth_query_acre_lane.json")
        )
    )
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        rows = await client.search_by_address("Acre Lane", max_results=3)
    assert len(rows) == 3


@pytest.mark.asyncio
async def test_search_by_address_rejects_empty_substring() -> None:
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        with pytest.raises(ValidationError):
            await client.search_by_address("   ")


@pytest.mark.asyncio
@respx.mock
async def test_recent_applications_builds_timestamp_literal() -> None:
    captured: list[dict[str, list[str]]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        captured.append(_query_params(request))
        return httpx.Response(200, json=_read_json("lambeth_query_recent.json"))

    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(side_effect=responder)

    since = datetime(2026, 4, 11, 0, 0, 0, tzinfo=UTC)
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        rows = await client.recent_applications(since=since)

    assert len(rows) == 4
    assert {row.reference for row in rows} >= {"26/01159/OBS", "26/01165/S106D"}
    where = captured[0]["where"][0]
    assert "DATEMODIFIED > TIMESTAMP '2026-04-11 00:00:00'" in where
    assert captured[0]["orderByFields"] == ["DATEMODIFIED DESC"]


@pytest.mark.asyncio
@respx.mock
async def test_applications_in_bbox_passes_envelope() -> None:
    captured: list[dict[str, list[str]]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        captured.append(_query_params(request))
        return httpx.Response(
            200,
            json={
                "objectIdFieldName": "OBJECTID",
                "features": [],
                "exceededTransferLimit": False,
            },
        )

    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(side_effect=responder)

    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        rows = await client.applications_in_bbox(
            min_lon=-0.13, min_lat=51.45, max_lon=-0.11, max_lat=51.47
        )
    assert rows == []

    params = captured[0]
    assert params["geometryType"] == ["esriGeometryEnvelope"]
    assert params["spatialRel"] == ["esriSpatialRelIntersects"]
    assert params["inSR"] == ["4326"]
    envelope = json.loads(params["geometry"][0])
    assert envelope == {
        "xmin": -0.13,
        "ymin": 51.45,
        "xmax": -0.11,
        "ymax": 51.47,
        "spatialReference": {"wkid": 4326},
    }


@pytest.mark.asyncio
@respx.mock
async def test_get_by_reference_escapes_quotes() -> None:
    """The REFVAL filter must escape single quotes to block SQL injection
    (unlikely in a reference, but defensive)."""

    captured: list[dict[str, list[str]]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        captured.append(_query_params(request))
        return httpx.Response(
            200, json={"objectIdFieldName": "OBJECTID", "features": []}
        )

    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(side_effect=responder)

    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        result = await client.get_by_reference("O'Briens/1/FUL")

    assert result is None
    where = captured[0]["where"][0]
    assert "REFVAL = 'O''Briens/1/FUL'" in where
    # ``get_by_reference`` intentionally does *not* apply the visibility
    # filter because exact-ref lookups should include withdrawn/archived.
    assert "ISPAVISIBLE" not in where


@pytest.mark.asyncio
@respx.mock
async def test_get_by_key_val_returns_single_application() -> None:
    acre_lane = _read_json("lambeth_query_acre_lane.json")
    first = acre_lane["features"][0]
    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "objectIdFieldName": "OBJECTID",
                "features": [first],
            },
        )
    )
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        app = await client.get_by_key_val("0300318ADV")
    assert app is not None
    assert app.key_val == "0300318ADV"
    assert app.reference == "03/00318/ADV"


@pytest.mark.asyncio
@respx.mock
async def test_arcgis_error_envelope_raises_validation_error() -> None:
    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": {
                    "code": 400,
                    "message": "Unable to complete operation.",
                    "details": ["Invalid where clause"],
                }
            },
        )
    )
    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        with pytest.raises(ValidationError, match="Invalid where clause"):
            await client.list_applications(where="BOGUSFIELD = 1")


@pytest.mark.asyncio
@respx.mock
async def test_default_visibility_filter_can_be_disabled() -> None:
    captured: list[dict[str, list[str]]] = []

    def responder(request: httpx.Request) -> httpx.Response:
        captured.append(_query_params(request))
        return httpx.Response(
            200, json={"objectIdFieldName": "OBJECTID", "features": []}
        )

    respx.get(url__regex=rf"{LAMBETH_QUERY_URL}\?.*").mock(side_effect=responder)

    async with ArcGISPlanningClient(KNOWN_COUNCILS["lambeth"]) as client:
        await client.list_applications(
            where="REFVAL LIKE '%FUL'",
            visible_only=False,
        )
    where = captured[0]["where"][0]
    assert "ISPAVISIBLE" not in where


# ── Council registry ────────────────────────────────────────────────────────


class TestCouncilRegistry:
    def test_get_council_normalises_case(self) -> None:
        assert get_council("LAMBETH").slug == "lambeth"
        assert get_council("  lambeth ").slug == "lambeth"

    def test_get_council_unknown_raises_keyerror(self) -> None:
        with pytest.raises(KeyError):
            get_council("atlantis")

    def test_arcgis_enabled_councils_is_subset(self) -> None:
        from uk_property_apis.idox.councils import arcgis_enabled_councils

        enabled = {c.slug for c in arcgis_enabled_councils()}
        assert enabled == {"lambeth"}

    def test_detail_url_builder_supports_alternate_tab(self) -> None:
        c = get_council("lambeth")
        assert c.detail_url("ABC", active_tab="documents").endswith(
            "activeTab=documents&keyVal=ABC"
        )
