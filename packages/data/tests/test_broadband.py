"""Tests for the Ofcom broadband postcode loader."""
from __future__ import annotations

import csv
import io
import zipfile
from typing import TYPE_CHECKING

import httpx
import pytest
import respx
from uk_property_data.broadband import BroadbandCoverage, BroadbandLookup

if TYPE_CHECKING:
    from pathlib import Path

_OFCOM_HEADER = [
    "postcode_space",
    "postcode area",
    "% of premises unable to receive 2Mbit/s",
    "% of premises unable to receive 5Mbit/s",
    "% of premises unable to receive 10Mbit/s",
    "% of premises unable to receive 30Mbit/s",
    "% of premises below the USO",
    "SFBB availability (% premises)",
    "UFBB availability (% premises)",
    "UFBB (100Mbit/s) availability (% premises)",
    "Gigabit availability (% premises)",
    "% of premises with NGA",
    "% of premises able to receive decent broadband from FWA",
]


def _ofcom_row(**overrides: object) -> list[str]:
    defaults = {
        "postcode_space": "W1 2AB",
        "postcode area": "W",
        "% of premises unable to receive 2Mbit/s": "0",
        "% of premises unable to receive 5Mbit/s": "0",
        "% of premises unable to receive 10Mbit/s": "0",
        "% of premises unable to receive 30Mbit/s": "0",
        "% of premises below the USO": "0",
        "SFBB availability (% premises)": "100",
        "UFBB availability (% premises)": "95.4",
        "UFBB (100Mbit/s) availability (% premises)": "95.4",
        "Gigabit availability (% premises)": "95.4",
        "% of premises with NGA": "100",
        "% of premises able to receive decent broadband from FWA": "0",
    }
    defaults.update({k: str(v) for k, v in overrides.items()})
    return [defaults[col] for col in _OFCOM_HEADER]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_OFCOM_HEADER)
        for row in rows:
            writer.writerow(row)


class TestFromDefault:
    """The bundled demo seed should surface enough real rows for smoke tests."""

    def test_has_known_urban_postcodes(self) -> None:
        lookup = BroadbandLookup.from_default()
        assert len(lookup) == 8
        assert lookup.query("SW1A 1AA") is not None
        assert lookup.query("CB1 2PB") is not None
        assert lookup.query("DG10 9NL") is not None

    def test_urban_row_shape(self) -> None:
        lookup = BroadbandLookup.from_default()
        row = lookup.query("SW11 2EE")
        assert row is not None
        assert row.postcode == "SW11 2EE"
        assert row.postcode_area == "SW"
        assert row.sfbb_pct == 100.0
        assert row.gigabit_pct == 100.0
        assert row.pct_premises_below_30m == 0.0

    def test_rural_uso_edge_case(self) -> None:
        lookup = BroadbandLookup.from_default()
        row = lookup.query("DG10 9NL")
        assert row is not None
        assert row.sfbb_pct == 0.0
        assert row.gigabit_pct == 0.0
        assert row.pct_premises_below_10m == 100.0
        assert row.pct_premises_below_30m == 100.0

    def test_query_case_and_whitespace_insensitive(self) -> None:
        lookup = BroadbandLookup.from_default()
        assert lookup.query("sw1a 1aa") is not None
        assert lookup.query("SW1A1AA") is not None
        assert lookup.query(" SW1A 1AA ") is not None

    def test_query_unknown_postcode_returns_none(self) -> None:
        lookup = BroadbandLookup.from_default()
        assert lookup.query("ZZ99 9ZZ") is None

    def test_query_empty_string_returns_none(self) -> None:
        lookup = BroadbandLookup.from_default()
        assert lookup.query("") is None

    def test_areas_sorted_and_deduped(self) -> None:
        lookup = BroadbandLookup.from_default()
        areas = lookup.areas()
        assert areas == sorted(areas)
        assert len(areas) == len(set(areas))
        assert "SW" in areas
        assert "DG" in areas


