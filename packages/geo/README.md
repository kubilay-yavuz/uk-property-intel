# uk-property-geo

Geospatial engine for every property question that ends in *"…how far?"*.

## What's shipped (v0.1)

| Capability | Module | Backed by |
|---|---|---|
| Great-circle distance | `uk_property_geo.distance.haversine_m` | Pure Python |
| Bounding box around a point | `uk_property_geo.distance.bbox_around` | Pure Python |
| Rank points by distance | `uk_property_geo.distance.sort_by_distance` | Pure Python |
| Nearby amenities (schools, stations, GPs, parks, …) | `uk_property_geo.OverpassClient` | OpenStreetMap Overpass API |

All 38 tests green; zero network required (Overpass client fully mockable via `respx`).

## Quickstart

```python
import asyncio
from uk_property_geo import OverpassClient, AmenityCategory, haversine_m

async def main():
    # Trafalgar Square
    lat, lng = 51.5080, -0.1281

    async with OverpassClient() as client:
        hits = await client.amenities_near(
            lat, lng,
            radius_m=500,
            categories=[AmenityCategory.RAIL_STATION, AmenityCategory.CAFE],
        )
    for h in hits[:5]:
        print(h.category.value, h.name, f"{h.distance_m:.0f} m")

    # Distance between two points (metres).
    d = haversine_m(51.5074, -0.1278, 52.2053, 0.1218)  # London → Cambridge
    print(f"London → Cambridge: {d/1000:.1f} km")

asyncio.run(main())
```

### Amenity categories

```
school, supermarket, gp, hospital, park,
rail_station, tube_station, bus_stop,
restaurant, pub, cafe, gym, pharmacy
```

Each category maps to one or more curated OSM tag filters
(`amenity=school`, `railway=station`, `healthcare=doctor`, …). New
categories are a one-line change to `overpass._CATEGORY_TAG_FILTERS`.

## Planned (not yet shipped)

- **Postcode → coordinates** — delegates to `uk_property_apis.PostcodesClient` in the agent today.
- **OSRM routing** — driving/walking/cycling times from a self-hosted
  UK OSM extract.
- **OpenTripPlanner isochrones** — "everything reachable within 30 min
  on public transport" using GTFS.
- **NaPTAN station proximity** — official UK transport stop graph.
- **School catchments** — DfE open-data overlay on a property polygon.
- **H3 tiling** — efficient spatial joins at scale.

These are all tractable but each needs either heavy infra (OSRM/OTP in a
container) or a dedicated data-ingest pipeline. They land as separate PRs
once the downstream agent needs them.

## Install

```bash
uv add uk-property-geo
# Polygon overlays (planned extras)
uv add "uk-property-geo[polygons]"
```
