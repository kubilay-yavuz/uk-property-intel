"""In-memory loader for Ofcom Connected Nations postcode broadband data.

The live Ofcom dataset ships as a nested ZIP of ~120 per-area CSVs
totalling ~32 MB compressed / ~150 MB uncompressed. We expose three
loading modes, mirroring the ``UKCP18Lookup`` pattern:

* :meth:`BroadbandLookup.from_default` — bundled demo seed with ~8
  real postcodes spanning urban / suburban / rural coverage.
  Good enough for smoke tests; drastically undercounts real runs.
* :meth:`BroadbandLookup.from_csv_path` — parse a local CSV
  (e.g. the result of ``zcat`` concatenating the Ofcom area CSVs).
* :meth:`BroadbandLookup.from_ofcom_zip_url` — async streams the
  official Ofcom nested-ZIP URL, unzips the inner ZIP in-memory,
  and concatenates every area CSV. This is the production-ready
  mode for full-UK coverage; it takes ~5-10 s on a first call and
  is then cached in-process.

Callers that need a more bespoke shape (e.g. only SFBB + UFBB
columns) can build a :class:`BroadbandLookup` directly from a list of
:class:`BroadbandCoverage` rows.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from uk_property_data.broadband.models import BroadbandCoverage

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Demo seed — eight real rows pulled from the 202407 Ofcom postcode
# data, chosen to cover Central London + Westminster + Cambridge +
# Edinburgh + Manchester + rural Dumfries so tests exercise both
# the gigabit-covered happy path and the below-USO edge case.
_DEMO_ROWS: list[Mapping[str, Any]] = [
    {
        "postcode": "SW1A 1AA",
        "postcode_area": "SW",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 0.0,
        "pct_premises_below_30m": 66.7,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 0.0,
        "ufbb_pct": 0.0,
        "ufbb_100m_pct": 0.0,
        "gigabit_pct": 0.0,
        "nga_pct": 0.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "SW11 2EE",
        "postcode_area": "SW",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 0.0,
        "pct_premises_below_30m": 0.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 100.0,
        "ufbb_pct": 100.0,
        "ufbb_100m_pct": 100.0,
        "gigabit_pct": 100.0,
        "nga_pct": 100.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "SS2 5RH",
        "postcode_area": "SS",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 0.0,
        "pct_premises_below_30m": 0.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 100.0,
        "ufbb_pct": 94.1,
        "ufbb_100m_pct": 94.1,
        "gigabit_pct": 94.1,
        "nga_pct": 100.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "CB1 2PB",
        "postcode_area": "CB",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 0.0,
        "pct_premises_below_30m": 0.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 100.0,
        "ufbb_pct": 100.0,
        "ufbb_100m_pct": 100.0,
        "gigabit_pct": 100.0,
        "nga_pct": 100.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "EH1 3DG",
        "postcode_area": "EH",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 0.0,
        "pct_premises_below_30m": 0.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 100.0,
        "ufbb_pct": 100.0,
        "ufbb_100m_pct": 100.0,
        "gigabit_pct": 100.0,
        "nga_pct": 100.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "M14 5PJ",
        "postcode_area": "M",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 0.0,
        "pct_premises_below_30m": 0.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 100.0,
        "ufbb_pct": 100.0,
        "ufbb_100m_pct": 100.0,
        "gigabit_pct": 100.0,
        "nga_pct": 100.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "DG10 9NL",
        "postcode_area": "DG",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 100.0,
        "pct_premises_below_10m": 100.0,
        "pct_premises_below_30m": 100.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 0.0,
        "ufbb_pct": 0.0,
        "ufbb_100m_pct": 0.0,
        "gigabit_pct": 0.0,
        "nga_pct": 0.0,
        "fwa_decent_pct": 0.0,
    },
    {
        "postcode": "DG10 9RG",
        "postcode_area": "DG",
        "pct_premises_below_2m": 0.0,
        "pct_premises_below_5m": 0.0,
        "pct_premises_below_10m": 66.7,
        "pct_premises_below_30m": 100.0,
        "pct_premises_below_uso": 0.0,
        "sfbb_pct": 0.0,
        "ufbb_pct": 0.0,
        "ufbb_100m_pct": 0.0,
        "gigabit_pct": 0.0,
        "nga_pct": 100.0,
        "fwa_decent_pct": 0.0,
    },
]


# Canonical Ofcom column → :class:`BroadbandCoverage` field mapping.
_OFCOM_COLUMNS: Mapping[str, str] = {
    "postcode_space": "postcode",
    "postcode area": "postcode_area",
    "% of premises unable to receive 2Mbit/s": "pct_premises_below_2m",
    "% of premises unable to receive 5Mbit/s": "pct_premises_below_5m",
    "% of premises unable to receive 10Mbit/s": "pct_premises_below_10m",
    "% of premises unable to receive 30Mbit/s": "pct_premises_below_30m",
    "% of premises below the USO": "pct_premises_below_uso",
    "SFBB availability (% premises)": "sfbb_pct",
    "UFBB availability (% premises)": "ufbb_pct",
    "UFBB (100Mbit/s) availability (% premises)": "ufbb_100m_pct",
    "Gigabit availability (% premises)": "gigabit_pct",
    "% of premises with NGA": "nga_pct",
    "% of premises able to receive decent broadband from FWA": "fwa_decent_pct",
}


def _compact_postcode(raw: str) -> str:
    """Normalise ``sw1a 1aa`` / ``SW1A1AA`` → ``SW1A1AA`` for fast lookup."""

    return "".join(ch for ch in raw.upper() if not ch.isspace())


def _coerce_row(row: Mapping[str, str]) -> BroadbandCoverage | None:
    """Coerce one Ofcom-shaped CSV row into a :class:`BroadbandCoverage`.

    Returns ``None`` when the row has no recognisable postcode so we
    can silently skip header junk from pre-concatenated multi-area
    CSV dumps (downstream callers typically ``zcat`` multiple Ofcom
    CSVs into one and forget to dedupe the header rows).
    """

    def _f(col: str) -> float:
        raw = row.get(col, "").strip()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            return 0.0

    postcode_space = (row.get("postcode_space") or "").strip()
    postcode_area = (row.get("postcode area") or "").strip()
    if not postcode_space or not postcode_area:
        return None
    if postcode_space.lower() == "postcode_space":  # Leaked header row.
        return None

    return BroadbandCoverage(
        postcode=postcode_space,
        postcode_area=postcode_area,
        pct_premises_below_2m=_f("% of premises unable to receive 2Mbit/s"),
        pct_premises_below_5m=_f("% of premises unable to receive 5Mbit/s"),
        pct_premises_below_10m=_f("% of premises unable to receive 10Mbit/s"),
        pct_premises_below_30m=_f("% of premises unable to receive 30Mbit/s"),
        pct_premises_below_uso=_f("% of premises below the USO"),
        sfbb_pct=_f("SFBB availability (% premises)"),
        ufbb_pct=_f("UFBB availability (% premises)"),
        ufbb_100m_pct=_f("UFBB (100Mbit/s) availability (% premises)"),
        gigabit_pct=_f("Gigabit availability (% premises)"),
        nga_pct=_f("% of premises with NGA"),
        fwa_decent_pct=_f(
            "% of premises able to receive decent broadband from FWA"
        ),
    )


class BroadbandLookup:
    """In-memory broadband-coverage lookup keyed by compact postcode."""

    def __init__(self, rows: Iterable[BroadbandCoverage]) -> None:
        self._rows: list[BroadbandCoverage] = list(rows)
        self._index: dict[str, BroadbandCoverage] = {
            _compact_postcode(r.postcode): r for r in self._rows
        }

    def __len__(self) -> int:
        return len(self._rows)

    def query(self, postcode: str) -> BroadbandCoverage | None:
        """Return the coverage row for ``postcode`` (case / whitespace-insensitive)."""

        if not postcode:
            return None
        return self._index.get(_compact_postcode(postcode))

    def areas(self) -> list[str]:
        """Return a sorted, deduplicated list of postcode areas in the lookup."""

        seen: set[str] = set()
        out: list[str] = []
        for row in self._rows:
            if row.postcode_area not in seen:
                seen.add(row.postcode_area)
                out.append(row.postcode_area)
        out.sort()
        return out

    @classmethod
    def from_rows(
        cls, rows: Iterable[Mapping[str, Any] | BroadbandCoverage]
    ) -> BroadbandLookup:
        """Build a lookup from already-typed rows or plain dicts."""

        coerced: list[BroadbandCoverage] = []
        for row in rows:
            if isinstance(row, BroadbandCoverage):
                coerced.append(row)
            else:
                coerced.append(BroadbandCoverage(**row))
        return cls(coerced)

    @classmethod
    def from_default(cls) -> BroadbandLookup:
        """Return a lookup populated from the bundled demo seed."""

        return cls.from_rows(_DEMO_ROWS)

    @classmethod
    def from_csv_path(cls, path: Path | str) -> BroadbandLookup:
        """Load the lookup from a local Ofcom-shaped postcode CSV.

        The CSV may be a single area CSV from the nested ZIP or a
        caller-concatenated superset. Unknown columns are ignored;
        only the subset listed in :data:`_OFCOM_COLUMNS` is parsed.
        """

        path = Path(path)
        with path.open("r", encoding="utf-8", newline="") as handle:
            return cls._from_csv_reader(csv.DictReader(handle))

    @classmethod
    async def from_csv_url(
        cls,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> BroadbandLookup:
        """Stream + parse a remote Ofcom-shaped postcode CSV."""

        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=timeout)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
        finally:
            if owns_client:
                await client.aclose()
        reader = csv.DictReader(io.StringIO(text))
        return cls._from_csv_reader(reader)

    @classmethod
    async def from_ofcom_zip_url(
        cls,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> BroadbandLookup:
        """Stream the Ofcom nested-ZIP URL and load every area CSV.

        The Ofcom download is a ZIP that contains another ZIP, which
        in turn contains one CSV per postcode area. We download the
        outer archive once, open both layers in-memory with
        :mod:`zipfile`, and concatenate the area CSVs into a single
        :class:`BroadbandLookup`.

        All I/O happens in one call; subsequent :meth:`query` lookups
        are O(1) dict hits.
        """

        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            outer_bytes = resp.content
        finally:
            if owns_client:
                await client.aclose()

        rows: list[BroadbandCoverage] = []
        with zipfile.ZipFile(io.BytesIO(outer_bytes)) as outer_zip:
            for outer_name in outer_zip.namelist():
                if not outer_name.lower().endswith(".zip"):
                    continue
                inner_bytes = outer_zip.read(outer_name)
                with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner_zip:
                    for inner_name in inner_zip.namelist():
                        if not inner_name.lower().endswith(".csv"):
                            continue
                        with inner_zip.open(inner_name) as handle:
                            text_stream = io.TextIOWrapper(
                                handle, encoding="utf-8", newline=""
                            )
                            rows.extend(
                                row
                                for row in _iter_rows(
                                    csv.DictReader(text_stream)
                                )
                                if row is not None
                            )
        return cls(rows)

    @classmethod
    def _from_csv_reader(cls, reader: csv.DictReader[str]) -> BroadbandLookup:
        rows = list(_iter_rows(reader))
        return cls(r for r in rows if r is not None)


def _iter_rows(
    reader: csv.DictReader[str],
) -> Iterable[BroadbandCoverage | None]:
    for raw in reader:
        yield _coerce_row(raw)


__all__ = [
    "BroadbandCoverage",
    "BroadbandLookup",
]