class TestFromCsvPath:
    """Parsing round-trips through the Ofcom column names."""

    def test_parses_rows(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "coverage.csv"
        _write_csv(
            csv_file,
            [
                _ofcom_row(postcode_space="AB1 0AA", **{"postcode area": "AB"}),
                _ofcom_row(
                    postcode_space="AB1 0AB",
                    **{"postcode area": "AB"},
                    **{"SFBB availability (% premises)": "20"},
                ),
            ],
        )

        lookup = BroadbandLookup.from_csv_path(csv_file)
        assert len(lookup) == 2
        ab_1 = lookup.query("AB1 0AA")
        assert ab_1 is not None
        assert ab_1.sfbb_pct == 100.0
        ab_2 = lookup.query("AB1 0AB")
        assert ab_2 is not None
        assert ab_2.sfbb_pct == 20.0

    def test_skips_rows_without_postcode(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "coverage.csv"
        _write_csv(
            csv_file,
            [
                _ofcom_row(postcode_space="", **{"postcode area": "AB"}),
                _ofcom_row(postcode_space="AB1 0AA", **{"postcode area": "AB"}),
            ],
        )

        lookup = BroadbandLookup.from_csv_path(csv_file)
        assert len(lookup) == 1

    def test_malformed_percent_defaults_to_zero(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "coverage.csv"
        _write_csv(
            csv_file,
            [
                _ofcom_row(
                    postcode_space="AB1 0AA",
                    **{"postcode area": "AB"},
                    **{"SFBB availability (% premises)": "n/a"},
                ),
            ],
        )

        lookup = BroadbandLookup.from_csv_path(csv_file)
        row = lookup.query("AB1 0AA")
        assert row is not None
        assert row.sfbb_pct == 0.0


class TestFromCsvUrl:
    """Remote CSV streaming parses into the same shape as local CSVs."""

    @pytest.mark.asyncio
    async def test_round_trip(self) -> None:
        csv_text = io.StringIO()
        writer = csv.writer(csv_text)
        writer.writerow(_OFCOM_HEADER)
        writer.writerow(
            _ofcom_row(
                postcode_space="W1 0AA",
                **{"postcode area": "W"},
                **{"SFBB availability (% premises)": "85"},
            )
        )

        with respx.mock(assert_all_called=True) as rmock:
            rmock.get("https://example.test/coverage.csv").respond(
                200,
                text=csv_text.getvalue(),
                headers={"content-type": "text/csv"},
            )
            async with httpx.AsyncClient() as client:
                lookup = await BroadbandLookup.from_csv_url(
                    "https://example.test/coverage.csv",
                    client=client,
                )

        assert len(lookup) == 1
        row = lookup.query("W1 0AA")
        assert row is not None
        assert row.sfbb_pct == 85.0


class TestFromOfcomZipUrl:
    """Nested-ZIP loader walks both layers and concatenates every CSV."""

    def _nested_zip_bytes(
        self, per_area_rows: dict[str, list[list[str]]]
    ) -> bytes:
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as inner_zip:
            for area, rows in per_area_rows.items():
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(_OFCOM_HEADER)
                for row in rows:
                    writer.writerow(row)
                inner_zip.writestr(
                    f"{area}_coverage.csv", csv_buffer.getvalue()
                )
        inner.seek(0)

        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as outer_zip:
            outer_zip.writestr("fixed-coverage.zip", inner.getvalue())
        return outer.getvalue()

    @pytest.mark.asyncio
    async def test_walks_nested_zip(self) -> None:
        payload = self._nested_zip_bytes(
            {
                "AB": [
                    _ofcom_row(
                        postcode_space="AB1 0AA", **{"postcode area": "AB"}
                    ),
                ],
                "W": [
                    _ofcom_row(
                        postcode_space="W1 0AA", **{"postcode area": "W"},
                    ),
                    _ofcom_row(
                        postcode_space="W1 0AB",
                        **{"postcode area": "W"},
                        **{"SFBB availability (% premises)": "0"},
                    ),
                ],
            }
        )

        with respx.mock(assert_all_called=True) as rmock:
            rmock.get("https://example.test/coverage.zip").respond(
                200,
                content=payload,
                headers={"content-type": "application/zip"},
            )
            async with httpx.AsyncClient() as client:
                lookup = await BroadbandLookup.from_ofcom_zip_url(
                    "https://example.test/coverage.zip",
                    client=client,
                )

        assert len(lookup) == 3
        assert lookup.query("AB1 0AA") is not None
        assert lookup.query("W1 0AA") is not None
        assert sorted(lookup.areas()) == ["AB", "W"]


class TestFromRows:
    """Callers with custom rows (dict or typed) can bypass the CSV path."""

    def test_accepts_mixed_rows(self) -> None:
        typed = BroadbandCoverage(
            postcode="W1 0AA",
            postcode_area="W",
            pct_premises_below_2m=0.0,
            pct_premises_below_5m=0.0,
            pct_premises_below_10m=0.0,
            pct_premises_below_30m=0.0,
            pct_premises_below_uso=0.0,
            sfbb_pct=100.0,
            ufbb_pct=100.0,
            ufbb_100m_pct=100.0,
            gigabit_pct=100.0,
            nga_pct=100.0,
            fwa_decent_pct=0.0,
        )
        as_dict = {
            "postcode": "W1 0AB",
            "postcode_area": "W",
            "pct_premises_below_2m": 0.0,
            "pct_premises_below_5m": 0.0,
            "pct_premises_below_10m": 0.0,
            "pct_premises_below_30m": 0.0,
            "pct_premises_below_uso": 0.0,
            "sfbb_pct": 50.0,
            "ufbb_pct": 50.0,
            "ufbb_100m_pct": 50.0,
            "gigabit_pct": 50.0,
            "nga_pct": 50.0,
            "fwa_decent_pct": 0.0,
        }

        lookup = BroadbandLookup.from_rows([typed, as_dict])
        assert len(lookup) == 2
        assert lookup.query("W1 0AA") is not None
        assert lookup.query("W1 0AB") is not None
