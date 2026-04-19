"""Tests for OverlayLayer and OverlayEngine (requires shapely>=2.0)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

shapely = pytest.importorskip("shapely")

from uk_property_geo.overlays import OverlayEngine, OverlayFeature, OverlayLayer, OverlayResult  # noqa: E402

# Small square polygon covering part of central London
_LONDON_SQUARE_FEATURE = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [-0.13, 51.50],
            [-0.12, 51.50],
            [-0.12, 51.51],
            [-0.13, 51.51],
            [-0.13, 51.50],
        ]],
    },
    "properties": {"name": "London Test Zone", "zone": "central"},
}

_EMPTY_FC = {"type": "FeatureCollection", "features": []}
_ONE_FEATURE_FC = {"type": "FeatureCollection", "features": [_LONDON_SQUARE_FEATURE]}

# Point inside the square
_LAT_IN = 51.505
_LNG_IN = -0.125

# Point outside the square
_LAT_OUT = 51.60
_LNG_OUT = -0.10


@pytest.fixture
def one_feature_geojson(tmp_path: Path) -> Path:
    p = tmp_path / "layer.geojson"
    p.write_text(json.dumps(_ONE_FEATURE_FC))
    return p


@pytest.fixture
def empty_geojson(tmp_path: Path) -> Path:
    p = tmp_path / "empty.geojson"
    p.write_text(json.dumps(_EMPTY_FC))
    return p


def test_overlay_layer_from_geojson_loads(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="test")
    assert len(layer._geometries) == 1


def test_overlay_layer_contains_inside_point(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="test")
    assert layer.contains(_LAT_IN, _LNG_IN) is True


def test_overlay_layer_not_contains_outside_point(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="test")
    assert layer.contains(_LAT_OUT, _LNG_OUT) is False


def test_overlay_layer_intersecting_features_inside(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    features = layer.intersecting_features(_LAT_IN, _LNG_IN)
    assert len(features) == 1
    assert isinstance(features[0], OverlayFeature)
    assert features[0].layer == "zones"
    assert features[0].name == "London Test Zone"


def test_overlay_layer_intersecting_features_outside(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    features = layer.intersecting_features(_LAT_OUT, _LNG_OUT)
    assert features == []


def test_overlay_layer_empty_geojson(empty_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(empty_geojson, name="empty")
    assert layer.contains(_LAT_IN, _LNG_IN) is False
    assert layer.intersecting_features(_LAT_IN, _LNG_IN) == []


def test_overlay_engine_query_hit(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    engine = OverlayEngine([layer])
    result = engine.query(_LAT_IN, _LNG_IN)
    assert isinstance(result, OverlayResult)
    assert len(result.features) == 1


def test_overlay_engine_query_miss(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    engine = OverlayEngine([layer])
    result = engine.query(_LAT_OUT, _LNG_OUT)
    assert result.features == []
    assert result.layer_hits == {}


def test_overlay_engine_in_layer_true(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    engine = OverlayEngine([layer])
    result = engine.query(_LAT_IN, _LNG_IN)
    assert result.in_layer("zones") is True


def test_overlay_engine_in_layer_false(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    engine = OverlayEngine([layer])
    result = engine.query(_LAT_OUT, _LNG_OUT)
    assert result.in_layer("zones") is False


def test_overlay_feature_name_extracted(one_feature_geojson: Path) -> None:
    layer = OverlayLayer.from_geojson(one_feature_geojson, name="zones")
    features = layer.intersecting_features(_LAT_IN, _LNG_IN)
    assert features[0].name == "London Test Zone"
    assert features[0].properties.get("zone") == "central"


def test_overlay_engine_multiple_layers(tmp_path: Path) -> None:
    second_feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-0.15, 51.49],
                [-0.10, 51.49],
                [-0.10, 51.52],
                [-0.15, 51.52],
                [-0.15, 51.49],
            ]],
        },
        "properties": {"name": "Wide Zone"},
    }
    p1 = tmp_path / "layer1.geojson"
    p2 = tmp_path / "layer2.geojson"
    p1.write_text(json.dumps(_ONE_FEATURE_FC))
    p2.write_text(json.dumps({"type": "FeatureCollection", "features": [second_feature]}))

    layer1 = OverlayLayer.from_geojson(p1, name="narrow")
    layer2 = OverlayLayer.from_geojson(p2, name="wide")
    engine = OverlayEngine([layer1, layer2])

    result = engine.query(_LAT_IN, _LNG_IN)
    # Point at 51.505, -0.125 is inside both polygons
    assert result.in_layer("narrow") is True
    assert result.in_layer("wide") is True
    assert len(result.features) == 2
