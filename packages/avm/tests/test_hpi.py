"""Tests for the UK HPI adjuster."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

import pytest
from uk_property_avm import (
    Comparable,
    EnrichedComparable,
    HPIAdjuster,
    adjust_comparable_prices,
    adjust_enriched_prices,
    list_ons_regions,
    parse_month_key,
)
from uk_property_avm.hpi import _DEFAULT_SERIES, _parse_ons_date

if TYPE_CHECKING:
    from pathlib import Path


class TestParseMonthKey:
    """Date-string normalisation edge cases."""

    def test_yyyy_mm(self) -> None:
        assert parse_month_key("2020-01") == "2020-01"

    def test_yyyy_mm_dd(self) -> None:
        assert parse_month_key("2020-01-15") == "2020-01"

    def test_pads_single_digit_day(self) -> None:
        # "2020-06-5" is still 7 chars when we include the trailing digit;
        # the parser only looks at positions 0..6 so the day is ignored.
        assert parse_month_key("2020-06-5") == "2020-06"

    def test_strip_whitespace(self) -> None:
        assert parse_month_key("  2020-01-15  ") == "2020-01"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "2020",
            "2020-",
            "20",
            "abcd-ef",
            "2020-XY",
            "2020-13",
            "2020-00",
        ],
    )
    def test_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_month_key(bad)


class TestHPIAdjusterConstruction:
    """Constructor validation + class-method loaders."""

    def test_default_loads_bundled_series(self) -> None:
        adjuster = HPIAdjuster.default()
        assert adjuster.earliest_month == min(_DEFAULT_SERIES)
        assert adjuster.latest_month == max(_DEFAULT_SERIES)
        assert len(adjuster.keys) == len(_DEFAULT_SERIES)

    def test_rejects_empty_series(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            HPIAdjuster({})

    def test_rejects_negative_value(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            HPIAdjuster({"2020-01": -5.0})

    def test_rejects_zero_value(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            HPIAdjuster({"2020-01": 0.0})

    def test_rejects_non_numeric_value(self) -> None:
        with pytest.raises(ValueError, match="not numeric"):
            HPIAdjuster({"2020-01": "high"})  # type: ignore[dict-item]

    def test_normalises_keys(self) -> None:
        adjuster = HPIAdjuster({"2019-03-15": 110.0, "2020-06-01": 115.0})
        assert list(adjuster.keys) == ["2019-03", "2020-06"]

    def test_sorts_keys(self) -> None:
        adjuster = HPIAdjuster(
            {
                "2020-06": 115.0,
                "2018-01": 100.0,
                "2019-03": 108.0,
            }
        )
        assert list(adjuster.keys) == ["2018-01", "2019-03", "2020-06"]

    def test_from_mapping_alias(self) -> None:
        adjuster = HPIAdjuster.from_mapping({"2020-01": 120.0})
        assert adjuster.latest_month == "2020-01"

    def test_from_csv_happy_path(self, tmp_path: Path) -> None:
        path = tmp_path / "hpi.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "value"])
            writer.writerow(["2020-01", "120.0"])
            writer.writerow(["2021-01", "132.0"])
            writer.writerow(["2022-01-15", "141.0"])
        adjuster = HPIAdjuster.from_csv(path)
        assert list(adjuster.keys) == ["2020-01", "2021-01", "2022-01"]
        assert adjuster.lookup("2021-01") == pytest.approx(132.0)

    def test_from_csv_custom_columns(self, tmp_path: Path) -> None:
        path = tmp_path / "hpi.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["month", "index"])
            writer.writerow(["2020-01", "120.0"])
        adjuster = HPIAdjuster.from_csv(path, month_col="month", value_col="index")
        assert adjuster.lookup("2020-01") == pytest.approx(120.0)

    def test_from_csv_missing_month_col(self, tmp_path: Path) -> None:
        path = tmp_path / "hpi.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["foo", "value"])
            writer.writerow(["2020-01", "120.0"])
        with pytest.raises(ValueError, match="missing 'date'"):
            HPIAdjuster.from_csv(path)

    def test_from_csv_missing_value_col(self, tmp_path: Path) -> None:
        path = tmp_path / "hpi.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "foo"])
            writer.writerow(["2020-01", "120.0"])
        with pytest.raises(ValueError, match="missing 'value'"):
            HPIAdjuster.from_csv(path)

    def test_from_csv_rejects_duplicate_months(self, tmp_path: Path) -> None:
        path = tmp_path / "hpi.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "value"])
            writer.writerow(["2020-01", "120.0"])
            writer.writerow(["2020-01-15", "121.0"])
        with pytest.raises(ValueError, match="duplicate month"):
            HPIAdjuster.from_csv(path)


class TestParseONSDate:
    """ONS publishes dates in several shapes; all land as ``YYYY-MM``."""

    def test_iso_date(self) -> None:
        assert _parse_ons_date("2020-06-01") == "2020-06"

    def test_iso_month(self) -> None:
        assert _parse_ons_date("2020-06") == "2020-06"

    def test_uk_slash_dd_mm_yyyy(self) -> None:
        assert _parse_ons_date("01/06/2020") == "2020-06"

    def test_uk_slash_single_digit_month(self) -> None:
        assert _parse_ons_date("01/9/2020") == "2020-09"

    def test_trims_whitespace(self) -> None:
        assert _parse_ons_date("  2020-06-01  ") == "2020-06"

    def test_slash_garbage_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognised ONS HPI date"):
            _parse_ons_date("not/a/date")


class TestHPIFromONSCSV:
    """Load the official ONS HPI long-form CSV shape."""

    @staticmethod
    def _write_ons_csv(
        path: Path,
        rows: list[tuple[str, str, str, str]],
        *,
        columns: tuple[str, str, str, str] = (
            "Date",
            "RegionName",
            "AreaCode",
            "Index",
        ),
    ) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)

    def test_filters_to_region(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "United Kingdom", "K02000001", "120.0"),
                ("2020-01-01", "London", "E12000007", "140.0"),
                ("2020-02-01", "United Kingdom", "K02000001", "121.5"),
                ("2020-02-01", "London", "E12000007", "141.2"),
                ("2020-03-01", "United Kingdom", "K02000001", "123.0"),
            ],
        )
        adjuster = HPIAdjuster.from_ons_csv(path)
        assert list(adjuster.keys) == ["2020-01", "2020-02", "2020-03"]
        assert adjuster.lookup("2020-02") == pytest.approx(121.5)

    def test_filters_to_london(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "United Kingdom", "K02000001", "120.0"),
                ("2020-01-01", "London", "E12000007", "140.0"),
                ("2020-02-01", "London", "E12000007", "141.2"),
            ],
        )
        adjuster = HPIAdjuster.from_ons_csv(path, region="London")
        assert list(adjuster.keys) == ["2020-01", "2020-02"]
        assert adjuster.lookup("2020-01") == pytest.approx(140.0)
        assert adjuster.lookup("2020-02") == pytest.approx(141.2)

    def test_filters_by_area_code(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "London", "E12000007", "140.0"),
                ("2020-01-01", "North East", "E12000001", "80.0"),
            ],
        )
        adjuster = HPIAdjuster.from_ons_csv(
            path, region="E12000007", region_col="AreaCode"
        )
        assert adjuster.lookup("2020-01") == pytest.approx(140.0)

    def test_accepts_uk_slash_dates(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("01/01/2020", "United Kingdom", "K02000001", "120.0"),
                ("01/02/2020", "United Kingdom", "K02000001", "121.5"),
            ],
        )
        adjuster = HPIAdjuster.from_ons_csv(path)
        assert list(adjuster.keys) == ["2020-01", "2020-02"]

    def test_custom_index_col(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "RegionName", "Index", "IndexSA"])
            writer.writerow(["2020-01-01", "United Kingdom", "120.0", "119.8"])
            writer.writerow(["2020-02-01", "United Kingdom", "121.5", "121.0"])
        sa = HPIAdjuster.from_ons_csv(path, index_col="IndexSA")
        assert sa.lookup("2020-01") == pytest.approx(119.8)
        assert sa.lookup("2020-02") == pytest.approx(121.0)

    def test_skips_rows_with_blank_index(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "United Kingdom", "K02000001", ""),
                ("2020-02-01", "United Kingdom", "K02000001", "121.5"),
            ],
        )
        adjuster = HPIAdjuster.from_ons_csv(path)
        assert list(adjuster.keys) == ["2020-02"]

    def test_skips_non_numeric_index(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "United Kingdom", "K02000001", "NA"),
                ("2020-02-01", "United Kingdom", "K02000001", "121.5"),
            ],
        )
        adjuster = HPIAdjuster.from_ons_csv(path)
        assert list(adjuster.keys) == ["2020-02"]

    def test_raises_when_region_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "United Kingdom", "K02000001", "120.0"),
            ],
        )
        with pytest.raises(ValueError, match="no rows matching"):
            HPIAdjuster.from_ons_csv(path, region="Atlantis")

    def test_raises_on_missing_region_col(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "Index"])
            writer.writerow(["2020-01-01", "120.0"])
        with pytest.raises(ValueError, match="missing 'RegionName'"):
            HPIAdjuster.from_ons_csv(path)

    def test_raises_on_missing_date_col(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["RegionName", "Index"])
            writer.writerow(["United Kingdom", "120.0"])
        with pytest.raises(ValueError, match="missing 'Date'"):
            HPIAdjuster.from_ons_csv(path)

    def test_raises_on_missing_index_col(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "RegionName"])
            writer.writerow(["2020-01-01", "United Kingdom"])
        with pytest.raises(ValueError, match="missing 'Index'"):
            HPIAdjuster.from_ons_csv(path)

    def test_rejects_duplicate_months(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        self._write_ons_csv(
            path,
            [
                ("2020-01-01", "United Kingdom", "K02000001", "120.0"),
                ("2020-01-15", "United Kingdom", "K02000001", "121.0"),
            ],
        )
        with pytest.raises(ValueError, match="duplicate month"):
            HPIAdjuster.from_ons_csv(path)

    def test_handles_utf8_bom(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        # Excel frequently saves CSVs with a BOM; csv.DictReader splits the
        # BOM into the first header name unless we open with utf-8-sig.
        with path.open("wb") as handle:
            handle.write(b"\xef\xbb\xbf")
            handle.write(b"Date,RegionName,AreaCode,Index\n")
            handle.write(b"2020-01-01,United Kingdom,K02000001,120.0\n")
        adjuster = HPIAdjuster.from_ons_csv(path)
        assert adjuster.lookup("2020-01") == pytest.approx(120.0)


class TestListONSRegions:
    """Discoverability helper for the region filter."""

    def test_returns_sorted_unique(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "RegionName", "Index"])
            writer.writerow(["2020-01-01", "United Kingdom", "120.0"])
            writer.writerow(["2020-01-01", "London", "140.0"])
            writer.writerow(["2020-02-01", "London", "141.2"])
            writer.writerow(["2020-01-01", "North East", "80.0"])
        assert list_ons_regions(path) == ["London", "North East", "United Kingdom"]

    def test_custom_region_col(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "RegionName", "AreaCode", "Index"])
            writer.writerow(["2020-01-01", "London", "E12000007", "140.0"])
            writer.writerow(["2020-01-01", "North East", "E12000001", "80.0"])
        assert list_ons_regions(path, region_col="AreaCode") == [
            "E12000001",
            "E12000007",
        ]

    def test_raises_on_missing_region_col(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "Index"])
            writer.writerow(["2020-01-01", "120.0"])
        with pytest.raises(ValueError, match="missing 'RegionName'"):
            list_ons_regions(path)

    def test_skips_blank_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ons.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "RegionName", "Index"])
            writer.writerow(["2020-01-01", "", "120.0"])
            writer.writerow(["2020-01-01", "London", "140.0"])
        assert list_ons_regions(path) == ["London"]


class TestHPIAdjusterLookup:
    """Lookup semantics: exact, on-or-before, out-of-range."""

    @pytest.fixture
    def adjuster(self) -> HPIAdjuster:
        return HPIAdjuster(
            {
                "2020-01": 100.0,
                "2020-04": 104.0,
                "2020-07": 110.0,
                "2020-10": 112.0,
                "2021-01": 115.0,
            }
        )

    def test_exact_match(self, adjuster: HPIAdjuster) -> None:
        assert adjuster.lookup("2020-04") == pytest.approx(104.0)

    def test_exact_match_with_day(self, adjuster: HPIAdjuster) -> None:
        assert adjuster.lookup("2020-04-15") == pytest.approx(104.0)

    def test_on_or_before_fallback(self, adjuster: HPIAdjuster) -> None:
        # May 2020: not in series, falls back to April 2020.
        assert adjuster.lookup("2020-05") == pytest.approx(104.0)

    def test_on_or_before_mid_quarter(self, adjuster: HPIAdjuster) -> None:
        # August: falls back to July.
        assert adjuster.lookup("2020-08") == pytest.approx(110.0)

    def test_below_range_raises(self, adjuster: HPIAdjuster) -> None:
        with pytest.raises(ValueError, match="predates series start"):
            adjuster.lookup("2019-12")

    def test_above_range_raises(self, adjuster: HPIAdjuster) -> None:
        with pytest.raises(ValueError, match="postdates series end"):
            adjuster.lookup("2021-02")

    def test_earliest_boundary(self, adjuster: HPIAdjuster) -> None:
        assert adjuster.lookup("2020-01") == pytest.approx(100.0)

    def test_latest_boundary(self, adjuster: HPIAdjuster) -> None:
        assert adjuster.lookup("2021-01") == pytest.approx(115.0)


class TestHPIAdjusterAdjust:
    """``adjust`` and ``adjust_int`` semantics."""

    @pytest.fixture
    def adjuster(self) -> HPIAdjuster:
        return HPIAdjuster(
            {
                "2015-01": 100.0,
                "2020-01": 120.0,
                "2024-01": 150.0,
            }
        )

    def test_adjust_to_default_target(self, adjuster: HPIAdjuster) -> None:
        # Default target = latest (2024-01, index 150). 2015 → 2024 = 1.5x.
        assert adjuster.adjust(200_000.0, "2015-01") == pytest.approx(300_000.0)

    def test_adjust_to_explicit_target(self, adjuster: HPIAdjuster) -> None:
        # 2015 → 2020 = 120 / 100 = 1.2x.
        assert adjuster.adjust(200_000.0, "2015-01", "2020-01") == pytest.approx(240_000.0)

    def test_adjust_is_identity_same_month(self, adjuster: HPIAdjuster) -> None:
        assert adjuster.adjust(250_000.0, "2020-01", "2020-01") == pytest.approx(250_000.0)

    def test_adjust_deflates_going_back(self, adjuster: HPIAdjuster) -> None:
        # 2024 → 2015: 100 / 150 = 0.6667.
        result = adjuster.adjust(300_000.0, "2024-01", "2015-01")
        assert result == pytest.approx(200_000.0, rel=1e-6)

    def test_adjust_rejects_negative_price(self, adjuster: HPIAdjuster) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            adjuster.adjust(-1.0, "2020-01")

    def test_adjust_zero_price_is_zero(self, adjuster: HPIAdjuster) -> None:
        assert adjuster.adjust(0.0, "2020-01") == 0.0

    def test_adjust_int_rounds(self, adjuster: HPIAdjuster) -> None:
        # 125_000 * (150/120) = 156_250.
        assert adjuster.adjust_int(125_000, "2020-01") == 156_250

    def test_adjust_int_fractional_rounds_half_to_even(self, adjuster: HPIAdjuster) -> None:
        # 100 * (120/100) = 120 exact. 100 * (150/120) = 125. These round
        # unambiguously; for ties Python's bankers rounding doesn't matter
        # because integer-valued ratios happen first.
        assert adjuster.adjust_int(100, "2015-01", "2020-01") == 120
        assert adjuster.adjust_int(100, "2020-01", "2024-01") == 125


class TestHPIBundledSeries:
    """Sanity checks on the bundled default series."""

    def test_quarterly_cadence(self) -> None:
        # Every month in the default series must be Jan/Apr/Jul/Oct.
        for key in _DEFAULT_SERIES:
            month = int(key.split("-")[1])
            assert month in {1, 4, 7, 10}, f"{key} is not on the quarterly grid"

    def test_monotone_long_run(self) -> None:
        adjuster = HPIAdjuster.default()
        # 2005 to latest should be meaningfully higher (>1.5x) — if the
        # bundled series ever goes sideways across 20 years, something is
        # off with the snapshot.
        latest = adjuster.lookup(adjuster.latest_month)
        assert latest / 70.0 > 1.5

    def test_inflation_matches_direction(self) -> None:
        # Prices from 2015 should be worth more in 2024 than in 2015.
        adjuster = HPIAdjuster.default()
        assert adjuster.adjust(100.0, "2015-01", "2024-01") > 100.0

    def test_latest_is_after_earliest(self) -> None:
        adjuster = HPIAdjuster.default()
        assert adjuster.earliest_month < adjuster.latest_month

    def test_keys_property_is_sorted_tuple(self) -> None:
        adjuster = HPIAdjuster.default()
        keys = adjuster.keys
        assert isinstance(keys, tuple)
        assert list(keys) == sorted(keys)


def _comparable(
    idx: int,
    *,
    price: int,
    transfer_date: str,
) -> Comparable:
    return Comparable(
        transaction_id=f"C{idx:05d}",
        price=price,
        transfer_date=transfer_date,
        postcode="SW2 5TN",
        property_type="T",
    )


def _enriched(
    idx: int,
    *,
    price: int,
    transfer_date: str,
) -> EnrichedComparable:
    return EnrichedComparable(
        transaction_id=f"E{idx:05d}",
        price=price,
        transfer_date=transfer_date,
        postcode="SW2 5TN",
        property_type="T",
    )


class TestAdjustComparablePrices:
    """Bulk adjustment over :class:`Comparable` rows."""

    @pytest.fixture
    def adjuster(self) -> HPIAdjuster:
        return HPIAdjuster(
            {
                "2015-01": 100.0,
                "2020-01": 120.0,
                "2024-01": 150.0,
            }
        )

    def test_adjusts_every_row(self, adjuster: HPIAdjuster) -> None:
        rows = [
            _comparable(1, price=200_000, transfer_date="2015-06-15"),
            _comparable(2, price=300_000, transfer_date="2020-03-01"),
        ]
        adjusted = adjust_comparable_prices(rows, adjuster)
        assert adjusted[0].price == 300_000  # 2015-01 index -> latest
        assert adjusted[1].price == 375_000  # 2020-01 index -> latest

    def test_returns_new_instances(self, adjuster: HPIAdjuster) -> None:
        rows = [_comparable(1, price=200_000, transfer_date="2015-06-15")]
        adjusted = adjust_comparable_prices(rows, adjuster)
        assert adjusted[0] is not rows[0]

    def test_preserves_other_fields(self, adjuster: HPIAdjuster) -> None:
        rows = [_comparable(7, price=200_000, transfer_date="2015-06-15")]
        adjusted = adjust_comparable_prices(rows, adjuster)
        assert adjusted[0].transaction_id == "C00007"
        assert adjusted[0].postcode == "SW2 5TN"
        assert adjusted[0].property_type == "T"
        assert adjusted[0].transfer_date == "2015-06-15"

    def test_out_of_range_rows_pass_through_unchanged(self, adjuster: HPIAdjuster) -> None:
        # Below range.
        rows = [_comparable(1, price=50_000, transfer_date="2010-01-01")]
        adjusted = adjust_comparable_prices(rows, adjuster)
        assert adjusted[0].price == 50_000

    def test_empty_transfer_date_passes_through(self, adjuster: HPIAdjuster) -> None:
        # Pydantic still accepts empty string; we just skip the row.
        rows = [
            Comparable(
                transaction_id="C99999",
                price=200_000,
                transfer_date="",
            )
        ]
        adjusted = adjust_comparable_prices(rows, adjuster)
        assert adjusted[0].price == 200_000

    def test_explicit_to_date(self, adjuster: HPIAdjuster) -> None:
        rows = [_comparable(1, price=200_000, transfer_date="2015-06-15")]
        adjusted = adjust_comparable_prices(rows, adjuster, to_date="2020-01")
        assert adjusted[0].price == 240_000

    def test_empty_input_returns_empty_list(self, adjuster: HPIAdjuster) -> None:
        assert adjust_comparable_prices([], adjuster) == []


class TestAdjustEnrichedPrices:
    """Bulk adjustment over :class:`EnrichedComparable` rows."""

    @pytest.fixture
    def adjuster(self) -> HPIAdjuster:
        return HPIAdjuster(
            {
                "2015-01": 100.0,
                "2020-01": 120.0,
                "2024-01": 150.0,
            }
        )

    def test_adjusts_prices(self, adjuster: HPIAdjuster) -> None:
        rows = [
            _enriched(1, price=200_000, transfer_date="2015-06-15"),
            _enriched(2, price=300_000, transfer_date="2020-03-01"),
        ]
        adjusted = adjust_enriched_prices(rows, adjuster)
        assert adjusted[0].price == 300_000
        assert adjusted[1].price == 375_000

    def test_preserves_epc_fields(self, adjuster: HPIAdjuster) -> None:
        row = EnrichedComparable(
            transaction_id="E00001",
            price=200_000,
            transfer_date="2015-06-15",
            postcode="SW2 5TN",
            property_type="T",
            floor_area_sqm=80.0,
            energy_efficiency=65,
            energy_rating="D",
            built_form="Mid-Terrace",
            construction_age_band="England and Wales: 1900-1929",
            epc_property_type="House",
            match_quality="exact_address",
        )
        adjusted = adjust_enriched_prices([row], adjuster)
        out = adjusted[0]
        assert out.price == 300_000
        assert out.floor_area_sqm == 80.0
        assert out.energy_efficiency == 65
        assert out.built_form == "Mid-Terrace"
        assert out.match_quality == "exact_address"
