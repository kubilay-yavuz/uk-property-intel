"""Spatial overlay engine using Shapely for point-in-polygon queries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

try:
    from shapely.geometry import Point as ShapelyPoint
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    _SHAPELY_AVAILABLE = True
except ImportError:
    _SHAPELY_AVAILABLE = False


def _require_shapely() -> None:
    if not _SHAPELY_AVAILABLE:
        msg = "shapely package is required. Install with: pip install 'shapely>=2.0'"
        raise ImportError(msg)


class OverlayFeature(BaseModel):
    model_config = ConfigDict(extra="allow")
    layer: str
    name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class OverlayResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    features: list[OverlayFeature] = Field(default_factory=list)
    layer_hits: dict[str, int] = Field(default_factory=dict)

    def in_layer(self, layer_name: str) -> bool:
        """Return True if any feature from the named layer contains the queried point."""
        return self.layer_hits.get(layer_name, 0) > 0


class OverlayLayer:
    """A single GeoJSON or Shapefile layer with spatial index."""

    def __init__(
        self,
        name: str,
        geometries: list[Any],
        properties: list[dict[str, Any]],
    ) -> None:
        _require_shapely()
        self._name = name
        self._geometries = geometries
        self._properties = properties
        self._tree = STRtree(geometries) if geometries else None

    @classmethod
    def from_geojson(cls, path: Path, *, name: str) -> OverlayLayer:
        """Load a GeoJSON FeatureCollection from disk."""
        _require_shapely()
        with path.open() as f:
            data = json.load(f)
        features = data.get("features", [])
        geometries = [shape(feat["geometry"]) for feat in features if feat.get("geometry")]
        props = [feat.get("properties", {}) or {} for feat in features if feat.get("geometry")]
        return cls(name=name, geometries=geometries, properties=props)

    @classmethod
    def from_shapefile(cls, path: Path, *, name: str) -> OverlayLayer:
        """Load a Shapefile using geopandas."""
        try:
            import geopandas as gpd
        except ImportError as exc:
            msg = "geopandas is required for Shapefile loading. Install: pip install 'geopandas>=1.0'"
            raise ImportError(msg) from exc
        gdf = gpd.read_file(path)
        geometries = list(gdf.geometry)
        props = gdf.drop(columns="geometry").to_dict("records")
        return cls(name=name, geometries=geometries, properties=props)

    def contains(self, lat: float, lng: float) -> bool:
        """Return True if any feature in this layer contains the point."""
        if self._tree is None:
            return False
        pt = ShapelyPoint(lng, lat)
        candidates = self._tree.query(pt)
        return any(self._geometries[i].contains(pt) for i in candidates)

    def intersecting_features(self, lat: float, lng: float) -> list[OverlayFeature]:
        """Return all features in this layer that contain the point."""
        if self._tree is None:
            return []
        pt = ShapelyPoint(lng, lat)
        candidates = self._tree.query(pt)
        results = []
        for i in candidates:
            if self._geometries[i].contains(pt):
                props = self._properties[i]
                results.append(OverlayFeature(
                    layer=self._name,
                    name=props.get("name") or props.get("NAME"),
                    properties=props,
                ))
        return results


class OverlayEngine:
    """Multi-layer spatial overlay engine."""

    def __init__(self, layers: list[OverlayLayer]) -> None:
        self._layers = layers

    def query(self, lat: float, lng: float) -> OverlayResult:
        """Query all layers and return all features that contain the point."""
        features: list[OverlayFeature] = []
        layer_hits: dict[str, int] = {}
        for layer in self._layers:
            hits = layer.intersecting_features(lat, lng)
            features.extend(hits)
            if hits:
                layer_hits[layer._name] = len(hits)
        return OverlayResult(features=features, layer_hits=layer_hits)
