"""Registry of known UK councils running Idox Public Access.

The registry is deliberately small at first — it seeds the aggregator with
a handful of confirmed councils and is intended to grow as more are
verified. Each entry encodes whether the council publishes an ArcGIS
FeatureServer (observed on Apr 2026):

* Lambeth, Barnet — public ArcGIS (``server/rest/services/PALIVE/...``)
* Westminster, Manchester, Southwark, Leeds — ArcGIS blocked externally;
  use HTML transport

New councils can be registered at runtime by constructing a
:class:`CouncilConfig` and dropping it into :data:`KNOWN_COUNCILS`.
"""

from __future__ import annotations

from collections.abc import Iterable

from uk_property_apis.idox.models import CouncilConfig


def _council(
    slug: str,
    name: str,
    public_access_host: str,
    *,
    arcgis_base_url: str | None = None,
) -> CouncilConfig:
    return CouncilConfig(
        slug=slug,
        name=name,
        public_access_base_url=f"https://{public_access_host}",
        arcgis_base_url=arcgis_base_url,
    )


KNOWN_COUNCILS: dict[str, CouncilConfig] = {
    c.slug: c
    for c in (
        _council(
            "lambeth",
            "Lambeth",
            "planning.lambeth.gov.uk",
            arcgis_base_url="https://planning.lambeth.gov.uk/server",
        ),
        _council(
            "barnet",
            "Barnet",
            "publicaccess.barnet.gov.uk",
            arcgis_base_url="https://publicaccess.barnet.gov.uk/server",
        ),
        _council(
            "westminster",
            "Westminster",
            "idoxpa.westminster.gov.uk",
        ),
        _council(
            "manchester",
            "Manchester",
            "pa.manchester.gov.uk",
        ),
        _council(
            "southwark",
            "Southwark",
            "planning.southwark.gov.uk",
        ),
        _council(
            "leeds",
            "Leeds",
            "publicaccess.leeds.gov.uk",
        ),
    )
}


def get_council(slug: str) -> CouncilConfig:
    """Look up a known council by slug (case-insensitive)."""

    key = slug.strip().lower()
    try:
        return KNOWN_COUNCILS[key]
    except KeyError as exc:
        known = ", ".join(sorted(KNOWN_COUNCILS)) or "(none)"
        msg = f"Unknown council {slug!r}; known: {known}"
        raise KeyError(msg) from exc


def arcgis_enabled_councils() -> Iterable[CouncilConfig]:
    """Yield the councils that publish a reachable ArcGIS FeatureServer."""

    return (c for c in KNOWN_COUNCILS.values() if c.arcgis_base_url is not None)


__all__ = [
    "KNOWN_COUNCILS",
    "arcgis_enabled_councils",
    "get_council",
]
