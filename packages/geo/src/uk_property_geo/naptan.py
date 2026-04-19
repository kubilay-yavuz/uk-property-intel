"""NaPTAN transport stop lookup with haversine spatial queries."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from uk_property_geo.distance import Point, haversine_m

StopType = Literal[
    "rail_station",
    "bus_stop",
    "tram_stop",
    "metro_stop",
    "ferry_terminal",
    "airport",
    "coach_station",
]

_NAPTAN_STOP_TYPE_MAP: dict[str, str] = {
    "RSE": "rail_station",
    "RLY": "rail_station",
    "RPL": "rail_station",
    "BCE": "bus_stop",
    "BCT": "bus_stop",
    "BCS": "bus_stop",
    "TMU": "tram_stop",
    "MET": "tram_stop",
    "MQC": "metro_stop",
    "FTD": "ferry_terminal",
    "FBT": "ferry_terminal",
    "AIR": "airport",
    "GAT": "coach_station",
}

# 20-stop public demo seed
_DEMO_STOPS: list[dict[str, Any]] = [
    {"atco_code": "9100VICTRIA", "name": "London Victoria Rail Station",
     "lat": 51.4952, "lng": -0.1441, "stop_type": "rail_station",
     "indicator": None, "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "9100PADTON", "name": "London Paddington Rail Station",
     "lat": 51.5154, "lng": -0.1755, "stop_type": "rail_station",
     "indicator": None, "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "9100CLPHMJN", "name": "Clapham Junction Rail Station",
     "lat": 51.4642, "lng": -0.1707, "stop_type": "rail_station",
     "indicator": None, "locality": "Wandsworth", "parent_locality": "London"},
    {"atco_code": "9100STFD", "name": "Stratford Rail Station",
     "lat": 51.5413, "lng": -0.0042, "stop_type": "rail_station",
     "indicator": None, "locality": "Newham", "parent_locality": "London"},
    {"atco_code": "9100LIVST", "name": "London Liverpool Street Rail Station",
     "lat": 51.5179, "lng": -0.0823, "stop_type": "rail_station",
     "indicator": None, "locality": "City of London", "parent_locality": "London"},
    {"atco_code": "490003452W", "name": "Oxford Street / Bond Street",
     "lat": 51.5133, "lng": -0.1520, "stop_type": "bus_stop",
     "indicator": "Stop W", "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "490003452E", "name": "Oxford Street / Bond Street",
     "lat": 51.5136, "lng": -0.1518, "stop_type": "bus_stop",
     "indicator": "Stop E", "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "490000235E", "name": "Trafalgar Square",
     "lat": 51.5074, "lng": -0.1278, "stop_type": "bus_stop",
     "indicator": "Stop E", "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "490000235W", "name": "Trafalgar Square",
     "lat": 51.5073, "lng": -0.1281, "stop_type": "bus_stop",
     "indicator": "Stop W", "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "9400ZZLUVIC1", "name": "Victoria Underground Station",
     "lat": 51.4965, "lng": -0.1447, "stop_type": "metro_stop",
     "indicator": None, "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "9400ZZLUKNG1", "name": "King's Cross St. Pancras Underground Station",
     "lat": 51.5309, "lng": -0.1233, "stop_type": "metro_stop",
     "indicator": None, "locality": "Camden", "parent_locality": "London"},
    {"atco_code": "9400ZZLUOXF1", "name": "Oxford Circus Underground Station",
     "lat": 51.5152, "lng": -0.1415, "stop_type": "metro_stop",
     "indicator": None, "locality": "Westminster", "parent_locality": "London"},
    {"atco_code": "9400ZZLUBRX1", "name": "Brixton Underground Station",
     "lat": 51.4627, "lng": -0.1145, "stop_type": "metro_stop",
     "indicator": None, "locality": "Lambeth", "parent_locality": "London"},
    {"atco_code": "4200A01", "name": "Manchester Piccadilly Rail Station",
     "lat": 53.4775, "lng": -2.2309, "stop_type": "rail_station",
     "indicator": None, "locality": "Manchester", "parent_locality": "Greater Manchester"},
    {"atco_code": "4200A02", "name": "Manchester Victoria Rail Station",
     "lat": 53.4887, "lng": -2.2437, "stop_type": "rail_station",
     "indicator": None, "locality": "Manchester", "parent_locality": "Greater Manchester"},
    {"atco_code": "1800SB01061", "name": "Birmingham New Street Rail Station",
     "lat": 52.4775, "lng": -1.9001, "stop_type": "rail_station",
     "indicator": None, "locality": "Birmingham", "parent_locality": "West Midlands"},
    {"atco_code": "3290YYA00001", "name": "Leeds Rail Station",
     "lat": 53.7959, "lng": -1.5492, "stop_type": "rail_station",
     "indicator": None, "locality": "Leeds", "parent_locality": "West Yorkshire"},
    {"atco_code": "6100GLS10001", "name": "Glasgow Central Rail Station",
     "lat": 55.8581, "lng": -4.2576, "stop_type": "rail_station",
     "indicator": None, "locality": "Glasgow", "parent_locality": "Greater Glasgow"},
    {"atco_code": "9300EDN", "name": "Edinburgh Waverley Rail Station",
     "lat": 55.9521, "lng": -3.1895, "stop_type": "rail_station",
     "indicator": None, "locality": "Edinburgh", "parent_locality": "Lothian"},
    {"atco_code": "MANAIRPORT", "name": "Manchester Airport",
     "lat": 53.3658, "lng": -2.2727, "stop_type": "airport",
     "indicator": None, "locality": "Manchester", "parent_locality": "Greater Manchester"},
]

_COLUMN_MAP = {
    "ATCOCode": "atco_code",
    "CommonName": "name",
    "Latitude": "lat",
    "Longitude": "lng",
    "StopType": "stop_type_raw",
    "Indicator": "indicator",
    "LocalityName": "locality",
    "ParentLocalityName": "parent_locality",
}


class TransportStop(BaseModel):
    model_config = ConfigDict(extra="allow")
    atco_code: str
    name: str
    lat: float
    lng: float
    stop_type: StopType
    indicator: str | None = None
    locality: str | None = None
    parent_locality: str | None = None


class NaPTANLookup:
    """In-memory NaPTAN stop lookup with haversine spatial queries."""

    def __init__(self, stops: list[TransportStop]) -> None:
        self._stops = stops
        self._index: dict[str, TransportStop] = {s.atco_code: s for s in stops}

    @classmethod
    def from_csv(cls, path: Path) -> NaPTANLookup:
        """Load from the official NaPTAN CSV (GOV.UK download)."""
        df = pd.read_csv(path, low_memory=False)
        rename = {k: v for k, v in _COLUMN_MAP.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)
        stops = []
        for _, row in df.iterrows():
            raw_type = str(row.get("stop_type_raw", ""))
            mapped = _NAPTAN_STOP_TYPE_MAP.get(raw_type, "bus_stop")
            stops.append(TransportStop(
                atco_code=str(row["atco_code"]),
                name=str(row["name"]),
                lat=float(row["lat"]),
                lng=float(row["lng"]),
                stop_type=mapped,  # type: ignore[arg-type]
                indicator=(
                    str(row["indicator"])
                    if "indicator" in df.columns and pd.notna(row.get("indicator"))
                    else None
                ),
                locality=(
                    str(row["locality"])
                    if "locality" in df.columns and pd.notna(row.get("locality"))
                    else None
                ),
                parent_locality=(
                    str(row["parent_locality"])
                    if "parent_locality" in df.columns and pd.notna(row.get("parent_locality"))
                    else None
                ),
            ))
        return cls(stops)

    @classmethod
    def from_default(cls) -> NaPTANLookup:
        """Return a 20-stop demo instance."""
        return cls([TransportStop(**s) for s in _DEMO_STOPS])  # type: ignore[arg-type]

    def nearest(
        self,
        lat: float,
        lng: float,
        *,
        stop_type: StopType | None = None,
        limit: int = 5,
    ) -> list[tuple[TransportStop, float]]:
        """Return the nearest stops (stop, distance_m) sorted by distance ascending."""
        origin = Point(lat=lat, lng=lng)
        candidates = (
            self._stops
            if stop_type is None
            else [s for s in self._stops if s.stop_type == stop_type]
        )
        scored = [(s, haversine_m(origin.lat, origin.lng, s.lat, s.lng)) for s in candidates]
        scored.sort(key=lambda x: x[1])
        return scored[:limit]

    def within_radius(
        self,
        lat: float,
        lng: float,
        *,
        radius_m: float = 1000,
        stop_type: StopType | None = None,
    ) -> list[tuple[TransportStop, float]]:
        """Return all stops within radius_m sorted by distance ascending."""
        origin = Point(lat=lat, lng=lng)
        candidates = (
            self._stops
            if stop_type is None
            else [s for s in self._stops if s.stop_type == stop_type]
        )
        result = []
        for s in candidates:
            d = haversine_m(origin.lat, origin.lng, s.lat, s.lng)
            if d <= radius_m:
                result.append((s, d))
        result.sort(key=lambda x: x[1])
        return result

    def by_atco(self, atco_code: str) -> TransportStop | None:
        """Return the stop with the given ATCO code, or None."""
        return self._index.get(atco_code)
